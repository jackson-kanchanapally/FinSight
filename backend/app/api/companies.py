import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.ingestion.storage import LocalDocumentStorage
from app.models import (
    AIAnalysis,
    Company,
    CompanyEvent,
    CompanySource,
    Document,
    DocumentPage,
    EventReference,
    FinancialFact,
    FinancialFactCorrection,
    FinancialMetric,
    FinancialPeriod,
    FinancialProcessingRun,
    EventAnalysis,
    MarketSnapshot,
    ValuationSnapshot,
    Transaction,
)
from app.schemas.company import CompanyCreate, CompanyRead

router = APIRouter(prefix="/api/companies", tags=["companies"])
logger = logging.getLogger(__name__)


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
def create_company(payload: CompanyCreate, db: Session = Depends(get_db)) -> Company:
    company = Company(**payload.model_dump())
    db.add(company)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Company symbol already exists"
        ) from exc
    db.refresh(company)
    return company


@router.get("", response_model=list[CompanyRead])
def list_companies(db: Session = Depends(get_db)) -> list[Company]:
    return list(db.scalars(select(Company).order_by(Company.symbol)).all())


@router.get("/{symbol}", response_model=CompanyRead)
def company_by_symbol(symbol: str, db: Session = Depends(get_db)) -> Company:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company(symbol: str, db: Session = Depends(get_db)) -> None:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")

    company_id = company.id
    company_symbol = company.symbol
    document_paths = {
        path
        for path in db.scalars(
            select(Document.local_path).where(
                Document.company_id == company_id, Document.local_path.is_not(None)
            )
        )
        if path
    }
    period_ids = select(FinancialPeriod.id).where(
        FinancialPeriod.company_id == company_id
    )
    fact_ids = select(FinancialFact.id).where(
        FinancialFact.financial_period_id.in_(period_ids)
    )
    event_ids = select(CompanyEvent.id).where(CompanyEvent.company_id == company_id)
    document_ids = select(Document.id).where(Document.company_id == company_id)
    try:
        # Remove related rows in dependency order, including with SQLite foreign keys off.
        # Trades use RESTRICT; the other rows also have database CASCADE as a safeguard.
        db.execute(delete(AIAnalysis).where(AIAnalysis.company_id == company_id))
        db.execute(
            delete(ValuationSnapshot).where(ValuationSnapshot.company_id == company_id)
        )
        db.execute(
            delete(FinancialFactCorrection).where(
                FinancialFactCorrection.fact_id.in_(fact_ids)
            )
        )
        db.execute(
            delete(FinancialMetric).where(
                FinancialMetric.financial_period_id.in_(period_ids)
            )
        )
        db.execute(
            update(FinancialMetric)
            .where(FinancialMetric.comparison_period_id.in_(period_ids))
            .values(comparison_period_id=None)
        )
        db.execute(
            delete(FinancialFact).where(
                FinancialFact.financial_period_id.in_(period_ids)
            )
        )
        db.execute(
            delete(FinancialPeriod).where(FinancialPeriod.company_id == company_id)
        )
        db.execute(
            delete(FinancialProcessingRun).where(
                FinancialProcessingRun.source_event_id.in_(event_ids)
            )
        )
        db.execute(
            delete(DocumentPage).where(DocumentPage.document_id.in_(document_ids))
        )
        db.execute(delete(Document).where(Document.company_id == company_id))
        db.execute(delete(EventAnalysis).where(EventAnalysis.company_id == company_id))
        db.execute(delete(EventReference).where(EventReference.event_id.in_(event_ids)))
        db.execute(delete(CompanyEvent).where(CompanyEvent.company_id == company_id))
        db.execute(delete(CompanySource).where(CompanySource.company_id == company_id))
        db.execute(
            delete(MarketSnapshot).where(MarketSnapshot.company_id == company_id)
        )
        db.execute(delete(Transaction).where(Transaction.company_id == company_id))
        db.execute(delete(Company).where(Company.id == company_id))
        db.commit()
    except Exception:
        db.rollback()
        raise

    # PDFs are content-addressed and may also belong to another company's event.
    storage = LocalDocumentStorage(settings.document_storage_path)
    for path in document_paths:
        try:
            if (
                db.scalar(
                    select(Document.id).where(Document.local_path == path).limit(1)
                )
                is not None
            ):
                continue
            storage.delete(path)
        except Exception:
            logger.warning(
                "company.delete_document_failed",
                extra={"symbol": company_symbol, "path": path},
                exc_info=True,
            )
