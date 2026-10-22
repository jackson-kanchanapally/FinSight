"""Build a bounded, auditable context from validated database records only."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models.event import CompanyEvent, Document, DocumentPage
from app.models.financial import (
    FactStatus,
    FinancialFact,
    FinancialPeriod,
    FinancialStatus,
)
from app.models.financial import MetricCode, PeriodType, FinancialUnit
from app.models.commentary import ManagementStatement
from app.ai.analyst.reasoning import (
    compare_values,
    margin_change_units,
    prioritize_changes,
    seasonality_pattern,
)
from app.market.valuation import calculate_valuation
from app.market.service import latest_snapshot
from app.services.investor_evidence import extend_results_evidence
from app.services.investor_report import build_investor_report
from app.services.memory.analyst import historical_context
from app.services.materiality import select_material
from app.services.quarterly_review import statement_evidence

VERIFIED = {
    FactStatus.VERIFIED_PDF,
    FactStatus.VERIFIED_STRUCTURED,
    FactStatus.MANUALLY_VERIFIED,
}
PAGE_KEYWORDS = (
    "revenue from operations",
    "profit for the period",
    "profit after tax",
    "finance costs",
    "depreciation",
    "profit before tax",
    "basic (in",
)
MANAGEMENT_WORDS = re.compile(
    r"\b(?:management|director|ceo|cfo|chairman|guidance|outlook|expects)\b", re.I
)
UNIT_LABELS = {
    "INR_CRORE": "₹ crore",
    "INR_PER_SHARE": "₹ per share",
    "PERCENT": "percent",
    "BASIS_POINTS": "basis points",
}


@dataclass(frozen=True)
class AnalysisContext:
    text: str
    registry: dict[str, dict]
    hash: str
    fact_count: int
    metric_count: int
    reasoning: dict
    investor_report: dict


def build_context(
    db: Session,
    company_name: str,
    symbol: str,
    period: FinancialPeriod,
    max_chars: int | None = None,
    max_document_chars: int | None = None,
    include_memory: bool = True,
) -> AnalysisContext:
    if period.status != FinancialStatus.VALIDATED:
        raise ValueError("PERIOD_NOT_VALIDATED")
    max_chars = max_chars or settings.ai_max_context_chars
    max_document_chars = (
        settings.ai_max_document_chars
        if max_document_chars is None
        else max_document_chars
    )
    event = db.get(CompanyEvent, period.source_event_id)
    document = db.get(Document, period.source_document_id)
    if event is None or document is None:
        raise ValueError("SOURCE_MISSING")

    registry: dict[str, dict] = {}
    snapshot = latest_snapshot(db, period.company_id)
    snapshot_age = (
        (datetime.now(ZoneInfo("Asia/Kolkata")).date() - snapshot.as_of_date).days
        if snapshot
        else None
    )
    fresh_snapshot = (
        snapshot if snapshot_age is not None and 0 <= snapshot_age <= 7 else None
    )
    if fresh_snapshot:
        registry[f"Q{fresh_snapshot.id}"] = {
            "kind": "MARKET_SNAPSHOT",
            "price": str(fresh_snapshot.close_price),
            "pe_ratio": (
                str(fresh_snapshot.pe_ratio)
                if fresh_snapshot.pe_ratio is not None
                else None
            ),
            "market_cap_crore": (
                str(fresh_snapshot.market_cap_crore)
                if fresh_snapshot.market_cap_crore is not None
                else None
            ),
            "forward_eps_growth_pct": (
                str(fresh_snapshot.forward_eps_growth_pct)
                if fresh_snapshot.forward_eps_growth_pct is not None
                else None
            ),
            "growth_source_url": fresh_snapshot.growth_source_url,
            "peg_ratio": (
                str(fresh_snapshot.peg_ratio)
                if fresh_snapshot.peg_ratio is not None
                else None
            ),
            "as_of_date": fresh_snapshot.as_of_date.isoformat(),
            "source_url": fresh_snapshot.source_url,
            "entered_by_user": fresh_snapshot.data_origin == "MANUAL",
            "provider": fresh_snapshot.provider,
            "data_origin": fresh_snapshot.data_origin,
        }
    current_facts = list(
        db.scalars(
            select(FinancialFact)
            .where(
                FinancialFact.financial_period_id == period.id,
                FinancialFact.status.in_(VERIFIED),
                FinancialFact.source_column_date == period.period_end,
            )
            .order_by(FinancialFact.id)
        )
    )
    current_facts = [fact for fact in current_facts if fact.unit == (FinancialUnit.INR_PER_SHARE
        if fact.metric_code in {MetricCode.BASIC_EPS, MetricCode.DILUTED_EPS} else FinancialUnit.INR_CRORE)]
    for fact in current_facts:
        registry[f"F{fact.id}"] = _fact_item(fact, period)

    valuation_items = []
    if fresh_snapshot:
        _, calculated = calculate_valuation(db, fresh_snapshot, period.scope)
        for code in (
            "ttm_revenue",
            "ttm_pat",
            "ttm_ebitda",
            "ttm_eps",
            "ttm_operating_cash_flow",
            "ttm_free_cash_flow",
            "pe_ttm",
            "ps_ttm",
            "ev_ebitda_ttm",
            "earnings_yield_pct",
            "peg_forward",
        ):
            detail = calculated[code]
            if detail["value"] is None:
                continue
            evidence_id = f"V{fresh_snapshot.id}_{code.upper()}"
            item = {
                "kind": "VALUATION",
                "metric_code": code.upper(),
                "value": detail["value"],
                "status": detail["status"],
                "formula": detail["formula"],
                "as_of_date": fresh_snapshot.as_of_date.isoformat(),
                "scope": period.scope.value,
                "input_evidence_ids": detail["inputs"],
                "source_url": fresh_snapshot.source_url,
            }
            registry[evidence_id] = item
            valuation_items.append({"evidence_id": evidence_id, **item})

    metric_items = []
    for metric in sorted(period.metrics, key=lambda item: item.id):
        inputs = (
            list(
                db.scalars(
                    select(FinancialFact).where(
                        FinancialFact.id.in_(metric.input_fact_ids)
                    )
                )
            )
            if metric.input_fact_ids
            else []
        )
        input_by_id = {fact.id: fact for fact in inputs}
        if not metric.input_fact_ids or len(input_by_id) != len(metric.input_fact_ids):
            continue
        source_periods = {fact.financial_period_id for fact in inputs}
        owners = {source_id: db.get(FinancialPeriod, source_id) for source_id in source_periods}
        allowed_period_ids = {period.id, metric.comparison_period_id} - {None}
        valid_sources = source_periods == allowed_period_ids and all(
            owner is not None
            and owner.id in allowed_period_ids
            and owner.status == FinancialStatus.VALIDATED
            and owner.company_id == period.company_id
            and owner.scope == period.scope
            and owner.period_type == period.period_type
            for owner in owners.values()
        )
        if metric.comparison_period_id:
            from app.services.financials.period import comparable_period
            prior = owners.get(metric.comparison_period_id)
            basis = "YOY" if metric.metric_code.value.endswith("YOY") or metric.metric_code == MetricCode.EBITDA_MARGIN_CHANGE_BPS else "QOQ"
            valid_sources = valid_sources and prior is not None and comparable_period(period, prior, basis)
        valid_sources = valid_sources and all(f.source_column_date == owners[f.financial_period_id].period_end for f in inputs if owners.get(f.financial_period_id))
        if not valid_sources or any(fact.status not in VERIFIED for fact in inputs):
            continue
        for fact in inputs:
            key = f"F{fact.id}"
            if key not in registry:
                registry[key] = _fact_item(fact, owners[fact.financial_period_id])
        entry = {
            "kind": "METRIC",
            "metric_code": metric.metric_code.value,
            "value": str(metric.value),
            "unit": metric.unit.value,
            "period_id": period.id,
            "scope": period.scope.value,
            "comparison_period_id": metric.comparison_period_id,
            "input_evidence_ids": [f"F{fact_id}" for fact_id in metric.input_fact_ids],
        }
        from app.schemas.financial import metric_formula
        entry["formula"] = metric_formula(metric.metric_code, inputs)
        if metric.metric_code in {MetricCode.EBITDA, MetricCode.EBITDA_MARGIN}:
            entry["definition"] = "Reported EBITDA basis" if any(f.metric_code == MetricCode.EBITDA for f in inputs) else "Calculated PBT-based proxy; includes other income and exceptional items"
        registry[f"M{metric.id}"] = entry
        metric_items.append(entry)

    earnings_quality = extend_results_evidence(db, period, registry)
    reasoning = _reasoning_context(db, period, registry, fresh_snapshot)

    all_statements = list(
        db.scalars(
            select(ManagementStatement)
            .where(ManagementStatement.financial_period_id == period.id)
            .order_by(
                ManagementStatement.published_at.desc(), ManagementStatement.id.desc()
            )
        )
    )
    for statement, category in select_material(all_statements):
        registry[statement.evidence_id] = statement_evidence(statement, category)

    pages = list(
        db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == document.id)
            .order_by(DocumentPage.page_number)
        )
    )
    fact_pages = {
        fact.source_page for fact in current_facts if fact.source_page is not None
    }
    excerpts = []
    remaining = max_document_chars
    for page in sorted(
        pages,
        key=lambda item: (
            not bool(MANAGEMENT_WORDS.search(item.text)),
            item.page_number,
        ),
    ):
        if remaining <= 0:
            continue
        relevant = [
            line.strip()
            for line in page.text.splitlines()
            if (
                page.page_number in fact_pages
                and any(word in line.lower() for word in PAGE_KEYWORDS)
            )
            or MANAGEMENT_WORDS.search(line)
        ]
        # A page can also contain NEEDS_REVIEW comparison columns. Keep the table
        # label as a locator, but do not let raw OCR/PDF numbers bypass fact review.
        excerpt = re.sub(
            r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?",
            "[number omitted]",
            "\n".join(relevant),
        )[: min(remaining, 900)]
        if not excerpt:
            continue
        evidence_id = f"D{document.id}P{page.page_number}"
        registry[evidence_id] = {
            "kind": "DOCUMENT_PAGE",
            "document_id": document.id,
            "page": page.page_number,
            "source_title": event.title,
            "document_url": f"/api/documents/{document.id}/file#page={page.page_number}",
            "excerpt": excerpt,
        }
        excerpts.append(
            {
                "evidence_id": evidence_id,
                "excerpt": excerpt,
                "note": "Page locator only; numbers omitted. Use verified facts for values.",
            }
        )
        remaining -= len(excerpt)

    report = build_investor_report(db, period, registry, earnings_quality)
    assessment = report["tested_pe"]
    referenced = {
        key for item in registry.values() for key in item.get("input_evidence_ids", [])
    }
    referenced.update(
        key
        for item in assessment["growth_comparisons"]
        for key in item.get("evidence_ids", [])
    )
    for key in referenced - registry.keys():
        if re.fullmatch(r"F\d+", key):
            fact = db.get(FinancialFact, int(key[1:]))
            owner = db.get(FinancialPeriod, fact.financial_period_id) if fact else None
            if (
                fact
                and fact.status in VERIFIED
                and owner.status == FinancialStatus.VALIDATED
                and owner.company_id == period.company_id
                and owner.scope == period.scope
            ):
                registry[key] = _fact_item(fact, owner)
    memory = historical_context(db, period, event, registry, report) if include_memory else {
        "status": "STORED_EVIDENCE_ONLY", "passages": []}
    registry["I_REVIEW"] = {"kind": "REVIEW_CONTEXT",
        "valuation_implication_status": report["review_packet"]["valuation_implication_status"],
        "changes": report["review_packet"]["changes"], "history_note": report["history_note"],
        "research_gaps": report["research_gaps"]}
    payload = {
        "company_memory": memory,
        "company": company_name,
        "symbol": symbol,
        "period": {
            "id": period.id,
            "fiscal_year": period.fiscal_year,
            "quarter": period.quarter,
            "end": period.period_end.isoformat(),
            "type": period.period_type.value,
            "scope": period.scope.value,
            "status": period.status.value,
        },
        "source": {
            "title": event.title,
            "origin": event.data_origin.value,
            "document_id": document.id,
            "sha256": document.sha256_hash,
            "published_at": event.published_at.isoformat(),
        },
        "verified_facts": [
            {"evidence_id": f"F{fact.id}", **_prompt_evidence(registry[f"F{fact.id}"])}
            for fact in current_facts
        ],
        "deterministic_metrics": [
            {"evidence_id": key, **_prompt_evidence(item)}
            for key, item in registry.items()
            if item["kind"] == "METRIC"
        ],
        "comparison_facts": [
            {"evidence_id": key, **_prompt_evidence(item)}
            for key, item in registry.items()
            if key.startswith("F") and item["period_id"] != period.id
        ],
        "source_excerpts": excerpts,
        "official_management_commentary": [],
        "investor_review_packet": json.loads(json.dumps(report["review_packet"])),
        "market_snapshot": (
            {
                "evidence_id": f"Q{fresh_snapshot.id}",
                **registry[f"Q{fresh_snapshot.id}"],
            }
            if fresh_snapshot
            else None
        ),
        "calculated_valuation": valuation_items,
        "investor_reasoning": reasoning,
        "results_signals": report["results"],
        "earnings_quality": earnings_quality,
        "source_verification": report["source_verification"],
        "result_freshness": report["result_freshness"],
        "evidence_confidence": report["evidence_confidence"],
        "previous_thesis": report["previous_thesis"],
        "growth_comparisons": assessment["growth_comparisons"],
        "historical_valuation": assessment["historical_pe"],
        "reverse_expectations": assessment["reverse_expectations"],
        "tested_pe_range": {
            "range": assessment["supported_pe_range"],
            "scenarios": assessment["scenarios"],
            "label": assessment["label"],
        },
        "valuation_relationship": {
            "verdict": report["valuation"]["verdict"],
            "gap": assessment["expectations_gap"],
        },
        "guidance_execution": report["management_guidance"],
        "management_execution_history": [
            {key: row[key] for key in (
                "evidence_id", "metric", "target_period", "lower_bound",
                "upper_bound", "unit", "scope", "published_at", "change",
                "actual_value", "execution_outcome",
            )}
            for row in report["management_execution_history"]
        ],
        "commentary_availability": report["commentary_availability"],
        "research_gaps": report["research_gaps"],
        "market_data_note": (
            "Use CALCULATED_VALUATION IDs for authoritative ratios. The quote's P/E and legacy PEG "
            "are third-party or user-entered references only. Forward growth is an assumption, not a result. "
            "The quote is dated; compare its date with today."
            if fresh_snapshot
            else "No fresh sourced price and valuation observation is available."
        ),
        "data_quality": "Only VALIDATED periods and verified facts are included. PDF excerpt numbers are omitted; other periods may need review.",
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        # The structured facts and metrics are essential; trim document excerpts first.
        payload["source_excerpts"] = []
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        payload["official_management_commentary"] = []
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        payload["calculated_valuation"] = [
            item
            for item in valuation_items
            if item["metric_code"] in {"PE_TTM", "PEG_FORWARD", "PS_TTM"}
        ]
        payload["investor_reasoning"] = reasoning
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        # Historical transition detail is useful, but the current verified result takes priority.
        for pattern in reasoning["seasonality"].values():
            pattern["historical_transitions"] = []
        payload["investor_reasoning"] = reasoning
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        # Comparison values are available through the cited metric input IDs.
        # Keep the full registry for validation and source-page drill-down.
        payload["comparison_facts"] = []
        payload["tested_pe_range"]["scenarios"] = [
            {
                key: row[key]
                for key in (
                    "name",
                    "evidence_id",
                    "eps_growth_pct",
                    "terminal_pe",
                    "years",
                    "required_return_pct",
                    "source_type",
                    "implied_current_pe",
                    "evidence_ids",
                )
                if key in row
            }
            for row in assessment["scenarios"]
        ]
        payload["tested_pe_range"]["assumption_sources"] = list(
            dict.fromkeys(row["source"] for row in assessment["scenarios"])
        )
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        for section in (
            "deterministic_metrics",
            "verified_facts",
            "calculated_valuation",
        ):
            payload[section] = [
                {
                    key: value
                    for key, value in item.items()
                    if key
                    in {
                        "evidence_id",
                        "metric_code",
                        "value",
                        "unit",
                        "input_evidence_ids",
                    }
                }
                for item in payload[section]
            ]
        payload["historical_valuation"] = {
            key: value
            for key, value in (assessment["historical_pe"] or {}).items()
            if key in {"one_year", "three_year", "available_points"}
        }
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        # These explanations already live in the system prompt / rendered report.
        payload.pop("market_data_note", None)
        payload["research_gaps"] = payload["research_gaps"][:3]
        payload["growth_comparisons"] = []
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        # Build the compact investor packet from the same authoritative inputs.
        # The full registry and deterministic report remain available for validation.
        payload.pop("growth_comparisons", None)
        payload.pop("historical_valuation", None)
        payload.pop("source_excerpts", None)
        payload.pop("comparison_facts", None)
        payload.pop("official_management_commentary", None)
        payload.pop("research_gaps", None)
        payload.pop("guidance_execution", None)
        payload.pop("management_execution_history", None)
        payload.pop("market_data_note", None)
        payload["investor_reasoning"] = {k: reasoning[k] for k in (
            "relationships", "seasonality", "materiality") if k in reasoning}
        payload["earnings_quality"] = {k: earnings_quality[k] for k in (
            "text", "evidence_ids", "signals", "basis") if k in earnings_quality}
        payload["results_signals"] = {k: report["results"][k] for k in ("verdict", "reasons", "evidence_ids")}
        packet = payload["investor_review_packet"]
        # Locators and calculation lineage stay in the server evidence registry;
        # repeating them beside every value displaces useful historical context.
        for section in ("verified_facts", "deterministic_metrics", "calculated_valuation"):
            for item in payload[section]:
                item.pop("input_evidence_ids", None)
        payload["source"] = {k: v for k, v in payload["source"].items() if k in {"published_at", "document_id", "origin"}}
        packet["checkpoints"] = [{k: c[k] for k in ("evidence_id", "source_statement_id", "metric", "target_period", "target_date", "target", "unit", "status", "is_previous", "measurement_note", "actual_outcome")} for c in packet["checkpoints"]]
        for checkpoint in packet["checkpoints"]:
            checkpoint["actual_outcome"] = {k: v for k, v in checkpoint["actual_outcome"].items()
                if k in {"actual_value", "evidence_ids", "change", "revised_target"} and v is not None and v != []}
        for statement in packet["material_statements"]:
            statement.pop("speaker", None)
            statement.pop("page", None)
            if len(statement["source_quote"]) > 260:
                statement["source_quote"] = statement["source_quote"][:260].rsplit(" ", 1)[0]
        payload["deterministic_metrics"] = [m for m in payload["deterministic_metrics"] if m["metric_code"] in {
            "REVENUE_YOY", "REVENUE_QOQ", "PAT_YOY", "PAT_QOQ", "EBITDA_MARGIN", "PAT_MARGIN", "EBITDA_MARGIN_CHANGE_BPS"}]
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        # Scenario assumptions are supplementary to an earnings review. Keep
        # their qualified conclusion while retaining every scenario and its
        # lineage in the full report/registry, rather than displacing current
        # financial evidence, confidence or material management checkpoints.
        payload["tested_pe_range"] = {
            "range": assessment["supported_pe_range"],
            "label": assessment["label"],
            "detail_note": "Scenario assumptions omitted from this bounded prompt; see the full report. Do not reconstruct them or treat them as verified forecasts.",
        }
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    while len(text) > max_chars and memory["passages"]:
        removed = memory["passages"].pop()
        registry.pop(removed["evidence_id"], None)
        memory["conflicts"] = [item for item in memory["conflicts"] if item["evidence_id"] != removed["evidence_id"]]
        retained_guidance = []
        for item in memory["structured_guidance"]:
            if any(p["document_id"] == item["document_id"] and p["page"] == item["page"] for p in memory["passages"]):
                retained_guidance.append(item)
            else:
                if item["evidence_id"] not in {
                    row["evidence_id"] for row in report["management_execution_history"]
                }:
                    registry.pop(item["evidence_id"], None)
        memory["structured_guidance"] = retained_guidance
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    while len(text) > max_chars and len(payload["investor_review_packet"]["material_statements"]) > 7:
        payload["investor_review_packet"]["material_statements"].pop()
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        raise ValueError("CONTEXT_LIMIT_EXCEEDED")
    return AnalysisContext(
        text=text,
        registry=registry,
        hash=hashlib.sha256(
            (text + json.dumps(report, sort_keys=True)).encode()
        ).hexdigest(),
        fact_count=len(current_facts),
        metric_count=len(metric_items),
        reasoning=reasoning,
        investor_report=report,
    )


def _fact_item(fact: FinancialFact, period: FinancialPeriod) -> dict:
    return {
        "kind": "FACT",
        "fact_id": fact.id,
        "metric_code": fact.metric_code.value,
        "value": str(fact.value),
        "unit": fact.unit.value,
        "period_id": period.id,
        "period_end": period.period_end.isoformat(),
        "scope": period.scope.value,
        "status": fact.status.value,
        "document_id": fact.source_document_id,
        "page": fact.source_page,
        "source_label": fact.source_label,
        "extraction_method": fact.extraction_method.value,
        "rounding_tolerance": str(fact.rounding_tolerance) if fact.rounding_tolerance is not None else None,
        "document_url": f"/api/documents/{fact.source_document_id}/file"
        + (f"#page={fact.source_page}" if fact.source_page else ""),
    }


def _prompt_evidence(item: dict) -> dict:
    """The model needs the value and provenance IDs, not repeated database metadata."""
    keys = (
        "kind",
        "metric_code",
        "value",
        "unit",
        "period_end",
        "scope",
        "comparison_period_id",
        "input_evidence_ids",
    )
    result = {key: item[key] for key in keys if key in item}
    if "unit" in result:
        result["unit"] = UNIT_LABELS.get(result["unit"], result["unit"])
    return result


def _reasoning_context(
    db: Session, period: FinancialPeriod, registry: dict[str, dict], snapshot
) -> dict:
    metrics = {
        item["metric_code"]: (key, Decimal(item["value"]))
        for key, item in registry.items()
        if item["kind"] == "METRIC"
    }
    valuations = {
        item["metric_code"]: (key, Decimal(item["value"]))
        for key, item in registry.items()
        if item["kind"] == "VALUATION"
    }
    relationships = {}
    if snapshot and "PE_TTM" in valuations and snapshot.pe_ratio is not None:
        pe_id, pe = valuations["PE_TTM"]
        result = compare_values(pe, snapshot.pe_ratio)
        result.update(
            metric="PE_TTM",
            role="CALCULATION_CROSS_CHECK",
            reference_status=(
                "MATERIAL_DISCREPANCY"
                if result["percentage_difference"] is not None
                and abs(Decimal(result["percentage_difference"])) > Decimal(5)
                else "WITHIN_TOLERANCE"
            ),
            input_evidence_ids=[pe_id, f"Q{snapshot.id}"],
        )
        registry["R_PE"] = {
            "kind": "RELATIONSHIP",
            "metric_code": "PE_REFERENCE_CROSSCHECK",
            **result,
        }
        relationships["pe_reference"] = {"evidence_id": "R_PE", **result}
    if "PAT_YOY" in metrics and "REVENUE_YOY" in metrics:
        pat_id, pat = metrics["PAT_YOY"]
        revenue_id, revenue = metrics["REVENUE_YOY"]
        result = compare_values(pat, revenue)
        result.update(
            metric="PAT_YOY_VS_REVENUE_YOY",
            unit="PERCENTAGE_POINTS",
            input_evidence_ids=[pat_id, revenue_id],
        )
        registry["R_GROWTH"] = {
            "kind": "RELATIONSHIP",
            "metric_code": "PAT_YOY_VS_REVENUE_YOY",
            **result,
        }
        relationships["pat_vs_revenue_growth"] = {"evidence_id": "R_GROWTH", **result}
    if "EBITDA_MARGIN_CHANGE_BPS" in metrics and "EBITDA_MARGIN" in metrics:
        change_id, bps = metrics["EBITDA_MARGIN_CHANGE_BPS"]
        current_id, current = metrics["EBITDA_MARGIN"]
        margin = margin_change_units(current, current - bps / Decimal(100))
        margin["input_evidence_ids"] = [change_id, current_id]
        registry["R_MARGIN"] = {
            "kind": "RELATIONSHIP",
            "metric_code": "EBITDA_MARGIN_CHANGE",
            **margin,
        }
        relationships["ebitda_margin_change"] = {"evidence_id": "R_MARGIN", **margin}
    if "TTM_OPERATING_CASH_FLOW" in valuations and "TTM_PAT" in valuations:
        cash_id, cash = valuations["TTM_OPERATING_CASH_FLOW"]
        pat_id, pat = valuations["TTM_PAT"]
        result = compare_values(cash, pat)
        result.update(
            metric="CASH_FLOW_VS_PAT",
            unit="INR_CRORE",
            input_evidence_ids=[cash_id, pat_id],
        )
        registry["R_CASH"] = {
            "kind": "RELATIONSHIP",
            "metric_code": "CASH_FLOW_VS_PAT",
            **result,
        }
        relationships["cash_vs_pat"] = {"evidence_id": "R_CASH", **result}

    seasonality = {}
    if period.period_type == PeriodType.QUARTERLY:
        periods = list(
            db.scalars(
                select(FinancialPeriod)
                .options(selectinload(FinancialPeriod.facts))
                .where(
                    FinancialPeriod.company_id == period.company_id,
                    FinancialPeriod.scope == period.scope,
                    FinancialPeriod.period_type == PeriodType.QUARTERLY,
                    FinancialPeriod.status == FinancialStatus.VALIDATED,
                    FinancialPeriod.period_end < period.period_end,
                )
                .order_by(FinancialPeriod.period_end.desc())
                .limit(32)
            )
        )
        by_end = {item.period_end: item for item in periods}
        for subject, fact_code in (
            ("REVENUE", MetricCode.REVENUE_FROM_OPERATIONS),
            ("PAT", MetricCode.PAT),
        ):
            qoq_code = f"{subject}_QOQ"
            historical = []
            for current in periods:
                if current.quarter != period.quarter:
                    continue
                prior = by_end.get(current.period_start - timedelta(days=1))
                if prior is None:
                    continue
                current_fact = next(
                    (
                        fact
                        for fact in current.facts
                        if fact.metric_code == fact_code and fact.status in VERIFIED
                    ),
                    None,
                )
                prior_fact = next(
                    (
                        fact
                        for fact in prior.facts
                        if fact.metric_code == fact_code
                        and fact.status in VERIFIED
                        and fact.value != 0
                    ),
                    None,
                )
                if current_fact is None or prior_fact is None:
                    continue
                change = (
                    (current_fact.value - prior_fact.value)
                    / abs(prior_fact.value)
                    * Decimal(100)
                )
                for fact, source_period in (
                    (current_fact, current),
                    (prior_fact, prior),
                ):
                    registry.setdefault(f"F{fact.id}", _fact_item(fact, source_period))
                historical.append(
                    {
                        "current_period_end": current.period_end.isoformat(),
                        "previous_period_end": prior.period_end.isoformat(),
                        "change_pct": str(change.quantize(Decimal("0.000001"))),
                        "input_evidence_ids": [
                            f"F{current_fact.id}",
                            f"F{prior_fact.id}",
                        ],
                    }
                )
                if len(historical) == 5:
                    break
            current_change = metrics.get(qoq_code)
            pattern = seasonality_pattern(
                current_change[1] if current_change else None, historical
            )
            inputs = ([current_change[0]] if current_change else []) + [
                item
                for transition in historical
                for item in transition["input_evidence_ids"]
            ]
            pattern.update(metric_code=qoq_code, input_evidence_ids=inputs)
            evidence_id = f"H_{subject}"
            registry[evidence_id] = {"kind": "SEASONALITY", **pattern}
            seasonality[subject.lower()] = {"evidence_id": evidence_id, **pattern}

    return {
        "valuation": {
            "price": str(snapshot.close_price) if snapshot else None,
            "price_date": snapshot.as_of_date.isoformat() if snapshot else None,
            "pe_ttm": str(valuations["PE_TTM"][1]) if "PE_TTM" in valuations else None,
            "ps_ttm": str(valuations["PS_TTM"][1]) if "PS_TTM" in valuations else None,
            "ev_ebitda_ttm": (
                str(valuations["EV_EBITDA_TTM"][1])
                if "EV_EBITDA_TTM" in valuations
                else None
            ),
            "peg_forward": (
                str(valuations["PEG_FORWARD"][1])
                if "PEG_FORWARD" in valuations
                else None
            ),
            "forward_eps_growth_pct": (
                str(snapshot.forward_eps_growth_pct)
                if snapshot and snapshot.forward_eps_growth_pct is not None
                else None
            ),
            "forward_growth_source_type": (
                snapshot.growth_source_type if snapshot else None
            ),
        },
        "relationships": relationships,
        "seasonality": seasonality,
        "materiality_order": prioritize_changes(
            {code: value for code, (_, value) in metrics.items()}
        ),
    }
