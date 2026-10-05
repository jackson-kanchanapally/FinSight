import logging
import json
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.analyst.context_builder import build_context
from app.ai.analyst.fallback import complete_comparison_claims, verified_fallback
from app.ai.analyst.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    QUARTERLY_REPAIR_PROMPT,
    allowed_model_evidence_ids,
    evidence_guide,
    user_prompt,
)
from app.ai.analyst.schemas import GeneratedAnalystOutput, GeneratedQuarterlyReview
from app.ai.analyst.prompts import QUARTERLY_SYSTEM_PROMPT
from app.ai.analyst.validator import InvalidAnalysis, citation_feedback, validate_output
from app.ai.analyst.quarterly_packet import quarterly_packet
from app.ai.analyst.quarterly_validation import assess_review, repair_schema, repair_prompt, apply_repairs
from app.ai.providers.base import LLMError, LLMProvider
from app.ai.providers.ollama import OllamaProvider
from app.config import settings
from app.models.analysis import AIAnalysis
from app.models.company import Company
from app.models.financial import FinancialPeriod, FinancialStatus
from app.services.commentary import ingest_commentary

logger = logging.getLogger(__name__)


def model_schema(registry: dict, period_id: int, *, quarterly_only=False, allowed_ids=None) -> dict:
    schema = (GeneratedQuarterlyReview if quarterly_only else GeneratedAnalystOutput).model_json_schema()
    for name in ("quality_report", "investor_report", "review_trace"):
        schema["properties"].pop(name, None)
    schema["required"] = [
        name
        for name in schema.get("required", [])
        if name not in {"quality_report", "investor_report", "review_trace"}
    ]
    # Omit unused server-report definitions from the constrained model contract.
    definitions = schema.pop("$defs", {})
    needed = set()

    def references(value):
        if isinstance(value, dict):
            ref = value.get("$ref", "")
            if (
                ref.startswith("#/$defs/")
                and (name := ref.rsplit("/", 1)[-1]) not in needed
            ):
                needed.add(name)
                references(definitions[name])
            for child in value.values():
                references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)

    references(schema)
    schema["$defs"] = {
        name: value for name, value in definitions.items() if name in needed
    }
    valuation_ref = (
        schema["properties"].get("valuation_assessment", {}).get("$ref", "").rsplit("/", 1)[-1]
    )
    valuation = schema["$defs"].get(valuation_ref, {}).get("properties", {})
    if valuation:
        has_multiple = any(
            item["kind"] == "VALUATION"
            and item.get("metric_code")
            in {"PE_TTM", "PS_TTM", "EV_EBITDA_TTM", "PEG_FORWARD"}
            for item in registry.values()
        )
        valuation["status"]["enum"] = [
            "ASSESSED" if has_multiple else "INSUFFICIENT_DATA"
        ]
        if not has_multiple:
            valuation["verdict"]["enum"] = ["INCONCLUSIVE"]
    allowed = allowed_ids if allowed_ids is not None else allowed_model_evidence_ids(registry, period_id)

    def constrain(value):
        if isinstance(value, dict):
            for name, field in value.get("properties", {}).items():
                if name in {"evidence_ids", "summary_evidence_ids"}:
                    field["items"] = {"type": "string", "enum": allowed}
            for child in value.values():
                constrain(child)
        elif isinstance(value, list):
            for child in value:
                constrain(child)

    constrain(schema)
    if quarterly_only and "ReviewValuation" in schema.get("$defs", {}):
        schema["$defs"]["ReviewValuation"]["properties"]["status"]["enum"] = [registry["I_REVIEW"]["valuation_implication_status"]]
        # Model grammar must require citations and attribution, even though
        # defaults remain useful when reading older persisted server reports.
        for name in ("ReviewItem", "ReviewValuation"):
            definition = schema["$defs"][name]
            definition["required"] = ["text", "evidence_ids", "basis", "source_quote"] + (["status"] if name == "ReviewValuation" else [])
            definition["properties"]["evidence_ids"]["minItems"] = 1
            definition["properties"]["text"]["maxLength"] = 500
        sections = schema["$defs"]["QuarterlyInvestorReview"]["properties"]
        for name, maximum in (("quarterly_investor_view", 2), ("key_developments", 5),
            ("management_signals", 3), ("key_risks", 3), ("watch_next_quarter", 4)):
            sections[name]["maxItems"] = maximum
    return schema


def configured_provider() -> LLMProvider:
    return OllamaProvider(
        settings.ollama_base_url,
        settings.ollama_model,
        settings.quarterly_review_timeout_seconds,
        settings.ai_temperature,
        settings.ollama_num_ctx,
    )


def analyze_period(
    db: Session,
    company: Company,
    period: FinancialPeriod,
    provider: LLMProvider | None = None,
    regenerate: bool = False,
    force: bool = False,
    deterministic_only: bool = False,
) -> AIAnalysis:
    if period.company_id != company.id:
        raise ValueError("PERIOD_COMPANY_MISMATCH")
    if period.status != FinancialStatus.VALIDATED:
        raise ValueError("PERIOD_NOT_VALIDATED")
    provider = provider or configured_provider()
    db.refresh(period, ["metrics"])
    ingest_commentary(db, period)
    context = build_context(db, company.name, company.symbol, period)
    if not context.fact_count:
        raise ValueError("NO_VERIFIED_FACTS")
    if deterministic_only:
        existing = db.scalar(select(AIAnalysis).where(
            AIAnalysis.financial_period_id == period.id,
            AIAnalysis.model_provider == "deterministic",
            AIAnalysis.prompt_version == PROMPT_VERSION,
            AIAnalysis.context_hash == context.hash,
        ).order_by(AIAnalysis.id.desc()))
        if existing is not None and not force:
            from app.services.thesis_evaluation import refresh_after_review
            refresh_after_review(db, existing)
            return existing
        output = verified_fallback(context.registry, period, context.reasoning)
        record = AIAnalysis(
            company_id=company.id,
            financial_period_id=period.id,
            analysis_type="FINANCIAL_RESULTS",
            model_provider="deterministic",
            model_name="python",
            prompt_version=PROMPT_VERSION,
            context_hash=context.hash,
            context_chars=len(context.text),
            evidence_registry=context.registry,
            status="VERIFIED_FALLBACK",
            attempt_count=0,
            summary=context.investor_report["investor_conclusion"]["text"],
            structured_output={
                **output.model_dump(),
                "investor_report": context.investor_report,
            },
            validation_diagnostics=[],
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        from app.services.thesis_evaluation import refresh_after_review
        refresh_after_review(db, record)
        return record
    quarterly_contract = provider.supports_quarterly_review
    packet, packet_ids, retrieval_count = (
        quarterly_packet(context) if quarterly_contract else (context.text, [], 0))
    prompt_hash = (hashlib.sha256((context.hash + packet).encode()).hexdigest()
                   if quarterly_contract else context.hash)
    existing = db.scalar(
        select(AIAnalysis)
        .where(
            AIAnalysis.financial_period_id == period.id,
            AIAnalysis.status.in_(("SUCCESS", "VERIFIED_FALLBACK")),
            AIAnalysis.context_hash == prompt_hash,
            AIAnalysis.model_provider == provider.name,
            AIAnalysis.model_name == provider.model,
            AIAnalysis.prompt_version == PROMPT_VERSION,
        )
        .order_by(AIAnalysis.id.desc())
    )
    if existing:
        created = existing.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        recent_fallback = (
            existing.status == "VERIFIED_FALLBACK"
            and existing.failure_reason is not None
            and datetime.now(timezone.utc) - created < timedelta(minutes=15)
        )
        if not regenerate or recent_fallback and not force:
            from app.services.thesis_evaluation import refresh_after_review
            refresh_after_review(db, existing)
            return existing

    record = AIAnalysis(
        company_id=company.id,
        financial_period_id=period.id,
        analysis_type="FINANCIAL_RESULTS",
        model_provider=provider.name,
        model_name=provider.model,
        prompt_version=PROMPT_VERSION,
        context_hash=prompt_hash,
        context_chars=len(packet),
        evidence_registry=context.registry,
        status="PENDING",
        attempt_count=0,
        validation_diagnostics=[],
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    logger.info(
        "[ai] company=%s period=%s_%s scope=%s provider=%s model=%s facts=%s metrics=%s evidence_items=%s context_chars=%s",
        company.symbol,
        period.quarter or "FY",
        period.fiscal_year,
        period.scope.value,
        provider.name,
        provider.model,
        context.fact_count,
        context.metric_count,
        len(context.registry),
        len(packet),
    )
    schema = model_schema(context.registry, period.id, quarterly_only=quarterly_contract,
                          allowed_ids=packet_ids if quarterly_contract else None)
    guide = ("Cite only IDs present in the packet. I_REVIEW fixes assumption credibility; A_BASE holds required EPS CAGR."
             if quarterly_contract else evidence_guide(context.registry, period.id))
    feedback = None
    repair_attempted = False
    seen_invalid: set[tuple[str, str | None, str | None]] = set()
    for attempt in (1, 2):
        record.attempt_count = attempt
        is_quarterly = False
        try:
            raw = provider.generate(
                QUARTERLY_SYSTEM_PROMPT if quarterly_contract else SYSTEM_PROMPT,
                user_prompt(
                    packet, repair_feedback=feedback, citation_guide=guide,
                    quarterly_only=quarterly_contract,
                ),
                schema,
            )
            try:
                decoded = json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise InvalidAnalysis("INVALID_JSON_OR_SCHEMA") from exc
            if quarterly_contract:
                is_quarterly = True
                if not isinstance(decoded, dict) or not isinstance(decoded.get("quarterly_review"), dict):
                    raise InvalidAnalysis("INVALID_JSON_OR_SCHEMA", field="quarterly_review")
                raw_review = decoded["quarterly_review"]
                assessment = assess_review(raw_review, context.registry, period.period_end.year,
                    context.investor_report["quarterly_review"], packet_ids)
                record.validation_diagnostics = [
                    *(record.validation_diagnostics or []),
                    *({"attempt": attempt, **row} for row in assessment.diagnostics),
                ]
                first_rejected = len(assessment.rejected)
                if assessment.rejected:
                    repair_attempted = True
                    record.attempt_count = attempt + 1
                    repair_priority = {"quarterly_investor_view": 0, "valuation_implication": 1,
                                       "investor_conclusion": 2, "management_signals": 3,
                                       "key_developments": 4, "key_risks": 5,
                                       "watch_next_quarter": 6, "what_changed": 7}
                    repair_targets = sorted(assessment.rejected,
                        key=lambda row: (repair_priority.get(row["section"], 8), row["index"]))[:4]
                    try:
                        repaired_raw = provider.generate(
                            QUARTERLY_REPAIR_PROMPT,
                            repair_prompt(packet, raw_review, repair_targets, packet_ids),
                            repair_schema(repair_targets, packet_ids),
                        )
                        repaired = apply_repairs(raw_review, json.loads(repaired_raw), assessment.rejected)
                        assessment = assess_review(repaired, context.registry, period.period_end.year,
                            context.investor_report["quarterly_review"], packet_ids)
                        record.validation_diagnostics = [
                            *(record.validation_diagnostics or []),
                            *({"attempt": record.attempt_count, **row} for row in assessment.diagnostics),
                        ]
                    except (LLMError, ValueError, TypeError) as exc:
                        record.validation_diagnostics = [
                            *(record.validation_diagnostics or []),
                            {"attempt": record.attempt_count, "code": "REPAIR_UNAVAILABLE",
                             "reason": getattr(exc, "reason", "REPAIR_UNAVAILABLE"),
                             "field": "quarterly_review", "model_value": None, "expected": None},
                        ]
                if assessment.accepted_count < 2:
                    raise InvalidAnalysis("NO_VALID_REVIEW_ITEMS", field="quarterly_review")
                trace = {"prompt_context_chars": len(packet), "evidence_ids_supplied": packet_ids,
                    "retrieval_count": retrieval_count, "model_items_accepted": assessment.accepted_count,
                    "items_rejected_initially": first_rejected,
                    "items_rejected_finally": len(assessment.rejected),
                    "fallback_items": assessment.fallback_items,
                    "repair_attempted": repair_attempted,
                    "generation_status": ("PARTIAL_VALIDATED" if assessment.rejected else
                        "VALIDATED_AFTER_REPAIR" if repair_attempted else "VALIDATED"),
                    "fallback_reason": None}
                base_output = complete_comparison_claims(
                    verified_fallback(context.registry, period, context.reasoning),
                    context.registry, period, context.reasoning)
                output = base_output.model_copy(
                    update={"quarterly_review": assessment.review, "review_trace": trace})
            else:
                # Compatibility with existing stored/test provider contracts.
                output = validate_output(raw, context.registry, period.period_end.year,
                    require_extended=True, model_response=True)
                output = complete_comparison_claims(
                    output, context.registry, period, context.reasoning
                )
            record.status = "SUCCESS"
            record.summary = output.summary
            record.structured_output = {
                **output.model_dump(),
                "investor_report": context.investor_report,
            }
            record.failure_reason = None
            break
        except InvalidAnalysis as exc:
            record.validation_diagnostics = [
                *(record.validation_diagnostics or []),
                {"attempt": attempt, **exc.diagnostic()},
            ]
            signature = (exc.reason, exc.token, exc.field)
            if signature in seen_invalid:
                record.status = "INVALID"
                record.failure_reason = exc.reason
                logger.info(
                    "[ai] repeated_invalid_output reason=%s field=%s; using verified fallback",
                    exc.reason,
                    exc.field,
                )
                break
            seen_invalid.add(signature)
            logger.warning(
                "[ai] rejected_model_output reason=%s token=%s field=%s hint=%s",
                exc.reason,
                exc.token,
                exc.field,
                exc.hint,
            )
            token_label = (
                "unsupported numeric token"
                if exc.reason == "UNSUPPORTED_NUMBER"
                else "token"
            )
            feedback = exc.reason + (
                f" ({token_label}: {exc.token})" if exc.token else ""
            )
            if exc.field:
                feedback += f" in {exc.field}"
            if exc.hint:
                feedback += f". {exc.hint}"
            elif exc.expected:
                feedback += f". Expected: {str(exc.expected)[:800]}"
            elif exc.reason == "UNSUPPORTED_MARGIN_COMPARISON":
                feedback += (
                    ". A margin level is not a margin change. Keep margin levels separate from "
                    "revenue or PAT trends. Remove margin comparisons without their exact "
                    "margin-change metric; never infer a QoQ margin change from a YoY metric"
                )
            issues = [] if is_quarterly else citation_feedback(raw, context.registry, period.period_end.year)
            if len(issues) > 1:
                feedback += ". Also correct every field: " + "; ".join(issues)
            record.status = "INVALID"
            record.failure_reason = exc.reason
            if quarterly_contract and repair_attempted:
                break
        except LLMError as exc:
            record.validation_diagnostics = [
                *(record.validation_diagnostics or []),
                {
                    "attempt": attempt,
                    "code": exc.reason,
                    "field": "provider",
                    "reason": exc.reason,
                    "model_value": None,
                    "expected": "A schema-valid, evidence-supported model response",
                },
            ]
            record.status = "FAILED"
            record.failure_reason = exc.reason
            break
        except Exception:
            logger.exception("[ai] unexpected provider failure")
            record.status = "FAILED"
            record.failure_reason = "ANALYSIS_ERROR"
            break
    if record.status in {"INVALID", "FAILED"}:
        try:
            output = verified_fallback(context.registry, period, context.reasoning)
            if quarterly_contract:
                output = output.model_copy(update={"review_trace": {
                    "prompt_context_chars": len(packet), "evidence_ids_supplied": packet_ids,
                    "retrieval_count": retrieval_count, "model_items_accepted": 0,
                    "items_rejected_initially": len(record.validation_diagnostics or []),
                    "items_rejected_finally": len(record.validation_diagnostics or []),
                    "fallback_items": 0, "repair_attempted": repair_attempted,
                    "generation_status": "VERIFIED_FALLBACK", "fallback_reason": record.failure_reason}})
            record.status = "VERIFIED_FALLBACK"
            record.summary = output.summary
            record.structured_output = {
                **output.model_dump(),
                "investor_report": context.investor_report,
            }
        except Exception:
            logger.exception("[ai] verified fallback failed")
    db.commit()
    db.refresh(record)
    logger.info(
        "[ai] company=%s period_id=%s analysis_status=%s reason=%s",
        company.symbol,
        period.id,
        record.status,
        record.failure_reason,
    )
    if record.status in {"SUCCESS", "VERIFIED_FALLBACK"}:
        from app.services.thesis_evaluation import refresh_after_review
        refresh_after_review(db, record, provider=provider)
    return record
