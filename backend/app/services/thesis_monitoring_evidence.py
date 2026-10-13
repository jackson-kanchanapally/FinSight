"""Current verified evidence, using the existing fact and checkpoint contracts."""
import hashlib
import json
from decimal import Decimal
from sqlalchemy import select
from app.ai.analyst.context_builder import _fact_item
from app.models.commentary import ManagementStatement, ManagementGuidance
from app.models.event import CompanyEvent, Document, DocumentPage
from app.models.financial import FinancialPeriod, FinancialFact, FinancialMetric
from app.services.checkpoints import checkpoint_review
from app.services.quarterly_review import statement_evidence
from app.services.thesis_evidence import VERIFIED, day
from app.services.materiality import materiality
from app.services.financials.calculator import growth, margin, margin_change_bps

VERSION = "THESIS_MONITOR_V1"
MATERIAL_EVENTS = {"ORDER_WIN", "ORDER_CANCELLATION", "ACQUISITION", "DIVESTMENT", "CAPEX",
    "MANAGEMENT_CHANGE", "AUDITOR_CHANGE", "FUND_RAISE", "CREDIT_RATING", "PLEDGE", "REGULATORY", "LITIGATION"}
OFFICIAL = {"NSE", "BSE", "ESDS_IR", "COMPANY_IR"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def official(event):
    return bool(event and ((event.data_origin.value == "LIVE" and event.source.upper() in OFFICIAL)
        or any(r.data_origin.value == "LIVE" and r.source.upper() in OFFICIAL for r in event.references)))


def metric_is_current(metric, registry):
    """Reject a stored metric when a corrected input has not been recalculated."""
    inputs = [registry[f"F{i}"] for i in metric.input_fact_ids]
    def value(code, owner=metric.financial_period_id):
        matches = [r for r in inputs if r["period_id"] == owner and r["metric_code"] == code]
        return Decimal(matches[0]["value"]) if len(matches) == 1 else None
    def ebitda(owner):
        parts = [value(c, owner) for c in ("PBT", "FINANCE_COST", "DEPRECIATION")]
        return sum(parts) if all(v is not None for v in parts) else None
    code = metric.metric_code.value
    if code == "EBITDA":
        expected = ebitda(metric.financial_period_id)
    elif code in {"PAT_MARGIN", "EBITDA_MARGIN"}:
        expected = margin(value("PAT") if code == "PAT_MARGIN" else ebitda(metric.financial_period_id), value("REVENUE_FROM_OPERATIONS"))
    elif code in {"REVENUE_YOY", "REVENUE_QOQ", "PAT_YOY", "PAT_QOQ"}:
        source = "PAT" if code.startswith("PAT") else "REVENUE_FROM_OPERATIONS"
        expected = growth(value(source), value(source, metric.comparison_period_id))
    elif code == "EBITDA_MARGIN_CHANGE_BPS":
        expected = margin_change_bps(margin(ebitda(metric.financial_period_id), value("REVENUE_FROM_OPERATIONS")),
            margin(ebitda(metric.comparison_period_id), value("REVENUE_FROM_OPERATIONS", metric.comparison_period_id)))
    else:
        return False
    return expected is not None and abs(expected - metric.value) <= Decimal("0.000001")


def collect_evidence(db, company_id, scope, today):
    events = {e.id: e for e in db.scalars(select(CompanyEvent).where(CompanyEvent.company_id == company_id))
              if day(e.published_at) <= today}
    documents = {d.id: d for d in db.scalars(select(Document).where(Document.company_id == company_id))
                 if d.event_id in events and d.processing_status.value == "PROCESSED"
                 and (d.published_at is None or day(d.published_at) <= today)}
    owners = {p.id: p for p in db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.company_id == company_id, FinancialPeriod.scope == scope,
        FinancialPeriod.status == "VALIDATED", FinancialPeriod.period_end <= today))
        if p.source_event_id in events and p.source_document_id in documents}
    # Annual and quarterly statements can share an end date. Keep both in the
    # registry; the freshest quarterly period anchors execution requirements.
    candidates = [p for p in owners.values() if p.period_type.value in {"QUARTERLY", "ANNUAL"}]
    if not candidates:
        return None
    period = max(candidates, key=lambda p: (p.period_end, p.period_type.value == "QUARTERLY", p.id))
    registry = {}
    for fact in db.scalars(select(FinancialFact).where(FinancialFact.financial_period_id.in_(owners))):
        document = documents.get(fact.source_document_id)
        owner = owners[fact.financial_period_id]
        if (fact.status.value not in VERIFIED or not document or not fact.value.is_finite()
                or fact.source_column_date != owner.period_end
                or (not official(events[document.event_id]) and fact.status.value != "MANUALLY_VERIFIED")):
            continue
        registry[f"F{fact.id}"] = {**_fact_item(fact, owner), "period_type": owner.period_type.value,
            "value": str(fact.value.quantize(Decimal("0.000001"))),
            "period_start": owner.period_start.isoformat(), "document_revision": document.sha256_hash,
            "published_at": events[document.event_id].published_at.isoformat(),
            "corrections": [{"id": c.id, "value": str(c.corrected_value.quantize(Decimal("0.000001"))), "reason": c.reason}
                            for c in sorted(fact.corrections, key=lambda c: c.id)]}
    if not any(r.get("period_id") == period.id for r in registry.values()):
        return None
    for metric in db.scalars(select(FinancialMetric).where(FinancialMetric.financial_period_id.in_(owners))):
        inputs = [f"F{i}" for i in metric.input_fact_ids]
        if not inputs or not all(k in registry for k in inputs) or not metric.value.is_finite():
            continue
        allowed = {metric.financial_period_id, metric.comparison_period_id} - {None}
        if ({registry[k]["period_id"] for k in inputs} != allowed
                or any(registry[k]["period_type"] != owners[metric.financial_period_id].period_type.value for k in inputs)
                or not metric_is_current(metric, registry)):
            continue
        owner = owners[metric.financial_period_id]
        registry[f"M{metric.id}"] = {"kind": "METRIC", "metric_code": metric.metric_code.value,
            "value": str(metric.value.quantize(Decimal("0.000001"))), "unit": metric.unit.value, "period_id": owner.id,
            "period_type": owner.period_type.value, "period_start": owner.period_start.isoformat(),
            "period_end": owner.period_end.isoformat(), "input_evidence_ids": inputs,
            "comparison_period_id": metric.comparison_period_id}
    pages = {(p.document_id, p.page_number): " ".join(p.text.split()) for p in db.scalars(
        select(DocumentPage).where(DocumentPage.document_id.in_(documents)))}
    for statement in db.scalars(select(ManagementStatement).where(
            ManagementStatement.company_id == company_id, ManagementStatement.financial_period_id.in_(owners))):
        event, document = events.get(statement.event_id), documents.get(statement.document_id)
        quote = " ".join(statement.raw_statement.split())
        if (not official(event) or not document or document.event_id != statement.event_id
                or day(statement.published_at) > today or statement.extraction_confidence != "HIGH"
                or not quote or quote not in pages.get((statement.document_id, statement.page_number), "")):
            continue
        registry[statement.evidence_id] = {**statement_evidence(statement, materiality(statement.raw_statement) or "MONITORING"),
            "period_id": statement.financial_period_id, "document_revision": document.sha256_hash}
    # Existing checkpoint calculation is the sole management delivery authority.
    for row in checkpoint_review(db, period, as_of=today):
        source = row["source_statement_id"]
        outcome_ids = row["actual_outcome"].get("evidence_ids", [])
        if source not in registry or any(k not in registry for k in outcome_ids):
            continue
        if row["status"] in {"DELIVERED", "MISSED", "WITHDRAWN", "DELAYED"} and not outcome_ids:
            continue
        row.pop("evaluated_as_of", None)  # Wall-clock time is not an evidence revision.
        registry[row["evidence_id"]] = row
    for guidance in db.scalars(select(ManagementGuidance).where(ManagementGuidance.company_id == company_id)):
        source = f"S{guidance.statement_id}"
        if source not in registry or day(guidance.published_at) > today:
            continue
        registry[f"G{guidance.id}"] = {"kind": "GUIDANCE", "metric_code": guidance.metric,
            "target_period": guidance.target_period, "unit": guidance.unit,
            "lower": str(guidance.lower_bound.quantize(Decimal("0.0001"))), "upper": str(guidance.upper_bound.quantize(Decimal("0.0001"))),
            "status": guidance.status, "source_statement_id": source,
            "published_at": guidance.published_at.isoformat(), "authority": "MANAGEMENT_CLAIM",
            **{k: registry[source][k] for k in ("document_id", "document_url", "page", "source_quote")}}
    for event in events.values():
        if not official(event) or event.event_type.value not in MATERIAL_EVENTS or event.processing_status.value != "PROCESSED":
            continue
        if event.reporting_basis and event.reporting_basis.value not in {scope, "BOTH"}:
            continue
        for document in documents.values():
            if document.event_id != event.id:
                continue
            available = sorted((number, text) for (doc_id, number), text in pages.items() if doc_id == document.id and text)
            if not available:
                continue
            number, quote = available[0]
            registry[f"E{event.id}D{document.id}"] = {"kind": "CORPORATE_DISCLOSURE",
                "event_type": event.event_type.value, "title": event.title, "source_quote": quote[:1200],
                "document_id": document.id, "page": number, "document_revision": document.sha256_hash,
                "document_url": f"/api/documents/{document.id}/file#page={number}",
                "published_at": event.published_at.isoformat(), "authority": "OFFICIAL_DISCLOSURE"}
    for row in registry.values():
        row.update(company_id=company_id, scope=scope)
        row["revision"] = digest(row)
    dates = [day(r["published_at"]) for r in registry.values() if r.get("published_at")]
    return period, max(dates), registry
