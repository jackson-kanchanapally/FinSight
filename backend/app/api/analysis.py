from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.analyst.schemas import AnalysisRead
from app.ai.analyst.service import analyze_period
from app.ai.events.schemas import EventAnalysisRead
from app.ai.events.service import analyze_event
from app.database import get_db
from app.models.analysis import AIAnalysis
from app.models.company import Company
from app.models.event import CompanyEvent
from app.models.financial import FinancialPeriod

router = APIRouter(tags=["analysis"])


@router.post("/api/events/{event_id}/analyze", response_model=EventAnalysisRead)
def analyze_company_event(
    event_id: int, regenerate: bool = False, db: Session = Depends(get_db)
):
    event = db.get(CompanyEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return analyze_event(db, event, regenerate=regenerate)


def _company(db: Session, symbol: str) -> Company:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.post(
    "/api/companies/{symbol}/financials/{period_id}/analyze",
    response_model=AnalysisRead,
)
def analyze(
    symbol: str,
    period_id: int,
    regenerate: bool = False,
    force: bool = False,
    deterministic_only: bool = False,
    db: Session = Depends(get_db),
) -> AIAnalysis:
    company = _company(db, symbol)
    period = db.get(FinancialPeriod, period_id)
    if period is None or period.company_id != company.id:
        raise HTTPException(status_code=404, detail="Financial period not found")
    try:
        return analyze_period(
            db,
            company,
            period,
            regenerate=regenerate,
            force=force,
            deterministic_only=deterministic_only,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/companies/{symbol}/analysis", response_model=list[AnalysisRead])
def company_analyses(symbol: str, db: Session = Depends(get_db)) -> list[AIAnalysis]:
    company = _company(db, symbol)
    return list(
        db.scalars(
            select(AIAnalysis)
            .where(AIAnalysis.company_id == company.id)
            .order_by(AIAnalysis.created_at.desc(), AIAnalysis.id.desc())
        )
    )


@router.get("/api/analysis/{analysis_id}", response_model=AnalysisRead)
def analysis_detail(analysis_id: int, db: Session = Depends(get_db)) -> AIAnalysis:
    record = db.get(AIAnalysis, analysis_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return record
