"""Read immutable evaluation history without replacing primary scores on correction."""
from collections import defaultdict
from datetime import datetime
from sqlalchemy import select
from app.models.expectations import ExpectationEvaluation as Evaluation
from app.services.expectation_evidence import utc
from app.services.forecast_scoring import SCORED, VERSION


def primary_candidate(revision, evaluation):
    result = evaluation.result
    published = result.get("outcome_published_at")
    if (not evaluation.prospective_eligible or revision.definition.get("historical_import")
            or evaluation.outcome not in SCORED or result.get("actual_value") is None or not published):
        return False
    published = utc(datetime.fromisoformat(published))
    return utc(revision.created_at) < utc(revision.cutoff_at) <= published and utc(revision.created_at) < published


def has_corrections(evaluation):
    return any(item.get("corrections") for item in evaluation.result.get("evidence", {}).values())


def observation_history(db, revisions):
    """First comparable uncorrected evaluation is immutable primary evidence.

    If the first scored snapshot already contains corrections, the original
    actual cannot safely be reconstructed from correction values alone: those
    rows do not retain original units, status or source-page context. Refuse a
    primary score rather than pretend a revised value was first published.
    """
    histories = defaultdict(list)
    for row in db.scalars(select(Evaluation).where(Evaluation.revision_id.in_(revisions))
                          .order_by(Evaluation.id)):
        histories[row.revision_id].append(row)
    primary = {}
    for key, rows in histories.items():
        candidates = [e for e in rows if primary_candidate(revisions[key], e)]
        if candidates and not has_corrections(candidates[0]):
            primary[key] = candidates[0]
    return primary, histories


def match_diagnostics(db, ledger, period, actual, registry, result, revision):
    """Freeze exclusions and match identity with each new evaluation state."""
    from app.models.financial import FinancialPeriod, FinancialFact
    from app.services.financials.period import identify_period
    from app.services.expectation_math import canonical_unit
    identity = identify_period(ledger.period_end, ledger.period_type)
    reason = "MATCHED"
    if revision.definition.get("historical_import"):
        reason = "HISTORICAL_CAPTURE_UNVERIFIED"
    elif result.get("reason") == "Expectation cutoff has not been reached":
        reason = "CUTOFF_PENDING"
    elif result.get("outcome") == "NOT_COMPARABLE":
        reason = "UNIT_MISMATCH" if actual and actual["unit"] != canonical_unit(ledger.metric) else "PUBLICATION_OR_DEFINITION_MISMATCH"
    elif not period:
        candidates = list(db.scalars(select(FinancialPeriod).where(FinancialPeriod.company_id == ledger.company_id,
                            FinancialPeriod.period_end == ledger.period_end)))
        if not candidates:
            reason = "ACTUAL_NOT_AVAILABLE"
        elif not any(p.scope == ledger.scope for p in candidates):
            reason = "ACCOUNTING_SCOPE_MISMATCH"
        elif not any(p.scope == ledger.scope and p.period_type == ledger.period_type for p in candidates):
            reason = "FISCAL_PERIOD_TYPE_MISMATCH"
        else:
            reason = "ACTUAL_PERIOD_NOT_VALIDATED"
    elif not actual:
        fact = db.scalar(select(FinancialFact).where(FinancialFact.financial_period_id == period.id,
                                                    FinancialFact.metric_code == ledger.metric))
        reason = "ACTUAL_SOURCE_OR_DEFINITION_INVALID" if fact else "METRIC_ACTUAL_MISSING"
    elif result.get("withdrawn"):
        reason = "WITHDRAWN"
    result.update(scoring_version=VERSION, reason_code=reason,
                  match_identity={"company_id": ledger.company_id, "metric": ledger.metric,
                                  "period_start": identity.start.isoformat(), "period_end": identity.end.isoformat(),
                                  "fiscal_year": identity.fiscal_year, "period_type": ledger.period_type,
                                  "scope": ledger.scope, "reporting_basis": revision.definition["reporting_basis"],
                                  "currency": revision.definition["currency"]},
                  actual_snapshot=actual, information_cutoff=revision.definition.get("forecast", {}).get(
                      "information_cutoff", utc(revision.created_at).isoformat()))
    return result
