"""Read existing commitments without inventing dated forecasts or delivery evidence."""
from collections import defaultdict
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload
from app.models.checkpoint import InvestorCheckpoint as Checkpoint
from app.models.commentary import ManagementStatement as Statement
from app.models.event import CompanyEvent, Document, DocumentPage
from app.models.financial import FinancialPeriod
from app.models.expectations import HistoricalExpectationRevision as Revision, HistoricalExpectation as Ledger
from app.services.expectation_evidence import validated_statement, utc, utcnow
from app.services.expectations import list_expectations, ledger_query
from app.services.commentary import capex_scope_unclear


def guidance_page(db, company_id, *, scope=None, offset=0, limit=50):
    """One stable page: ledger rows first, then unlinked checkpoint IDs descending.

    This projection needs no backfill and never mutates original checkpoints. A
    checkpoint represented by any ledger revision is omitted to avoid double display.
    """
    result = list_expectations(db, company_id, source="MANAGEMENT", scope=scope, offset=offset, limit=limit)
    ledger_groups = defaultdict(list)
    ledger_keys = db.execute(ledger_query(company_id, scope=scope, source="MANAGEMENT").with_only_columns(
        Ledger.id, Ledger.metric, Ledger.scope, Ledger.period_end, Ledger.period_type)).all()
    for key, metric, basis, end, kind in ledger_keys:
        ledger_groups[(metric, basis, end.isoformat(), kind)].append(f"E{key}")
    for item in result["items"]:
        definition = item["definition"]
        key = (definition["metric"], definition["financial_scope"], definition["period"], definition["period_type"])
        item["possible_duplicate_ids"] = [key for key in ledger_groups[key] if key != f"E{item['id']}"]
    linked = select(Revision.id).where(or_(Revision.checkpoint_id == Checkpoint.id,
        (Checkpoint.guidance_id.is_not(None)) & (Revision.guidance_id == Checkpoint.guidance_id))).exists()
    query = select(Checkpoint, Statement, FinancialPeriod, Document, CompanyEvent, DocumentPage).join(
        FinancialPeriod, FinancialPeriod.id == Checkpoint.created_period_id).outerjoin(
        Statement, Statement.id == Checkpoint.statement_id).outerjoin(Document, Document.id == Statement.document_id).outerjoin(
        CompanyEvent, CompanyEvent.id == Statement.event_id).outerjoin(DocumentPage,
        (DocumentPage.document_id == Statement.document_id) & (DocumentPage.page_number == Statement.page_number)).where(
        Checkpoint.company_id == company_id, FinancialPeriod.company_id == company_id, ~linked)
    if scope:
        query = query.where(FinancialPeriod.scope == scope)
    # Lightweight keys include the whole filtered population so flags survive pagination.
    keys = list(db.execute(query.with_only_columns(Checkpoint.id, Checkpoint.metric, Checkpoint.target_date, FinancialPeriod.scope)))
    groups = defaultdict(list)
    for key, metric, deadline, basis in keys:
        if metric:
            groups[(metric, deadline, basis)].append(f"K{key}")
    checkpoint_total = len(keys)
    ledger_total = result["total"]
    remaining = limit - len(result["items"])
    rows = db.execute(query.options(selectinload(CompanyEvent.references)).order_by(Checkpoint.id.desc()).offset(
        max(0, offset - ledger_total)).limit(remaining)).all() if remaining else []
    items = []
    now = utcnow()
    for cp, statement, period, document, event, page in rows:
        source = validated_statement(statement, document, event, period, page, company_id, period.scope.value, now) if statement else None
        if not source:
            status, reason = "UNRESOLVED", "Original source provenance is missing or cannot be verified."
        elif cp.metric == "CAPEX_SCOPE_UNCLEAR" or (cp.metric == "CAPEX" and capex_scope_unclear(statement.raw_statement)):
            status, reason = "UNRESOLVED", "Ambiguous target: total versus additional capex requires clarification."
        elif not cp.target_date:
            status, reason = "UNRESOLVED", "No explicit deadline; no reporting period has been inferred."
        elif now.date() <= cp.target_date:
            status, reason = "PENDING", "Awaiting the stated target date and verified outcome evidence."
        else:
            status, reason = "UNRESOLVED", "Target date passed; no verified outcome is linked to this expectation ledger."
        items.append(dict(id=f"K{cp.id}", checkpoint_id=cp.id, company_id=company_id, financial_scope=period.scope.value,
            statement_id=cp.statement_id, guidance_id=cp.guidance_id, quote=statement.raw_statement if statement else cp.commitment,
            published_at=utc(statement.published_at) if statement else None, target_period=cp.target_period,
            target_date=cp.target_date, target=cp.target, unit=cp.unit, metric=cp.metric, status=status, reason=reason,
            measurement_note=cp.measurement_note, source_verified=bool(source), source=source,
            possible_duplicate_ids=[key for key in groups[(cp.metric, cp.target_date, period.scope)] if key != f"K{cp.id}"],
            scoring_eligible=False))
    return {**result, "total": ledger_total + checkpoint_total, "ledger_total": ledger_total,
        "checkpoint_total": checkpoint_total, "checkpoint_items": items}
