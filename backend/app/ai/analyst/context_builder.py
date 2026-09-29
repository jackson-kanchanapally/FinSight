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
from app.models.financial import (FactStatus, FinancialFact, FinancialPeriod, FinancialStatus)
from app.models.financial import MetricCode, PeriodType
from app.ai.analyst.reasoning import compare_values, margin_change_units, prioritize_changes, seasonality_pattern
from app.market.valuation import calculate_valuation
from app.market.service import latest_snapshot

VERIFIED = {FactStatus.VERIFIED_PDF, FactStatus.VERIFIED_STRUCTURED, FactStatus.MANUALLY_VERIFIED}
PAGE_KEYWORDS = ("revenue from operations", "profit for the period", "profit after tax",
                 "finance costs", "depreciation", "profit before tax", "basic (in")
MANAGEMENT_WORDS = re.compile(r"\b(?:management|director|ceo|cfo|chairman|guidance|outlook|expects)\b", re.I)
UNIT_LABELS = {"INR_CRORE": "₹ crore", "INR_PER_SHARE": "₹ per share",
               "PERCENT": "percent", "BASIS_POINTS": "basis points"}


@dataclass(frozen=True)
class AnalysisContext:
    text: str
    registry: dict[str, dict]
    hash: str
    fact_count: int
    metric_count: int
    reasoning: dict


def build_context(db: Session, company_name: str, symbol: str, period: FinancialPeriod,
                  max_chars: int | None = None, max_document_chars: int | None = None) -> AnalysisContext:
    if period.status != FinancialStatus.VALIDATED:
        raise ValueError("PERIOD_NOT_VALIDATED")
    max_chars = max_chars or settings.ai_max_context_chars
    max_document_chars = settings.ai_max_document_chars if max_document_chars is None else max_document_chars
    event = db.get(CompanyEvent, period.source_event_id)
    document = db.get(Document, period.source_document_id)
    if event is None or document is None:
        raise ValueError("SOURCE_MISSING")

    registry: dict[str, dict] = {}
    snapshot = latest_snapshot(db, period.company_id)
    snapshot_age = (datetime.now(ZoneInfo("Asia/Kolkata")).date() - snapshot.as_of_date).days if snapshot else None
    fresh_snapshot = snapshot if snapshot_age is not None and 0 <= snapshot_age <= 7 else None
    if fresh_snapshot:
        registry[f"Q{fresh_snapshot.id}"] = {
            "kind": "MARKET_SNAPSHOT", "price": str(fresh_snapshot.close_price),
            "pe_ratio": str(fresh_snapshot.pe_ratio) if fresh_snapshot.pe_ratio is not None else None,
            "market_cap_crore": str(fresh_snapshot.market_cap_crore) if fresh_snapshot.market_cap_crore is not None else None,
            "forward_eps_growth_pct": (str(fresh_snapshot.forward_eps_growth_pct)
                                       if fresh_snapshot.forward_eps_growth_pct is not None else None),
            "growth_source_url": fresh_snapshot.growth_source_url,
            "peg_ratio": str(fresh_snapshot.peg_ratio) if fresh_snapshot.peg_ratio is not None else None,
            "as_of_date": fresh_snapshot.as_of_date.isoformat(), "source_url": fresh_snapshot.source_url,
            "entered_by_user": fresh_snapshot.data_origin == "MANUAL",
            "provider": fresh_snapshot.provider, "data_origin": fresh_snapshot.data_origin,
        }
    current_facts = list(db.scalars(select(FinancialFact).where(
        FinancialFact.financial_period_id == period.id,
        FinancialFact.status.in_(VERIFIED)).order_by(FinancialFact.id)))
    for fact in current_facts:
        registry[f"F{fact.id}"] = _fact_item(fact, period)

    valuation_items = []
    if fresh_snapshot:
        _, calculated = calculate_valuation(db, fresh_snapshot, period.scope)
        for code in ("ttm_revenue", "ttm_pat", "ttm_ebitda", "ttm_eps",
                     "ttm_operating_cash_flow", "ttm_free_cash_flow", "pe_ttm",
                     "ps_ttm", "ev_ebitda_ttm", "earnings_yield_pct", "peg_forward"):
            detail = calculated[code]
            if detail["value"] is None:
                continue
            evidence_id = f"V{fresh_snapshot.id}_{code.upper()}"
            item = {"kind": "VALUATION", "metric_code": code.upper(),
                    "value": detail["value"], "status": detail["status"],
                    "formula": detail["formula"], "as_of_date": fresh_snapshot.as_of_date.isoformat(),
                    "scope": period.scope.value, "input_evidence_ids": detail["inputs"],
                    "source_url": fresh_snapshot.source_url}
            registry[evidence_id] = item
            valuation_items.append({"evidence_id": evidence_id, **item})

    metric_items = []
    for metric in sorted(period.metrics, key=lambda item: item.id):
        inputs = list(db.scalars(select(FinancialFact).where(
            FinancialFact.id.in_(metric.input_fact_ids)))) if metric.input_fact_ids else []
        input_by_id = {fact.id: fact for fact in inputs}
        if len(input_by_id) != len(metric.input_fact_ids):
            continue
        source_periods = {fact.financial_period_id for fact in inputs}
        valid_sources = all(db.get(FinancialPeriod, source_id).status == FinancialStatus.VALIDATED
                            for source_id in source_periods)
        if not valid_sources or any(fact.status not in VERIFIED for fact in inputs):
            continue
        for fact in inputs:
            key = f"F{fact.id}"
            if key not in registry:
                source_period = db.get(FinancialPeriod, fact.financial_period_id)
                registry[key] = _fact_item(fact, source_period)
        entry = {"kind": "METRIC", "metric_code": metric.metric_code.value,
                 "value": str(metric.value), "unit": metric.unit.value,
                 "period_id": period.id, "scope": period.scope.value,
                 "comparison_period_id": metric.comparison_period_id,
                 "input_evidence_ids": [f"F{fact_id}" for fact_id in metric.input_fact_ids]}
        registry[f"M{metric.id}"] = entry
        metric_items.append(entry)

    reasoning = _reasoning_context(db, period, registry, fresh_snapshot)

    pages = list(db.scalars(select(DocumentPage).where(DocumentPage.document_id == document.id)
                            .order_by(DocumentPage.page_number)))
    fact_pages = {fact.source_page for fact in current_facts if fact.source_page is not None}
    excerpts = []
    remaining = max_document_chars
    for page in sorted(pages, key=lambda item: (not bool(MANAGEMENT_WORDS.search(item.text)), item.page_number)):
        if remaining <= 0:
            continue
        relevant = [line.strip() for line in page.text.splitlines()
                    if (page.page_number in fact_pages and any(word in line.lower() for word in PAGE_KEYWORDS))
                    or MANAGEMENT_WORDS.search(line)]
        # A page can also contain NEEDS_REVIEW comparison columns. Keep the table
        # label as a locator, but do not let raw OCR/PDF numbers bypass fact review.
        excerpt = re.sub(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?", "[number omitted]",
                         "\n".join(relevant))[:min(remaining, 900)]
        if not excerpt:
            continue
        evidence_id = f"D{document.id}P{page.page_number}"
        registry[evidence_id] = {"kind": "DOCUMENT_PAGE", "document_id": document.id,
            "page": page.page_number, "source_title": event.title,
            "document_url": f"/api/documents/{document.id}/file#page={page.page_number}",
            "excerpt": excerpt}
        excerpts.append({"evidence_id": evidence_id, "excerpt": excerpt,
                         "note": "Page locator only; numbers omitted. Use verified facts for values."})
        remaining -= len(excerpt)

    payload = {"company": company_name, "symbol": symbol,
        "period": {"id": period.id, "fiscal_year": period.fiscal_year, "quarter": period.quarter,
                   "end": period.period_end.isoformat(), "type": period.period_type.value,
                   "scope": period.scope.value, "status": period.status.value},
        "source": {"title": event.title, "origin": event.data_origin.value,
                   "document_id": document.id, "sha256": document.sha256_hash,
                   "published_at": event.published_at.isoformat()},
        "verified_facts": [{"evidence_id": f"F{fact.id}",
                            **_prompt_evidence(registry[f"F{fact.id}"])}
                           for fact in current_facts],
        "deterministic_metrics": [{"evidence_id": f"M{metric.id}",
                                   **_prompt_evidence(registry[f"M{metric.id}"])}
                                  for metric in sorted(period.metrics, key=lambda item: item.id)
                                  if f"M{metric.id}" in registry],
        "comparison_facts": [{"evidence_id": key, **_prompt_evidence(item)} for key, item in registry.items()
                             if key.startswith("F") and item["period_id"] != period.id],
        "source_excerpts": excerpts,
        "market_snapshot": ({"evidence_id": f"Q{fresh_snapshot.id}",
            **registry[f"Q{fresh_snapshot.id}"]} if fresh_snapshot else None),
        "calculated_valuation": valuation_items,
        "investor_reasoning": reasoning,
        "market_data_note": ("Use CALCULATED_VALUATION IDs for authoritative ratios. The quote's P/E and legacy PEG "
                             "are third-party or user-entered references only. Forward growth is an assumption, not a result. "
                             "The quote is dated; compare its date with today."
                             if fresh_snapshot else "No fresh sourced price and valuation observation is available."),
        "data_quality": "Only VALIDATED periods and verified facts are included. PDF excerpt numbers are omitted; other periods may need review."}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        # The structured facts and metrics are essential; trim document excerpts first.
        payload["source_excerpts"] = []
        registry = {key: value for key, value in registry.items() if not key.startswith("D")}
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        payload["calculated_valuation"] = [item for item in valuation_items if item["metric_code"] in
                                          {"PE_TTM", "PEG_FORWARD", "PS_TTM"}]
        kept_valuation_ids = {item["evidence_id"] for item in payload["calculated_valuation"]}
        registry = {key: value for key, value in registry.items()
                    if not key.startswith("V") or key in kept_valuation_ids}
        if "R_CASH" in registry:
            registry.pop("R_CASH")
            reasoning["relationships"].pop("cash_vs_pat", None)
        payload["investor_reasoning"] = reasoning
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        # Historical transition detail is useful, but the current verified result takes priority.
        for pattern in reasoning["seasonality"].values():
            pattern["historical_transitions"] = []
        payload["investor_reasoning"] = reasoning
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        raise ValueError("CONTEXT_LIMIT_EXCEEDED")
    return AnalysisContext(text=text, registry=registry, hash=hashlib.sha256(text.encode()).hexdigest(),
                           fact_count=len(current_facts), metric_count=len(metric_items), reasoning=reasoning)


def _fact_item(fact: FinancialFact, period: FinancialPeriod) -> dict:
    return {"kind": "FACT", "fact_id": fact.id, "metric_code": fact.metric_code.value,
            "value": str(fact.value), "unit": fact.unit.value,
            "period_id": period.id, "period_end": period.period_end.isoformat(),
            "scope": period.scope.value, "status": fact.status.value,
            "document_id": fact.source_document_id, "page": fact.source_page,
            "source_label": fact.source_label,
            "document_url": f"/api/documents/{fact.source_document_id}/file"
                            + (f"#page={fact.source_page}" if fact.source_page else "")}


def _prompt_evidence(item: dict) -> dict:
    """The model needs the value and provenance IDs, not repeated database metadata."""
    keys = ("kind", "metric_code", "value", "unit", "period_end", "scope",
            "comparison_period_id", "input_evidence_ids")
    result = {key: item[key] for key in keys if key in item}
    if "unit" in result:
        result["unit"] = UNIT_LABELS.get(result["unit"], result["unit"])
    return result


def _reasoning_context(db: Session, period: FinancialPeriod, registry: dict[str, dict], snapshot) -> dict:
    metrics = {item["metric_code"]: (key, Decimal(item["value"])) for key, item in registry.items()
               if item["kind"] == "METRIC"}
    valuations = {item["metric_code"]: (key, Decimal(item["value"])) for key, item in registry.items()
                  if item["kind"] == "VALUATION"}
    relationships = {}
    if snapshot and "PE_TTM" in valuations and snapshot.pe_ratio is not None:
        pe_id, pe = valuations["PE_TTM"]
        result = compare_values(pe, snapshot.pe_ratio)
        result.update(metric="PE_TTM", role="CALCULATION_CROSS_CHECK",
                      reference_status=("MATERIAL_DISCREPANCY" if result["percentage_difference"] is not None
                                        and abs(Decimal(result["percentage_difference"])) > Decimal(5)
                                        else "WITHIN_TOLERANCE"),
                      input_evidence_ids=[pe_id, f"Q{snapshot.id}"])
        registry["R_PE"] = {"kind": "RELATIONSHIP", "metric_code": "PE_REFERENCE_CROSSCHECK", **result}
        relationships["pe_reference"] = {"evidence_id": "R_PE", **result}
    if "PAT_YOY" in metrics and "REVENUE_YOY" in metrics:
        pat_id, pat = metrics["PAT_YOY"]
        revenue_id, revenue = metrics["REVENUE_YOY"]
        result = compare_values(pat, revenue)
        result.update(metric="PAT_YOY_VS_REVENUE_YOY", unit="PERCENTAGE_POINTS",
                      input_evidence_ids=[pat_id, revenue_id])
        registry["R_GROWTH"] = {"kind": "RELATIONSHIP", "metric_code": "PAT_YOY_VS_REVENUE_YOY", **result}
        relationships["pat_vs_revenue_growth"] = {"evidence_id": "R_GROWTH", **result}
    if "EBITDA_MARGIN_CHANGE_BPS" in metrics and "EBITDA_MARGIN" in metrics:
        change_id, bps = metrics["EBITDA_MARGIN_CHANGE_BPS"]
        current_id, current = metrics["EBITDA_MARGIN"]
        margin = margin_change_units(current, current - bps / Decimal(100))
        margin["input_evidence_ids"] = [change_id, current_id]
        registry["R_MARGIN"] = {"kind": "RELATIONSHIP", "metric_code": "EBITDA_MARGIN_CHANGE", **margin}
        relationships["ebitda_margin_change"] = {"evidence_id": "R_MARGIN", **margin}
    if "TTM_OPERATING_CASH_FLOW" in valuations and "TTM_PAT" in valuations:
        cash_id, cash = valuations["TTM_OPERATING_CASH_FLOW"]
        pat_id, pat = valuations["TTM_PAT"]
        result = compare_values(cash, pat)
        result.update(metric="CASH_FLOW_VS_PAT", unit="INR_CRORE",
                      input_evidence_ids=[cash_id, pat_id])
        registry["R_CASH"] = {"kind": "RELATIONSHIP", "metric_code": "CASH_FLOW_VS_PAT", **result}
        relationships["cash_vs_pat"] = {"evidence_id": "R_CASH", **result}

    seasonality = {}
    if period.period_type == PeriodType.QUARTERLY:
        periods = list(db.scalars(select(FinancialPeriod).options(selectinload(FinancialPeriod.facts))
            .where(FinancialPeriod.company_id == period.company_id,
                   FinancialPeriod.scope == period.scope,
                   FinancialPeriod.period_type == PeriodType.QUARTERLY,
                   FinancialPeriod.status == FinancialStatus.VALIDATED,
                   FinancialPeriod.period_end < period.period_end)
            .order_by(FinancialPeriod.period_end.desc()).limit(32)))
        by_end = {item.period_end: item for item in periods}
        for subject, fact_code in (("REVENUE", MetricCode.REVENUE_FROM_OPERATIONS),
                                   ("PAT", MetricCode.PAT)):
            qoq_code = f"{subject}_QOQ"
            historical = []
            for current in periods:
                if current.quarter != period.quarter:
                    continue
                prior = by_end.get(current.period_start - timedelta(days=1))
                if prior is None:
                    continue
                current_fact = next((fact for fact in current.facts if fact.metric_code == fact_code
                                     and fact.status in VERIFIED), None)
                prior_fact = next((fact for fact in prior.facts if fact.metric_code == fact_code
                                   and fact.status in VERIFIED and fact.value != 0), None)
                if current_fact is None or prior_fact is None:
                    continue
                change = (current_fact.value - prior_fact.value) / abs(prior_fact.value) * Decimal(100)
                for fact, source_period in ((current_fact, current), (prior_fact, prior)):
                    registry.setdefault(f"F{fact.id}", _fact_item(fact, source_period))
                historical.append({"current_period_end": current.period_end.isoformat(),
                                   "previous_period_end": prior.period_end.isoformat(),
                                   "change_pct": str(change.quantize(Decimal("0.000001"))),
                                   "input_evidence_ids": [f"F{current_fact.id}", f"F{prior_fact.id}"]})
                if len(historical) == 5:
                    break
            current_change = metrics.get(qoq_code)
            pattern = seasonality_pattern(current_change[1] if current_change else None, historical)
            inputs = ([current_change[0]] if current_change else []) + [
                item for transition in historical for item in transition["input_evidence_ids"]]
            pattern.update(metric_code=qoq_code, input_evidence_ids=inputs)
            evidence_id = f"H_{subject}"
            registry[evidence_id] = {"kind": "SEASONALITY", **pattern}
            seasonality[subject.lower()] = {"evidence_id": evidence_id, **pattern}

    return {"valuation": {
        "price": str(snapshot.close_price) if snapshot else None,
        "price_date": snapshot.as_of_date.isoformat() if snapshot else None,
        "pe_ttm": str(valuations["PE_TTM"][1]) if "PE_TTM" in valuations else None,
        "ps_ttm": str(valuations["PS_TTM"][1]) if "PS_TTM" in valuations else None,
        "ev_ebitda_ttm": str(valuations["EV_EBITDA_TTM"][1]) if "EV_EBITDA_TTM" in valuations else None,
        "peg_forward": str(valuations["PEG_FORWARD"][1]) if "PEG_FORWARD" in valuations else None,
        "forward_eps_growth_pct": str(snapshot.forward_eps_growth_pct) if snapshot and snapshot.forward_eps_growth_pct is not None else None,
        "forward_growth_source_type": snapshot.growth_source_type if snapshot else None},
        "relationships": relationships, "seasonality": seasonality,
        "materiality_order": prioritize_changes({code: value for code, (_, value) in metrics.items()})}
