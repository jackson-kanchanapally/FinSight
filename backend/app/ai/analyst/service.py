import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai.analyst.context_builder import build_context
from app.ai.analyst.fallback import complete_comparison_claims, verified_fallback
from app.ai.analyst.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    allowed_model_evidence_ids,
    evidence_guide,
    user_prompt,
)
from app.ai.analyst.schemas import GeneratedAnalystOutput
from app.ai.analyst.validator import InvalidAnalysis, citation_feedback, validate_output
from app.ai.providers.base import LLMError, LLMProvider
from app.ai.providers.ollama import OllamaProvider
from app.config import settings
from app.models.analysis import AIAnalysis
from app.models.company import Company
from app.models.financial import FinancialPeriod, FinancialStatus
from app.services.commentary import ingest_commentary

logger = logging.getLogger(__name__)


def model_schema(registry: dict, period_id: int) -> dict:
    schema = GeneratedAnalystOutput.model_json_schema()
    for name in ("quality_report", "investor_report"):
        schema["properties"].pop(name, None)
    schema["required"] = [
        name
        for name in schema.get("required", [])
        if name not in {"quality_report", "investor_report"}
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
        schema["properties"]["valuation_assessment"].get("$ref", "").rsplit("/", 1)[-1]
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
    allowed = allowed_model_evidence_ids(registry, period_id)

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
    return schema


def configured_provider() -> LLMProvider:
    return OllamaProvider(
        settings.ollama_base_url,
        settings.ollama_model,
        settings.ollama_timeout_seconds,
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
        return record
    existing = db.scalar(
        select(AIAnalysis)
        .where(
            AIAnalysis.financial_period_id == period.id,
            AIAnalysis.status.in_(("SUCCESS", "VERIFIED_FALLBACK")),
            AIAnalysis.context_hash == context.hash,
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
            return existing

    record = AIAnalysis(
        company_id=company.id,
        financial_period_id=period.id,
        analysis_type="FINANCIAL_RESULTS",
        model_provider=provider.name,
        model_name=provider.model,
        prompt_version=PROMPT_VERSION,
        context_hash=context.hash,
        context_chars=len(context.text),
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
        len(context.text),
    )
    schema = model_schema(context.registry, period.id)
    guide = evidence_guide(context.registry, period.id)
    feedback = None
    seen_invalid: set[tuple[str, str | None, str | None]] = set()
    for attempt in (1, 2):
        record.attempt_count = attempt
        try:
            raw = provider.generate(
                SYSTEM_PROMPT,
                user_prompt(
                    context.text, repair_feedback=feedback, citation_guide=guide
                ),
                schema,
            )
            output = validate_output(
                raw,
                context.registry,
                period.period_end.year,
                require_extended=True,
                model_response=True,
            )
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
            issues = citation_feedback(raw, context.registry, period.period_end.year)
            if len(issues) > 1:
                feedback += ". Also correct every field: " + "; ".join(issues)
            record.status = "INVALID"
            record.failure_reason = exc.reason
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
    return record
