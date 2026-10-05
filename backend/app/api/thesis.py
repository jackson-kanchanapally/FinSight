"""Company-scoped thesis definitions, proposals and immutable review snapshots."""
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.analysis import _company
from app.database import get_db
from app.models.analysis import AIAnalysis
from app.models.financial import FinancialPeriod, MetricCode, FinancialUnit
from app.models.thesis import ThesisEvaluationRun
from app.schemas.thesis import PillarInput, ExpectationInput, BootstrapRequest, EvaluateRequest
from app.services.thesis import thesis_view, save_pillar, save_expectation, bootstrap
from app.services.thesis_evaluation import evaluate_thesis, run_view
from app.services.thesis_evidence import analysis_evidence
from app.services.thesis_presentation import present_thesis

router = APIRouter(tags=["thesis"])
PREFIX = "/api/companies/{symbol}/thesis"


def mutation(db, operation):
    try:
        result = operation()
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409 if "VERSION_CONFLICT" in str(exc) else 422, str(exc)) from exc


@router.get(PREFIX)
def current(symbol: str, scope: Literal["CONSOLIDATED", "STANDALONE"] = "CONSOLIDATED",
            as_of_date: date | None = None, db: Session = Depends(get_db)):
    company = _company(db, symbol)
    as_of = as_of_date or date.today()
    if as_of_date is None:
        latest = db.execute(select(AIAnalysis, FinancialPeriod).join(
            FinancialPeriod, AIAnalysis.financial_period_id == FinancialPeriod.id).where(
            AIAnalysis.company_id == company.id, FinancialPeriod.scope == scope,
            AIAnalysis.status.in_(["SUCCESS", "VERIFIED_FALLBACK"])
        ).order_by(FinancialPeriod.period_end.desc(), AIAnalysis.id.desc()).limit(1)).first()
        if latest:
            from app.services.thesis_evaluation import refresh_after_review
            # Existing research predates the automatic workflow. One idempotent
            # refresh builds its first thesis and evaluation on current reads.
            refresh_after_review(db, latest[0])
    result = thesis_view(db, company.id, scope, as_of)
    analyses, checkpoints = [], {}
    for analysis, period in db.execute(select(AIAnalysis, FinancialPeriod).join(
            FinancialPeriod, AIAnalysis.financial_period_id == FinancialPeriod.id).where(
            AIAnalysis.company_id == company.id, FinancialPeriod.scope == scope,
            AIAnalysis.status.in_(["SUCCESS", "VERIFIED_FALLBACK"])
    ).order_by(FinancialPeriod.period_end.desc(), AIAnalysis.id.desc())):
        # Historical read never offers an analysis created after the requested date.
        if analysis.created_at.date() > as_of:
            continue
        try:
            _, _, cutoff, registry, _, _ = analysis_evidence(db, company.id, analysis.id, as_of)
        except ValueError:
            continue
        analyses.append({"id": analysis.id, "label": f"{period.fiscal_year} Q{period.quarter or 'annual'} · {analysis.status}",
                         "evidence_as_of": cutoff.isoformat()})
        for key, row in registry.items():
            if row["kind"] == "CHECKPOINT_RECORD":
                checkpoints.setdefault(row["id"], {"id": row["id"], "description": row.get("commitment", row.get("metric", key))})
    latest_run = db.scalar(select(ThesisEvaluationRun).where(
        ThesisEvaluationRun.company_id == company.id, ThesisEvaluationRun.scope == scope,
        ThesisEvaluationRun.as_of_date <= as_of).order_by(ThesisEvaluationRun.id.desc()))
    source_analysis = db.get(AIAnalysis, latest_run.analysis_id) if latest_run else None
    source_period = db.get(FinancialPeriod, source_analysis.financial_period_id) if source_analysis else None
    overview = present_thesis(result, analysis=source_analysis, period=source_period,
                              registry=latest_run.evidence_registry if latest_run else {})
    return {**result, "overview": overview, "available_analyses": analyses, "checkpoint_options": list(checkpoints.values()),
            "metric_options": [v.value for v in MetricCode], "unit_options": [v.value for v in FinancialUnit]}


@router.post(PREFIX + "/bootstrap")
def propose(symbol: str, body: BootstrapRequest, db: Session = Depends(get_db)):
    company = _company(db, symbol)
    try:
        return bootstrap(db, company.id, body.analysis_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(PREFIX + "/pillars")
def create(symbol: str, body: PillarInput, db: Session = Depends(get_db)):
    company = _company(db, symbol)
    return mutation(db, lambda: {"id": save_pillar(db, company.id, body).id})


@router.put(PREFIX + "/pillars/{pillar_id}")
def edit(symbol: str, pillar_id: int, body: PillarInput, db: Session = Depends(get_db)):
    company = _company(db, symbol)
    return mutation(db, lambda: {"id": save_pillar(db, company.id, body, pillar_id).id})


@router.post(PREFIX + "/pillars/{pillar_id}/expectations")
def add_test(symbol: str, pillar_id: int, body: ExpectationInput, db: Session = Depends(get_db)):
    company = _company(db, symbol)
    return mutation(db, lambda: {"id": save_expectation(db, company.id, pillar_id, body).expectation_key})


@router.put(PREFIX + "/pillars/{pillar_id}/expectations/{expectation_key}")
def edit_test(symbol: str, pillar_id: int, expectation_key: str, body: ExpectationInput, db: Session = Depends(get_db)):
    company = _company(db, symbol)
    return mutation(db, lambda: {"id": save_expectation(db, company.id, pillar_id, body, expectation_key).expectation_key})


@router.post(PREFIX + "/evaluate")
def evaluate(symbol: str, body: EvaluateRequest, db: Session = Depends(get_db)):
    company = _company(db, symbol)
    return mutation(db, lambda: evaluate_thesis(db, company.id, body.analysis_id, use_ai=body.use_ai))


@router.get("/api/analysis/{analysis_id}/thesis-impact")
def impact(analysis_id: int, db: Session = Depends(get_db)):
    if not db.get(AIAnalysis, analysis_id):
        raise HTTPException(404, "Analysis not found")
    run = db.scalar(select(ThesisEvaluationRun).where(ThesisEvaluationRun.analysis_id == analysis_id)
                    .order_by(ThesisEvaluationRun.id.desc()))
    return run_view(db, run) if run else {"run_id": None, "analysis_id": analysis_id, "impacts": []}
