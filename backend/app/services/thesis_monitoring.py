"""Synchronous, bounded monitoring at existing ingestion transaction boundaries."""
from datetime import date
from decimal import Decimal
import logging
from sqlalchemy import select
from app.models.analysis import AIAnalysis
from app.models.thesis import ThesisPillar, ThesisEvaluationRun
from app.services.thesis import lock_company
from app.services.thesis_evidence import analysis_evidence, day
from app.services.thesis_evaluation import evaluate_thesis, run_view
from app.services.thesis_monitoring_evidence import collect_evidence, digest

logger = logging.getLogger(__name__)


def monitor_company(db, company_id, *, scope=None, today=None):
    """Caller commits. PostgreSQL's company row lock serializes all thesis writers.

    The unique evidence fingerprint is a second guard. SQLite development writers
    are serialized with BEGIN IMMEDIATE when the transaction has not yet written.
    """
    today = today or date.today()
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        raw = connection.connection.driver_connection
        if not raw.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    lock_company(db, company_id)
    scopes = set(db.scalars(select(ThesisPillar.scope).where(ThesisPillar.company_id == company_id)))
    results = []
    for current_scope in sorted(scopes):
        if scope and scope != current_scope:
            continue
        evidence = collect_evidence(db, company_id, current_scope, today)
        if evidence is None:
            continue
        period, cutoff, registry = evidence
        analysis = db.scalar(select(AIAnalysis).where(AIAnalysis.company_id == company_id,
            AIAnalysis.financial_period_id == period.id,
            AIAnalysis.status.in_(["SUCCESS", "VERIFIED_FALLBACK"])).order_by(AIAnalysis.id.desc()))
        report = {}
        if analysis and day(analysis.created_at) <= today:
            try:
                _, _, _, saved, candidate, _ = analysis_evidence(db, company_id, analysis.id, today)
                # Saved valuation context cannot carry superseded financial values.
                facts = {k: v for k, v in saved.items() if v.get("kind") == "FACT"}
                if facts and all(k in registry and Decimal(registry[k]["value"]) == Decimal(v["value"]) for k, v in facts.items()):
                    report = candidate
                    registry.update({k: {**v, "revision": digest(v)} for k, v in saved.items() if k == "A_BASE"})
            except ValueError:
                pass
        else:
            analysis = None
        context = (analysis, period, cutoff, registry, report, [])
        results.append(evaluate_thesis(db, company_id, analysis.id if analysis else None,
            use_ai=False, today=today, monitoring_context=context))
    return results


def refresh_monitoring(db, company_id):
    """Called after evidence commits; failures never roll back successful ingestion."""
    from app.services.expectations import refresh_expectations
    refresh_expectations(db, company_id)
    from app.services.research_jobs import enqueue_research
    enqueue_research(db, company_id)
    db.commit()
    try:
        # Start the SQLite writer transaction before the SAVEPOINT as well.
        if db.get_bind().dialect.name == "sqlite":
            connection = db.connection()
            if not connection.connection.driver_connection.in_transaction:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
        with db.begin_nested():
            result = monitor_company(db, company_id)
        db.commit()
        return result
    except Exception:
        db.rollback()
        logger.exception("thesis.monitoring_failed company_id=%s", company_id)
        return []


def monitoring_view(db, company_id, scope, as_of):
    runs = list(db.scalars(select(ThesisEvaluationRun).where(ThesisEvaluationRun.company_id == company_id,
        ThesisEvaluationRun.scope == scope, ThesisEvaluationRun.as_of_date <= as_of
    ).order_by(ThesisEvaluationRun.id.desc())))
    monitored = [run for run in runs if run.assessment]
    return {"latest": run_view(db, monitored[0]) if monitored else None,
        "history": [{"run_id": r.id, "recorded_at": r.created_at.isoformat(),
            "previous_run_id": r.previous_run_id, "summary": (r.assessment or {}).get("summary", "Saved thesis evaluation"),
            "confidence": (r.assessment or {}).get("confidence")} for r in runs]}


def compare_assessments(db, company_id, scope, first_id, second_id):
    from app.services.thesis_changes import detect_changes, historical_registry
    first, second = db.get(ThesisEvaluationRun, first_id), db.get(ThesisEvaluationRun, second_id)
    if not all(r and r.company_id == company_id and r.scope == scope for r in (first, second)):
        raise ValueError("ASSESSMENT_NOT_FOUND")
    # Both directions are supported; this compares stored evidence only.
    return {"from": run_view(db, first), "to": run_view(db, second),
        "changes": detect_changes(first.evidence_registry, second.evidence_registry, first.id),
        "evidence": {**historical_registry(first.evidence_registry, first.id), **second.evidence_registry}}
