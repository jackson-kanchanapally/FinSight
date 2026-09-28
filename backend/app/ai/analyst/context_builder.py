"""Build a bounded, auditable context from validated database records only."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models.event import CompanyEvent, Document, DocumentPage
from app.models.financial import (FactStatus, FinancialFact, FinancialPeriod, FinancialStatus)
from app.market.valuation import calculate_valuation
from app.market.service import latest_snapshot

VERIFIED = {FactStatus.VERIFIED_PDF, FactStatus.VERIFIED_STRUCTURED, FactStatus.MANUALLY_VERIFIED}
PAGE_KEYWORDS = ("revenue from operations", "profit for the period", "profit after tax",
                 "finance costs", "depreciation", "profit before tax", "basic (in")
MANAGEMENT_WORDS = re.compile(r"\b(?:management|director|ceo|cfo|chairman|guidance|outlook|expects)\b", re.I)


@dataclass(frozen=True)
class AnalysisContext:
    text: str
    registry: dict[str, dict]
    hash: str
    fact_count: int
    metric_count: int


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
        for code in ("ttm_revenue", "ttm_pat", "ttm_ebitda", "ttm_eps", "pe_ttm",
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
                   "document_id": document.id, "sha256": document.sha256_hash},
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
        registry = {key: value for key, value in registry.items()
                    if not key.startswith("V") or key in {item["evidence_id"] for item in payload["calculated_valuation"]}}
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(text) > max_chars:
        raise ValueError("CONTEXT_LIMIT_EXCEEDED")
    return AnalysisContext(text=text, registry=registry, hash=hashlib.sha256(text.encode()).hexdigest(),
                           fact_count=len(current_facts), metric_count=len(metric_items))


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
    return {key: item[key] for key in keys if key in item}
