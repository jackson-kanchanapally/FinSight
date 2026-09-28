import hashlib
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.event import CompanyEvent, DataOrigin, Document, DocumentPage, EventType, ProcessingStatus
from app.models.financial import (FactMethod, FactStatus, FinancialFact, FinancialFactCorrection, FinancialMetric, FinancialPeriod,
                                  FinancialProcessingRun, FinancialStatus, FinancialUnit, MetricCode, OriginalUnit,
                                  PeriodType)
from app.services.financials.calculator import growth, margin, margin_change_bps, normalize_currency
from app.services.financials.extractor import RawPeriod, extract_pdf_tables
from app.services.financials.period import PeriodIdentity, previous_quarter_end, previous_year_end

logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    from app.ingestion.storage import LocalDocumentStorage


class FinancialProcessingError(ValueError):
    pass


def _record_run(db: Session, event_id: int, document_id: int, status: FinancialStatus,
                note: str | None) -> None:
    run = db.scalar(select(FinancialProcessingRun).where(
        FinancialProcessingRun.source_event_id == event_id,
        FinancialProcessingRun.source_document_id == document_id))
    if run is None:
        run = FinancialProcessingRun(source_event_id=event_id, source_document_id=document_id)
        db.add(run)
    run.status = status
    run.status_note = note
    run.last_attempted_at = datetime.now(timezone.utc)
    db.commit()


def _store_period(db: Session, event: CompanyEvent, document: Document, raw: RawPeriod) -> tuple[FinancialPeriod, bool]:
    identity = raw.identity
    existing = db.scalar(select(FinancialPeriod).where(
        FinancialPeriod.company_id == event.company_id,
        FinancialPeriod.period_end == identity.end,
        FinancialPeriod.period_type == identity.period_type,
        FinancialPeriod.scope == raw.scope))
    if existing:
        if existing.source_document_id != document.id:
            raise FinancialProcessingError(
                f"{identity.end} {identity.period_type.value} {raw.scope.value} already has another source PDF; review revision")
        if existing.status == FinancialStatus.NEEDS_REVIEW and not raw.issues:
            db.refresh(existing, ["facts"])
            by_code = {fact.metric_code: fact for fact in existing.facts}
            for code, fact in by_code.items():
                if code not in raw.facts:
                    fact.status = FactStatus.NEEDS_REVIEW
            for raw_fact in raw.facts.values():
                unit = (FinancialUnit.INR_PER_SHARE if raw_fact.original_unit == OriginalUnit.INR_PER_SHARE
                        else FinancialUnit.INR_CRORE)
                value = (raw_fact.original_value if unit == FinancialUnit.INR_PER_SHARE else
                         normalize_currency(raw_fact.original_value, raw_fact.original_unit))
                fact = by_code.get(raw_fact.code)
                if fact is None:
                    fact = FinancialFact(financial_period_id=existing.id, metric_code=raw_fact.code,
                                         source_document_id=document.id)
                    db.add(fact)
                elif fact.value != value:
                    db.add(FinancialFactCorrection(fact_id=fact.id, previous_value=fact.value,
                        corrected_value=value, reason="Same PDF re-extracted with reconciled component rows"))
                fact.value = value
                fact.unit = unit
                fact.original_value = None if raw_fact.calculated else raw_fact.original_value
                fact.original_unit = None if raw_fact.calculated else raw_fact.original_unit
                fact.source_page = raw_fact.page
                fact.source_label = raw_fact.label
                fact.source_column_date = raw_fact.column_date
                fact.extraction_method = FactMethod.CALCULATED if raw_fact.calculated else FactMethod.PDF_TABLE
                fact.status = FactStatus.VERIFIED_PDF
            existing.status = FinancialStatus.VALIDATED
            existing.status_note = ("Previously unreadable cells calculated from reconciled PDF components"
                                    if any(fact.calculated for fact in raw.facts.values()) else None)
            db.commit()
            db.refresh(existing, ["facts"])
        return existing, False
    status = FinancialStatus.NEEDS_REVIEW if raw.issues else FinancialStatus.VALIDATED
    period = FinancialPeriod(company_id=event.company_id, fiscal_year=identity.fiscal_year,
        quarter=identity.quarter, period_start=identity.start, period_end=identity.end,
        period_type=identity.period_type, scope=raw.scope, source_event_id=event.id,
        source_document_id=document.id, status=status, status_note="; ".join(raw.issues) or None)
    db.add(period)
    db.flush()
    for raw_fact in raw.facts.values():
        unit = (FinancialUnit.INR_PER_SHARE if raw_fact.original_unit == OriginalUnit.INR_PER_SHARE
                else FinancialUnit.INR_CRORE)
        value = (raw_fact.original_value if unit == FinancialUnit.INR_PER_SHARE else
                 normalize_currency(raw_fact.original_value, raw_fact.original_unit))
        db.add(FinancialFact(financial_period_id=period.id, metric_code=raw_fact.code,
            value=value, unit=unit,
            original_value=None if raw_fact.calculated else raw_fact.original_value,
            original_unit=None if raw_fact.calculated else raw_fact.original_unit,
            source_document_id=document.id,
            source_page=raw_fact.page, source_label=raw_fact.label,
            source_column_date=raw_fact.column_date,
            extraction_method=FactMethod.CALCULATED if raw_fact.calculated else FactMethod.PDF_TABLE,
            status=FactStatus.NEEDS_REVIEW if raw.issues else FactStatus.VERIFIED_PDF))
    db.commit()
    return period, True


def _fact(period: FinancialPeriod, code: MetricCode) -> FinancialFact | None:
    return next((fact for fact in period.facts if fact.metric_code == code
                 and fact.status in {FactStatus.VERIFIED_PDF, FactStatus.VERIFIED_STRUCTURED,
                                     FactStatus.MANUALLY_VERIFIED}), None)


def _put_metric(db: Session, period: FinancialPeriod, code: MetricCode, value: Decimal | None,
                unit: FinancialUnit, inputs: list[FinancialFact],
                comparison: FinancialPeriod | None = None) -> None:
    if value is None:
        return
    metric = db.scalar(select(FinancialMetric).where(
        FinancialMetric.financial_period_id == period.id, FinancialMetric.metric_code == code))
    if metric is None:
        metric = FinancialMetric(financial_period_id=period.id, metric_code=code)
        db.add(metric)
    metric.value = value
    metric.unit = unit
    metric.input_fact_ids = [fact.id for fact in inputs]
    metric.comparison_period_id = comparison.id if comparison else None


def calculate_company_metrics(db: Session, company_id: int) -> None:
    periods = list(db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.company_id == company_id,
        FinancialPeriod.status == FinancialStatus.VALIDATED).order_by(FinancialPeriod.period_end)).all())
    for period in periods:
        db.refresh(period, ["facts"])
        identity = PeriodIdentity(period.period_start, period.period_end, period.fiscal_year,
                                  period.quarter, period.period_type)
        same_scope = [other for other in periods if other.scope == period.scope
                      and other.period_type == period.period_type]
        yoy = next((p for p in same_scope if p.period_end == previous_year_end(identity)), None)
        previous_end = previous_quarter_end(identity)
        qoq = next((p for p in same_scope if previous_end and p.period_end == previous_end), None)
        revenue = _fact(period, MetricCode.REVENUE_FROM_OPERATIONS)
        pat = _fact(period, MetricCode.PAT)
        pbt = _fact(period, MetricCode.PBT)
        finance = _fact(period, MetricCode.FINANCE_COST)
        depreciation = _fact(period, MetricCode.DEPRECIATION)
        ebitda = pbt.value + finance.value + depreciation.value if pbt and finance and depreciation else None
        if ebitda is not None:
            _put_metric(db, period, MetricCode.EBITDA, ebitda, FinancialUnit.INR_CRORE,
                        [pbt, finance, depreciation])
        pat_margin = margin(pat.value if pat else None, revenue.value if revenue else None)
        _put_metric(db, period, MetricCode.PAT_MARGIN, pat_margin, FinancialUnit.PERCENT,
                    [fact for fact in (pat, revenue) if fact])
        ebitda_margin = margin(ebitda, revenue.value if revenue else None)
        _put_metric(db, period, MetricCode.EBITDA_MARGIN, ebitda_margin, FinancialUnit.PERCENT,
                    [fact for fact in (pbt, finance, depreciation, revenue) if fact])
        for prior, suffix in ((yoy, "YOY"), (qoq, "QOQ")):
            if prior is None:
                continue
            db.refresh(prior, ["facts"])
            for code, metric_code in ((MetricCode.REVENUE_FROM_OPERATIONS, MetricCode[f"REVENUE_{suffix}"]),
                                      (MetricCode.PAT, MetricCode[f"PAT_{suffix}"])):
                current_fact, prior_fact = _fact(period, code), _fact(prior, code)
                if current_fact and prior_fact:
                    _put_metric(db, period, metric_code, growth(current_fact.value, prior_fact.value),
                        FinancialUnit.PERCENT, [current_fact, prior_fact], prior)
            if suffix == "YOY":
                prior_revenue = _fact(prior, MetricCode.REVENUE_FROM_OPERATIONS)
                prior_pbt = _fact(prior, MetricCode.PBT)
                prior_finance = _fact(prior, MetricCode.FINANCE_COST)
                prior_depreciation = _fact(prior, MetricCode.DEPRECIATION)
                if all((prior_revenue, prior_pbt, prior_finance, prior_depreciation)):
                    prior_ebitda = prior_pbt.value + prior_finance.value + prior_depreciation.value
                    prior_margin = margin(prior_ebitda, prior_revenue.value)
                    _put_metric(db, period, MetricCode.EBITDA_MARGIN_CHANGE_BPS,
                        margin_change_bps(ebitda_margin, prior_margin), FinancialUnit.BASIS_POINTS,
                        [fact for fact in (pbt, finance, depreciation, revenue,
                                          prior_pbt, prior_finance, prior_depreciation, prior_revenue) if fact], prior)
    db.commit()


def process_financial_event(db: Session, event_id: int, *,
                            storage: "LocalDocumentStorage | None" = None) -> dict:
    from app.ingestion.storage import LocalDocumentStorage
    event = db.get(CompanyEvent, event_id)
    if event is None:
        raise FinancialProcessingError("Event not found")
    if event.event_type != EventType.FINANCIAL_RESULTS:
        raise FinancialProcessingError("Event is not a financial-results disclosure")
    if event.data_origin != DataOrigin.LIVE:
        raise FinancialProcessingError("Only live official financial-result documents can be processed")
    document = db.scalar(select(Document).where(Document.event_id == event.id,
        Document.processing_status == ProcessingStatus.PROCESSED,
        Document.local_path.is_not(None)).order_by(Document.id))
    if document is None:
        raise FinancialProcessingError("Event has no processed PDF document")
    storage = storage or LocalDocumentStorage(settings.document_storage_path)
    try:
        path = storage.resolve(document.local_path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != document.sha256_hash:
            raise FinancialProcessingError("Stored PDF hash does not match document metadata")
        stored_pages = set(db.scalars(select(DocumentPage.page_number).where(
            DocumentPage.document_id == document.id)).all())
        if not stored_pages:
            raise FinancialProcessingError("Document has no extracted page records")
        extracted = extract_pdf_tables(path)
        if any(fact.page not in stored_pages for period in extracted for fact in period.facts.values()):
            raise FinancialProcessingError("Financial fact page is missing from document_pages")
    except (OSError, ValueError, RuntimeError) as exc:
        _record_run(db, event.id, document.id, FinancialStatus.FAILED, type(exc).__name__)
        raise FinancialProcessingError("Financial PDF cannot be processed; failure recorded") from exc
    if not extracted:
        _record_run(db, event.id, document.id, FinancialStatus.NEEDS_REVIEW,
                    "No financial table with readable scope, units, and period headers")
        return {"event_id": event.id, "document_id": document.id, "status": "NEEDS_REVIEW",
                "periods_created": 0, "periods": [], "note": "No reliable financial table found"}
    created = 0
    results = []
    conflict = False
    for raw in extracted:
        try:
            period, is_new = _store_period(db, event, document, raw)
            created += int(is_new)
            results.append({"id": period.id, "period_end": period.period_end.isoformat(),
                "period_type": period.period_type.value, "scope": period.scope.value,
                "status": period.status.value, "facts": len(period.facts),
                "note": period.status_note})
            logger.info("financials.period_processed", extra={"event_id": event.id,
                "document_id": document.id, "period_end": str(period.period_end),
                "scope": period.scope.value, "status": period.status.value, "facts": len(period.facts)})
        except FinancialProcessingError as exc:
            conflict = True
            logger.warning("financials.period_conflict", extra={"event_id": event.id,
                "document_id": document.id, "error": str(exc)})
            results.append({"period_end": raw.identity.end.isoformat(), "scope": raw.scope.value,
                            "status": "NEEDS_REVIEW", "note": str(exc)})
    calculate_company_metrics(db, event.company_id)
    status = (FinancialStatus.NEEDS_REVIEW if conflict or any(
        row["status"] != "VALIDATED" for row in results) else FinancialStatus.VALIDATED)
    _record_run(db, event.id, document.id, status,
                "Some table columns require review" if status == FinancialStatus.NEEDS_REVIEW else None)
    return {"event_id": event.id, "document_id": document.id, "status": status.value,
            "periods_created": created, "periods": results}
