"""Resolve existing verified facts and official statements with precise availability times."""
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.event import CompanyEvent, Document, DocumentPage
from app.models.financial import FinancialPeriod, FinancialFact, FinancialMetric
from app.models.commentary import ManagementStatement
from app.services.thesis_evidence import VERIFIED
from app.services.thesis_monitoring_evidence import official, metric_is_current
from app.ai.analyst.context_builder import _fact_item


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def utcnow():
    return datetime.now(timezone.utc)


def publication(event):
    # A date-only result could have appeared at any time that day: use its start.
    value = utc(event.published_at)
    if event.publication_precision.value == "DATE":
        value = value.replace(hour=0, minute=0, second=0, microsecond=0)
    return value


def fact_evidence(db, fact, company_id, scope, now, batch=None):
    get = (lambda model, key: batch['models'].get(model, {}).get(key)) if batch is not None else db.get
    period = get(FinancialPeriod, fact.financial_period_id)
    document = get(Document, fact.source_document_id)
    event = get(CompanyEvent, document.event_id) if document else None
    if (not period or period.company_id != company_id or period.scope.value != scope
        or period.status.value != "VALIDATED" or period.period_end > now.date()
        or fact.status.value not in VERIFIED or not fact.value.is_finite()
        or fact.source_column_date != period.period_end or not document or document.company_id != company_id
        or document.processing_status.value != "PROCESSED" or not event or event.company_id != company_id
        or event.data_origin.value == "FIXTURE"
        or (not official(event) and fact.status.value != "MANUALLY_VERIFIED")
        or utc(event.published_at) > now or (document.published_at and utc(document.published_at) > now)):
        return None
    from app.services.financials.period import identify_period
    try:
        identity = identify_period(period.period_end, period.period_type)
    except ValueError:
        return None
    if period.period_start != identity.start or (event.reporting_basis and event.reporting_basis.value not in {scope, "BOTH"}):
        return None
    return {**_fact_item(fact, period), "value": str(fact.value.quantize(Decimal("0.000001"))), "unit": fact.unit.value,
        "company_id": company_id, "scope": scope, "period_type": period.period_type.value,
        "period_start": period.period_start.isoformat(), "period_end": period.period_end.isoformat(),
        "published_at": publication(event).isoformat(),
        "available_at": max(utc(event.published_at), utc(document.published_at or event.published_at)).isoformat(),
        "document_revision": document.sha256_hash,
        "publication_precision": event.publication_precision.value,
        "source_url": document.document_url or document.source_url,
        "corrections": [{"id": c.id, "previous_value": str(c.previous_value), "corrected_value": str(c.corrected_value),
                         "corrected_at": utc(c.corrected_at).isoformat()} for c in sorted(fact.corrections, key=lambda c: c.id)]}


def statement_evidence(db, statement, company_id, scope, now):
    document = db.get(Document, statement.document_id)
    event = db.get(CompanyEvent, statement.event_id)
    period = db.get(FinancialPeriod, statement.financial_period_id)
    page = db.scalar(select(DocumentPage).where(DocumentPage.document_id == statement.document_id,
                                               DocumentPage.page_number == statement.page_number))
    return validated_statement(statement, document, event, period, page, company_id, scope, now)


def validated_statement(statement, document, event, period, page, company_id, scope, now):
    """Shared validation for individual registrations and batched history reads."""
    quote = " ".join(statement.raw_statement.split())
    if (statement.company_id != company_id or not period or period.company_id != company_id or period.scope.value != scope
        or not official(event) or event.company_id != company_id
        or (event.reporting_basis and event.reporting_basis.value not in {scope, "BOTH"})
        or not document or document.company_id != company_id or document.event_id != event.id
        or document.processing_status.value != "PROCESSED" or statement.extraction_confidence != "HIGH"
        or not page or not quote or quote not in " ".join(page.text.split())
        or max(utc(statement.published_at), utc(event.published_at)) > now
        or (document.published_at and utc(document.published_at) > now)):
        return None
    return {"kind": "STATEMENT", "evidence_id": statement.evidence_id, "company_id": company_id, "scope": scope,
        "source_quote": statement.raw_statement, "published_at": max(utc(statement.published_at), utc(event.published_at), utc(document.published_at or event.published_at)).isoformat(),
        "document_id": document.id, "document_revision": document.sha256_hash, "page": statement.page_number,
        "document_url": f"/api/documents/{document.id}/file#page={statement.page_number}", "source": event.source}


def resolve_source(db, key, company_id, scope, now):
    if not key:
        return None
    model = FinancialFact if key.startswith("F") else ManagementStatement
    row = db.get(model, int(key[1:]))
    if not row:
        return None
    return (fact_evidence if model is FinancialFact else statement_evidence)(db, row, company_id, scope, now)


def financial_evidence_batch(db, periods):
    """Bounded prefetch for forecasting; reuse the exact single-fact validation."""
    from collections import defaultdict
    ids = [period.id for period in periods]
    facts = list(db.scalars(select(FinancialFact).where(FinancialFact.financial_period_id.in_(ids))
        .options(selectinload(FinancialFact.corrections))))
    documents = list(db.scalars(select(Document).where(Document.id.in_({fact.source_document_id for fact in facts}))))
    events = list(db.scalars(select(CompanyEvent).where(CompanyEvent.id.in_({doc.event_id for doc in documents}))
        .options(selectinload(CompanyEvent.references))))
    metrics = list(db.scalars(select(FinancialMetric).where(FinancialMetric.financial_period_id.in_(ids))))
    by_metric = defaultdict(list)
    for fact in facts:
        by_metric[(fact.financial_period_id, fact.metric_code)].append(fact)
    return {'models': {model: {row.id: row for row in rows} for model, rows in
        ((FinancialPeriod, periods), (Document, documents), (CompanyEvent, events), (FinancialFact, facts))},
        'periods': {(p.company_id, p.scope, p.period_end, p.period_type): p for p in periods},
        'facts': by_metric, 'metrics': {(m.financial_period_id, m.metric_code): m for m in metrics}}


def actual_evidence(db, expectation, now, batch=None):
    period = batch['periods'].get((expectation.company_id, expectation.scope, expectation.period_end, expectation.period_type)) if batch is not None else db.scalar(select(FinancialPeriod).where(FinancialPeriod.company_id == expectation.company_id,
        FinancialPeriod.scope == expectation.scope, FinancialPeriod.period_end == expectation.period_end,
        FinancialPeriod.period_type == expectation.period_type, FinancialPeriod.status == "VALIDATED"))
    if not period:
        return None, None, {}
    facts = batch['facts'].get((period.id, expectation.metric), []) if batch is not None else list(db.scalars(select(FinancialFact).where(FinancialFact.financial_period_id == period.id,
                                                    FinancialFact.metric_code == expectation.metric)))
    for fact in facts:
        source = fact_evidence(db, fact, expectation.company_id, expectation.scope, now, batch)
        if source:
            return period, source, {f"F{fact.id}": source}
    metric = batch['metrics'].get((period.id, expectation.metric)) if batch is not None else db.scalar(select(FinancialMetric).where(FinancialMetric.financial_period_id == period.id,
                                                   FinancialMetric.metric_code == expectation.metric))
    if not metric or not metric.input_fact_ids or not metric.value.is_finite():
        return period, None, {}
    registry = {}
    inputs = [batch['models'][FinancialFact][key] for key in metric.input_fact_ids if key in batch['models'][FinancialFact]] if batch is not None else db.scalars(select(FinancialFact).where(FinancialFact.id.in_(metric.input_fact_ids)))
    for fact in inputs:
        source = fact_evidence(db, fact, expectation.company_id, expectation.scope, now, batch)
        if source:
            registry[f"F{fact.id}"] = source
    keys = [f"F{i}" for i in metric.input_fact_ids]
    from app.services.expectation_math import canonical_unit
    if (not all(k in registry for k in keys)
        or {registry[k]["period_id"] for k in keys} != {period.id, metric.comparison_period_id} - {None}
        or any(registry[k]["period_type"] != expectation.period_type for k in keys)
        or any(registry[k]["unit"] != canonical_unit(registry[k]["metric_code"]) for k in keys)
        or not metric_is_current(metric, registry)):
        return period, None, {}
    if metric.comparison_period_id:
        prior = batch['models'][FinancialPeriod].get(metric.comparison_period_id) if batch is not None else db.get(FinancialPeriod, metric.comparison_period_id)
        if prior is None:
            return period, None, {}
        from app.services.financials.period import identify_period, previous_quarter_end, previous_year_end
        identity = identify_period(period.period_end, period.period_type)
        expected_end = previous_quarter_end(identity) if expectation.metric.endswith("QOQ") else previous_year_end(identity)
        if not prior or prior.period_end != expected_end:
            return period, None, {}
    source = {"kind": "METRIC", "metric_code": expectation.metric, "value": str(metric.value),
        "unit": metric.unit.value, "period_id": period.id, "period_type": period.period_type.value,
        "scope": expectation.scope, "company_id": expectation.company_id,
        "period_start": period.period_start.isoformat(),
        "period_end": period.period_end.isoformat(), "input_evidence_ids": keys,
        "published_at": max(registry[k]["published_at"] for k in keys)}
    registry[f"M{metric.id}"] = source
    return period, source, registry
