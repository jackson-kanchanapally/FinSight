from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Company
from app.models.event import CompanyEvent, Document
from app.models.financial import (
    FinancialPeriod,
    FinancialProcessingRun,
    FinancialScope,
    FinancialStatus,
)
from app.models.financial import FinancialFact
from app.schemas.financial import (
    CompanyFinancialsRead,
    FinancialFactRead,
    FinancialMetricRead,
    FinancialPeriodRead,
    FinancialProcessingRead,
    FORMULAS,
)
from app.services.financials.service import (
    FinancialProcessingError,
    process_financial_event,
)

router = APIRouter(tags=["financials"])


def _period_query():
    return select(FinancialPeriod).options(
        selectinload(FinancialPeriod.facts), selectinload(FinancialPeriod.metrics)
    )


def _period_read(db: Session, period: FinancialPeriod) -> FinancialPeriodRead:
    event = db.get(CompanyEvent, period.source_event_id)
    document = db.get(Document, period.source_document_id)
    input_ids = {
        fact_id for metric in period.metrics for fact_id in metric.input_fact_ids
    }
    input_facts = (
        {
            fact.id: fact
            for fact in db.scalars(
                select(FinancialFact).where(FinancialFact.id.in_(input_ids))
            ).all()
        }
        if input_ids
        else {}
    )

    def fact_read(fact: FinancialFact) -> FinancialFactRead:
        return FinancialFactRead(
            id=fact.id,
            metric_code=fact.metric_code,
            value=fact.value,
            unit=fact.unit,
            original_value=fact.original_value,
            original_unit=fact.original_unit,
            source_document_id=fact.source_document_id,
            source_page=fact.source_page,
            source_label=fact.source_label,
            source_column_date=fact.source_column_date,
            extraction_method=fact.extraction_method,
            status=fact.status,
            document_url=f"/api/documents/{fact.source_document_id}/file",
        )

    return FinancialPeriodRead(
        id=period.id,
        company_id=period.company_id,
        fiscal_year=period.fiscal_year,
        quarter=period.quarter,
        period_start=period.period_start,
        period_end=period.period_end,
        period_type=period.period_type,
        scope=period.scope,
        status=period.status,
        status_note=period.status_note,
        source_event_id=event.id,
        source_document_id=document.id,
        source_title=event.title,
        source_name=event.source,
        source_url=event.source_url,
        document_url=f"/api/documents/{document.id}/file",
        published_at=event.published_at,
        facts=[
            fact_read(fact)
            for fact in sorted(period.facts, key=lambda f: f.metric_code.value)
        ],
        metrics=[
            FinancialMetricRead(
                metric_code=metric.metric_code,
                value=metric.value,
                unit=metric.unit,
                input_fact_ids=metric.input_fact_ids,
                comparison_period_id=metric.comparison_period_id,
                formula=FORMULAS.get(
                    metric.metric_code, "Calculated from listed source facts"
                ),
                inputs=[
                    fact_read(input_facts[fact_id])
                    for fact_id in metric.input_fact_ids
                    if fact_id in input_facts
                ],
            )
            for metric in sorted(period.metrics, key=lambda m: m.metric_code.value)
        ],
    )


def _company(db: Session, symbol: str) -> Company:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/api/companies/{symbol}/financials", response_model=CompanyFinancialsRead)
def company_financials(
    symbol: str, db: Session = Depends(get_db)
) -> CompanyFinancialsRead:
    company = _company(db, symbol)
    periods = db.scalars(
        _period_query()
        .where(FinancialPeriod.company_id == company.id)
        .order_by(FinancialPeriod.period_end.desc(), FinancialPeriod.id.desc())
    ).all()
    return CompanyFinancialsRead(
        symbol=company.symbol,
        company_name=company.name,
        periods=[_period_read(db, period) for period in periods],
    )


@router.get(
    "/api/companies/{symbol}/financials/latest", response_model=FinancialPeriodRead
)
def latest_financials(
    symbol: str,
    scope: FinancialScope = FinancialScope.CONSOLIDATED,
    db: Session = Depends(get_db),
) -> FinancialPeriodRead:
    company = _company(db, symbol)
    period = db.scalar(
        _period_query()
        .where(
            FinancialPeriod.company_id == company.id,
            FinancialPeriod.scope == scope,
            FinancialPeriod.status == FinancialStatus.VALIDATED,
        )
        .order_by(FinancialPeriod.period_end.desc(), FinancialPeriod.id.desc())
    )
    if period is None:
        raise HTTPException(
            status_code=404, detail="No validated financial period for this scope"
        )
    return _period_read(db, period)


@router.get(
    "/api/companies/{symbol}/financials/{period_id}", response_model=FinancialPeriodRead
)
def financial_period(
    symbol: str, period_id: int, db: Session = Depends(get_db)
) -> FinancialPeriodRead:
    company = _company(db, symbol)
    period = db.scalar(
        _period_query().where(
            FinancialPeriod.company_id == company.id, FinancialPeriod.id == period_id
        )
    )
    if period is None:
        raise HTTPException(status_code=404, detail="Financial period not found")
    return _period_read(db, period)


@router.post(
    "/api/financials/process/{event_id}", response_model=FinancialProcessingRead
)
def process_financials(
    event_id: int, confirm_results: bool = False, db: Session = Depends(get_db)
) -> FinancialProcessingRead:
    try:
        return FinancialProcessingRead.model_validate(
            process_financial_event(db, event_id, confirm_results=confirm_results)
        )
    except FinancialProcessingError as exc:
        detail = str(exc)
        raise HTTPException(
            status_code=404 if detail == "Event not found" else 422, detail=detail
        ) from exc


@router.get("/api/financials/process/{event_id}")
def financial_processing_status(event_id: int, db: Session = Depends(get_db)) -> dict:
    run = db.scalar(
        select(FinancialProcessingRun)
        .where(FinancialProcessingRun.source_event_id == event_id)
        .order_by(FinancialProcessingRun.last_attempted_at.desc())
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Financial processing has not run")
    return {
        "event_id": event_id,
        "document_id": run.source_document_id,
        "status": run.status,
        "note": run.status_note,
        "last_attempted_at": run.last_attempted_at,
    }
