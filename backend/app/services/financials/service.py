import hashlib
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.event import (
    CompanyEvent,
    DataOrigin,
    Document,
    DocumentPage,
    EventType,
    ProcessingStatus,
)
from app.models.company import Company
from app.models.financial import (
    FactMethod,
    FactStatus,
    FinancialFact,
    FinancialFactCorrection,
    FinancialMetric,
    FinancialPeriod,
    FinancialProcessingRun,
    FinancialStatus,
    FinancialUnit,
    MetricCode,
    OriginalUnit,
    PeriodType,
)
from app.services.financials.calculator import (
    growth,
    margin,
    margin_change_bps,
    normalize_currency,
    printed_rounding,
)
from app.services.financials.extractor import RawPeriod, extract_pdf_tables
from app.services.financials.period import (
    PeriodIdentity,
    previous_quarter_end,
    previous_year_end,
    comparable_period,
)

logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    from app.ingestion.storage import LocalDocumentStorage


class FinancialProcessingError(ValueError):
    pass


def _manual_company_matches(
    db: Session, event: CompanyEvent, document: Document
) -> bool:
    """Require the selected company to be identified inside the uploaded PDF."""
    company = db.get(Company, event.company_id)
    pages = db.scalars(
        select(DocumentPage.text)
        .where(DocumentPage.document_id == document.id)
        .order_by(DocumentPage.page_number)
    ).all()
    name = re.sub(r"[^a-z0-9]+", " ", company.name.casefold()).strip()
    for page in pages:
        normalized = re.sub(r"[^a-z0-9]+", " ", page.casefold())
        if (
            name
            and "financial results" in normalized
            and re.search(rf"(?<!\w){re.escape(name)}(?!\w)", normalized)
        ):
            return True
        if re.search(
            rf"\b(?:scrip\s+symbol|nse\s*(?:symbol|code)?|symbol)\s*[:\-]\s*{re.escape(company.symbol)}\b",
            page,
            re.IGNORECASE,
        ):
            return True
    return False


def _record_run(
    db: Session,
    event_id: int,
    document_id: int,
    status: FinancialStatus,
    note: str | None,
) -> None:
    run = db.scalar(
        select(FinancialProcessingRun).where(
            FinancialProcessingRun.source_event_id == event_id,
            FinancialProcessingRun.source_document_id == document_id,
        )
    )
    if run is None:
        run = FinancialProcessingRun(
            source_event_id=event_id, source_document_id=document_id
        )
        db.add(run)
    run.status = status
    run.status_note = note
    run.last_attempted_at = datetime.now(timezone.utc)
    db.commit()


def _store_period(
    db: Session, event: CompanyEvent, document: Document, raw: RawPeriod
) -> tuple[FinancialPeriod, bool]:
    identity = raw.identity
    existing = db.scalar(
        select(FinancialPeriod).where(
            FinancialPeriod.company_id == event.company_id,
            FinancialPeriod.period_end == identity.end,
            FinancialPeriod.period_type == identity.period_type,
            FinancialPeriod.scope == raw.scope,
        )
    )
    if existing:
        if existing.source_document_id != document.id:
            from app.services.result_sources import compare_authoritative_candidate
            verification = compare_authoritative_candidate(db, existing, event, document, raw)
            if verification is not None:
                db.commit()
                if verification.status == "VERIFIED":
                    return existing, False
            raise FinancialProcessingError(
                f"{identity.end} {identity.period_type.value} {raw.scope.value} already has another source PDF; review revision"
            )
        db.refresh(existing, ["facts"])
        by_code = {fact.metric_code: fact for fact in existing.facts}
        for code, fact in by_code.items():
            if code not in raw.facts:
                fact.status = FactStatus.NEEDS_REVIEW
        for raw_fact in raw.facts.values():
            unit = (
                FinancialUnit.INR_PER_SHARE
                if raw_fact.original_unit == OriginalUnit.INR_PER_SHARE
                else FinancialUnit.INR_CRORE
            )
            value = (
                raw_fact.original_value
                if unit == FinancialUnit.INR_PER_SHARE
                else normalize_currency(raw_fact.original_value, raw_fact.original_unit)
            )
            fact = by_code.get(raw_fact.code)
            if fact is None:
                fact = FinancialFact(
                    financial_period_id=existing.id,
                    metric_code=raw_fact.code,
                    source_document_id=document.id,
                )
                db.add(fact)
            elif fact.value != value:
                db.add(
                    FinancialFactCorrection(
                        fact_id=fact.id,
                        previous_value=fact.value,
                        corrected_value=value,
                        reason="Same PDF re-extracted with reconciled component rows",
                    )
                )
            fact.value = value
            fact.unit = unit
            fact.original_value = (
                None if raw_fact.calculated else raw_fact.original_value
            )
            fact.original_unit = None if raw_fact.calculated else raw_fact.original_unit
            fact.rounding_tolerance = printed_rounding(raw_fact.original_value, raw_fact.original_unit)
            fact.source_page = raw_fact.page
            fact.source_label = raw_fact.label
            fact.source_column_date = raw_fact.column_date
            fact.extraction_method = (
                FactMethod.CALCULATED if raw_fact.calculated else FactMethod.PDF_TABLE
            )
            fact.status = (
                FactStatus.NEEDS_REVIEW if raw.issues else FactStatus.VERIFIED_PDF
            )
        existing.status = (
            FinancialStatus.NEEDS_REVIEW if raw.issues else FinancialStatus.VALIDATED
        )
        existing.status_note = "; ".join(raw.issues) or None
        db.commit()
        db.refresh(existing, ["facts"])
        return existing, False
    status = FinancialStatus.NEEDS_REVIEW if raw.issues else FinancialStatus.VALIDATED
    period = FinancialPeriod(
        company_id=event.company_id,
        fiscal_year=identity.fiscal_year,
        quarter=identity.quarter,
        period_start=identity.start,
        period_end=identity.end,
        period_type=identity.period_type,
        scope=raw.scope,
        source_event_id=event.id,
        source_document_id=document.id,
        status=status,
        status_note="; ".join(raw.issues) or None,
    )
    db.add(period)
    db.flush()
    for raw_fact in raw.facts.values():
        unit = (
            FinancialUnit.INR_PER_SHARE
            if raw_fact.original_unit == OriginalUnit.INR_PER_SHARE
            else FinancialUnit.INR_CRORE
        )
        value = (
            raw_fact.original_value
            if unit == FinancialUnit.INR_PER_SHARE
            else normalize_currency(raw_fact.original_value, raw_fact.original_unit)
        )
        db.add(
            FinancialFact(
                financial_period_id=period.id,
                metric_code=raw_fact.code,
                value=value,
                unit=unit,
                original_value=None if raw_fact.calculated else raw_fact.original_value,
                original_unit=None if raw_fact.calculated else raw_fact.original_unit,
                rounding_tolerance=printed_rounding(raw_fact.original_value, raw_fact.original_unit),
                source_document_id=document.id,
                source_page=raw_fact.page,
                source_label=raw_fact.label,
                source_column_date=raw_fact.column_date,
                extraction_method=(
                    FactMethod.CALCULATED
                    if raw_fact.calculated
                    else FactMethod.PDF_TABLE
                ),
                status=(
                    FactStatus.NEEDS_REVIEW if raw.issues else FactStatus.VERIFIED_PDF
                ),
            )
        )
    db.commit()
    return period, True


def _fact(period: FinancialPeriod, code: MetricCode) -> FinancialFact | None:
    return next(
        (
            fact
            for fact in period.facts
            if fact.metric_code == code
            and fact.unit == FinancialUnit.INR_CRORE
            and fact.source_column_date == period.period_end
            and fact.status
            in {
                FactStatus.VERIFIED_PDF,
                FactStatus.VERIFIED_STRUCTURED,
                FactStatus.MANUALLY_VERIFIED,
            }
        ),
        None,
    )


def _put_metric(
    db: Session,
    period: FinancialPeriod,
    code: MetricCode,
    value: Decimal | None,
    unit: FinancialUnit,
    inputs: list[FinancialFact],
    produced: set[MetricCode],
    comparison: FinancialPeriod | None = None,
) -> None:
    if value is None:
        return
    metric = db.scalar(
        select(FinancialMetric).where(
            FinancialMetric.financial_period_id == period.id,
            FinancialMetric.metric_code == code,
        )
    )
    if metric is None:
        metric = FinancialMetric(financial_period_id=period.id, metric_code=code)
        db.add(metric)
    metric.value = value
    metric.unit = unit
    metric.input_fact_ids = [fact.id for fact in inputs]
    metric.comparison_period_id = comparison.id if comparison else None
    produced.add(code)


def calculate_company_metrics(db: Session, company_id: int) -> None:
    periods = list(
        db.scalars(
            select(FinancialPeriod)
            .where(
                FinancialPeriod.company_id == company_id,
                FinancialPeriod.status == FinancialStatus.VALIDATED,
            )
            .order_by(FinancialPeriod.period_end)
        ).all()
    )
    for period in periods:
        db.refresh(period, ["facts"])
        existing_metrics = list(period.metrics)
        produced: set[MetricCode] = set()
        identity = PeriodIdentity(
            period.period_start,
            period.period_end,
            period.fiscal_year,
            period.quarter,
            period.period_type,
        )
        same_scope = [
            other
            for other in periods
            if other.scope == period.scope and other.period_type == period.period_type
        ]
        yoy = next(
            (p for p in same_scope if comparable_period(period, p, "YOY")), None
        )
        previous_end = previous_quarter_end(identity)
        qoq = next(
            (p for p in same_scope if previous_end and comparable_period(period, p, "QOQ")),
            None,
        )
        revenue = _fact(period, MetricCode.REVENUE_FROM_OPERATIONS)
        pat = _fact(period, MetricCode.PAT)
        pbt = _fact(period, MetricCode.PBT)
        finance = _fact(period, MetricCode.FINANCE_COST)
        depreciation = _fact(period, MetricCode.DEPRECIATION)
        reported_ebitda = _fact(period, MetricCode.EBITDA)
        ebitda = (
            reported_ebitda.value if reported_ebitda else pbt.value + finance.value + depreciation.value
            if pbt and finance and depreciation
            else None
        )
        ebitda_inputs = [reported_ebitda] if reported_ebitda else [fact for fact in (pbt, finance, depreciation) if fact]
        if ebitda is not None:
            _put_metric(
                db,
                period,
                MetricCode.EBITDA,
                ebitda,
                FinancialUnit.INR_CRORE,
                ebitda_inputs,
                produced,
            )
        pat_margin = margin(
            pat.value if pat else None, revenue.value if revenue else None
        )
        _put_metric(
            db,
            period,
            MetricCode.PAT_MARGIN,
            pat_margin,
            FinancialUnit.PERCENT,
            [fact for fact in (pat, revenue) if fact],
            produced,
        )
        ebitda_margin = margin(ebitda, revenue.value if revenue else None)
        _put_metric(
            db,
            period,
            MetricCode.EBITDA_MARGIN,
            ebitda_margin,
            FinancialUnit.PERCENT,
            ebitda_inputs + ([revenue] if revenue else []),
            produced,
        )
        for prior, suffix in ((yoy, "YOY"), (qoq, "QOQ")):
            if prior is None:
                continue
            db.refresh(prior, ["facts"])
            for code, metric_code in (
                (MetricCode.REVENUE_FROM_OPERATIONS, MetricCode[f"REVENUE_{suffix}"]),
                (MetricCode.PAT, MetricCode[f"PAT_{suffix}"]),
            ):
                current_fact, prior_fact = _fact(period, code), _fact(prior, code)
                if current_fact and prior_fact:
                    _put_metric(
                        db,
                        period,
                        metric_code,
                        growth(current_fact.value, prior_fact.value),
                        FinancialUnit.PERCENT,
                        [current_fact, prior_fact],
                        produced,
                        prior,
                    )
            if suffix == "YOY":
                prior_revenue = _fact(prior, MetricCode.REVENUE_FROM_OPERATIONS)
                prior_pbt = _fact(prior, MetricCode.PBT)
                prior_finance = _fact(prior, MetricCode.FINANCE_COST)
                prior_depreciation = _fact(prior, MetricCode.DEPRECIATION)
                prior_reported = _fact(prior, MetricCode.EBITDA)
                same_definition = bool(reported_ebitda) == bool(prior_reported)
                if prior_revenue and same_definition and (prior_reported or all((prior_pbt, prior_finance, prior_depreciation))):
                    prior_ebitda = (
                        prior_reported.value if prior_reported else prior_pbt.value + prior_finance.value + prior_depreciation.value
                    )
                    prior_margin = margin(prior_ebitda, prior_revenue.value)
                    _put_metric(
                        db,
                        period,
                        MetricCode.EBITDA_MARGIN_CHANGE_BPS,
                        margin_change_bps(ebitda_margin, prior_margin),
                        FinancialUnit.BASIS_POINTS,
                        ebitda_inputs + ([revenue] if revenue else []) +
                        ([prior_reported] if prior_reported else [prior_pbt, prior_finance, prior_depreciation]) + [prior_revenue],
                        produced,
                        prior,
                    )
        for metric in existing_metrics:
            if metric.metric_code not in produced:
                db.delete(metric)
    db.commit()
    from app.services.thesis_monitoring import refresh_monitoring
    refresh_monitoring(db, company_id)


def process_financial_event(
    db: Session,
    event_id: int,
    *,
    storage: "LocalDocumentStorage | None" = None,
    confirm_results: bool = False,
) -> dict:
    from app.ingestion.storage import LocalDocumentStorage

    event = db.get(CompanyEvent, event_id)
    if event is None:
        raise FinancialProcessingError("Event not found")
    classify_manual = (
        confirm_results
        and event.data_origin == DataOrigin.MANUAL
        and event.event_type == EventType.OTHER
    )
    if event.event_type != EventType.FINANCIAL_RESULTS and not classify_manual:
        raise FinancialProcessingError("Event is not a financial-results disclosure")
    if event.data_origin == DataOrigin.FIXTURE:
        raise FinancialProcessingError("Fixture financial results cannot be processed")
    document = db.scalar(
        select(Document)
        .where(
            Document.event_id == event.id,
            Document.processing_status == ProcessingStatus.PROCESSED,
            Document.local_path.is_not(None),
        )
        .order_by(Document.id)
    )
    if document is None:
        raise FinancialProcessingError("Event has no processed PDF document")
    if event.data_origin == DataOrigin.MANUAL and not _manual_company_matches(
        db, event, document
    ):
        _record_run(
            db,
            event.id,
            document.id,
            FinancialStatus.NEEDS_REVIEW,
            "Uploaded PDF does not identify the selected company",
        )
        return {
            "event_id": event.id,
            "document_id": document.id,
            "status": "NEEDS_REVIEW",
            "periods_created": 0,
            "periods": [],
            "note": "Company identity requires review",
        }
    storage = storage or LocalDocumentStorage(settings.document_storage_path)
    try:
        path = storage.resolve(document.local_path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != document.sha256_hash:
            raise FinancialProcessingError(
                "Stored PDF hash does not match document metadata"
            )
        stored_pages = set(
            db.scalars(
                select(DocumentPage.page_number).where(
                    DocumentPage.document_id == document.id
                )
            ).all()
        )
        if not stored_pages:
            raise FinancialProcessingError("Document has no extracted page records")
        extracted = extract_pdf_tables(path)
        if any(
            fact.page not in stored_pages
            for period in extracted
            for fact in period.facts.values()
        ):
            raise FinancialProcessingError(
                "Financial fact page is missing from document_pages"
            )
    except (OSError, ValueError, RuntimeError) as exc:
        _record_run(
            db, event.id, document.id, FinancialStatus.FAILED, type(exc).__name__
        )
        raise FinancialProcessingError(
            "Financial PDF cannot be processed; failure recorded"
        ) from exc
    if not extracted:
        _record_run(
            db,
            event.id,
            document.id,
            FinancialStatus.NEEDS_REVIEW,
            "No financial table with readable scope, units, and period headers",
        )
        if classify_manual:
            raise FinancialProcessingError(
                "PDF has no readable financial-results tables"
            )
        return {
            "event_id": event.id,
            "document_id": document.id,
            "status": "NEEDS_REVIEW",
            "periods_created": 0,
            "periods": [],
            "note": "No reliable financial table found",
        }
    if event.data_origin in {DataOrigin.MANUAL, DataOrigin.LIVE}:
        declared = re.search(
            r"\bQ([1-4])\s*FY\s*(20\d{2}|\d{2})\b", event.title, re.IGNORECASE
        )
        quarterly = [
            raw.identity for raw in extracted if raw.identity.quarter is not None
        ]
        if declared and (
            not quarterly
            or (
                max(quarterly, key=lambda item: item.end).quarter,
                max(quarterly, key=lambda item: item.end).fiscal_year[-2:],
            )
            != (int(declared.group(1)), declared.group(2)[-2:])
        ):
            _record_run(
                db,
                event.id,
                document.id,
                FinancialStatus.NEEDS_REVIEW,
                "Uploaded title and PDF reporting period disagree",
            )
            return {
                "event_id": event.id,
                "document_id": document.id,
                "status": "NEEDS_REVIEW",
                "periods_created": 0,
                "periods": [],
                "note": "Reporting period requires review",
            }
        if event.period_end_date and quarterly and max(quarterly, key=lambda item: item.end).end != event.period_end_date:
            _record_run(db, event.id, document.id, FinancialStatus.NEEDS_REVIEW,
                        "Authoritative filing metadata and PDF reporting period disagree")
            return {"event_id": event.id, "document_id": document.id, "status": "NEEDS_REVIEW",
                    "periods_created": 0, "periods": [], "note": "Reporting period conflicts with source metadata"}
        if event.reporting_basis and event.reporting_basis.value != "BOTH" and not any(raw.scope.value == event.reporting_basis.value for raw in extracted):
            _record_run(db, event.id, document.id, FinancialStatus.NEEDS_REVIEW,
                        "Filing metadata and PDF reporting basis disagree")
            return {"event_id": event.id, "document_id": document.id, "status": "NEEDS_REVIEW",
                    "periods_created": 0, "periods": [], "note": "Reporting basis conflicts with source metadata"}
    if classify_manual:
        event.event_type = EventType.FINANCIAL_RESULTS
        db.commit()
    created = 0
    results = []
    conflict = False
    for raw in extracted:
        try:
            period, is_new = _store_period(db, event, document, raw)
            from app.services.result_sources import assess_stored_period
            db.flush()
            assess_stored_period(db, period)
            db.commit()
            created += int(is_new)
            results.append(
                {
                    "id": period.id,
                    "period_end": period.period_end.isoformat(),
                    "period_type": period.period_type.value,
                    "scope": period.scope.value,
                    "status": period.status.value,
                    "facts": len(period.facts),
                    "note": period.status_note,
                }
            )
            logger.info(
                "financials.period_processed",
                extra={
                    "event_id": event.id,
                    "document_id": document.id,
                    "period_end": str(period.period_end),
                    "scope": period.scope.value,
                    "status": period.status.value,
                    "facts": len(period.facts),
                },
            )
        except FinancialProcessingError as exc:
            conflict = True
            logger.warning(
                "financials.period_conflict",
                extra={
                    "event_id": event.id,
                    "document_id": document.id,
                    "error": str(exc),
                },
            )
            results.append(
                {
                    "period_end": raw.identity.end.isoformat(),
                    "scope": raw.scope.value,
                    "status": "NEEDS_REVIEW",
                    "note": str(exc),
                }
            )
    calculate_company_metrics(db, event.company_id)
    status = (
        FinancialStatus.NEEDS_REVIEW
        if conflict or any(row["status"] != "VALIDATED" for row in results)
        else FinancialStatus.VALIDATED
    )
    _record_run(
        db,
        event.id,
        document.id,
        status,
        (
            "Some table columns require review"
            if status == FinancialStatus.NEEDS_REVIEW
            else None
        ),
    )
    from app.services.quarterly_review import refresh_quarterly_reviews
    refresh_quarterly_reviews(db, event.company_id)
    return {
        "event_id": event.id,
        "document_id": document.id,
        "status": status.value,
        "periods_created": created,
        "periods": results,
    }
