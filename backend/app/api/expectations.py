"""Company-scoped expectation ledger, following the existing local API access model."""
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.analysis import _company
from app.database import get_db
from app.models.expectations import HistoricalExpectationRevision as Revision, ExpectationEvaluation as Evaluation
from app.schemas.expectations import ExpectationInput, ExpectationRead, ExpectationPage, EvaluationRead, StatisticsRead, Scope, Source, CalibrationRead, ThesisHistoryRead, RetryRead, GuidancePage
from app.services import expectations as service
from app.services.expectation_statistics import statistics, thesis_history
from app.services.checkpoint_guidance import guidance_page

router = APIRouter(tags=["historical accuracy"])
PREFIX = "/api/companies/{symbol}/expectations"


def mutation(db, fn):
    try:
        result = fn()
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        message = str(exc)
        raise HTTPException(404 if "NOT_FOUND" in message else 409 if "CONFLICT" in message or "DUPLICATE" in message else 422, message) from exc


@router.post(PREFIX, response_model=ExpectationRead, status_code=201)
def create(symbol: str, body: ExpectationInput, db: Session = Depends(get_db)):
    company = _company(db, symbol)
    return mutation(db, lambda: service.view(*service.save(db, company.id, body)))


@router.post(PREFIX + "/{expectation_id}/revisions", response_model=ExpectationRead, status_code=201)
def revise(symbol: str, expectation_id: int, body: ExpectationInput, db: Session = Depends(get_db)):
    company = _company(db, symbol)
    return mutation(db, lambda: service.view(*service.save(db, company.id, body, expectation_id)))


@router.get(PREFIX, response_model=ExpectationPage)
def listing(symbol: str, scope: Scope | None = None, period: date | None = None,
            metric: str | None = Query(None, max_length=60), source: Source | None = None,
            offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    return service.list_expectations(db, _company(db, symbol).id, scope=scope, period=period, metric=metric,
                                     source=source, offset=offset, limit=limit)


@router.get(PREFIX + "/{expectation_id}/revisions", response_model=list[ExpectationRead])
def revisions(symbol: str, expectation_id: int, offset: int = Query(0, ge=0),
              limit: int = Query(200, ge=1, le=200), db: Session = Depends(get_db)):
    try:
        return service.revisions_view(db, _company(db, symbol).id, expectation_id, offset=offset, limit=limit)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post(PREFIX + "/{expectation_id}/evaluate", response_model=EvaluationRead)
def evaluate(symbol: str, expectation_id: int, db: Session = Depends(get_db)):
    company = _company(db, symbol)
    return mutation(db, lambda: service.evaluation_view(service.evaluate(db, company.id, expectation_id)))


@router.post(PREFIX + "/evaluate-pending", response_model=RetryRead)
def retry(symbol: str, db: Session = Depends(get_db)):
    count = service.refresh_expectations(db, _company(db, symbol).id)
    if count is None:
        raise HTTPException(503, "Evaluation failed; persisted evidence is safe. Retry evaluation.")
    return {"processed": count}


@router.get(PREFIX + "/{expectation_id}/results", response_model=list[EvaluationRead])
def results(symbol: str, expectation_id: int, offset: int = Query(0, ge=0),
            limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    try:
        service.owned(db, _company(db, symbol).id, expectation_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return [service.evaluation_view(row) for row in db.scalars(select(Evaluation).join(Revision).where(
        Revision.expectation_id == expectation_id).order_by(Evaluation.id.desc()).offset(offset).limit(limit))]


@router.get("/api/companies/{symbol}/accuracy", response_model=StatisticsRead)
def company_accuracy(symbol: str, scope: Scope | None = None, period: date | None = None,
        metric: str | None = None, source: Source | None = None, db: Session = Depends(get_db)):
    return statistics(db, _company(db, symbol).id, scope=scope, period=period, metric=metric, source=source)


@router.get("/api/accuracy", response_model=StatisticsRead)
def system_accuracy(db: Session = Depends(get_db)):
    return statistics(db)


@router.get("/api/companies/{symbol}/accuracy/calibration", response_model=CalibrationRead)
def confidence_calibration(symbol: str, scope: Scope | None = None, db: Session = Depends(get_db)):
    stats = statistics(db, _company(db, symbol).id, scope=scope)
    return {"items": stats["calibration"], "minimum_n": stats["policy"]["minimum_n"]}


@router.get("/api/companies/{symbol}/accuracy/guidance", response_model=GuidancePage)
def guidance(symbol: str, scope: Scope | None = None, offset: int = Query(0, ge=0),
             limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    return guidance_page(db, _company(db, symbol).id, scope=scope, offset=offset, limit=limit)


@router.get("/api/companies/{symbol}/accuracy/thesis-history", response_model=ThesisHistoryRead)
def thesis(symbol: str, scope: Scope | None = None, offset: int = Query(0, ge=0),
           limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    return thesis_history(db, _company(db, symbol).id, scope=scope, offset=offset, limit=limit)
