"""Company, scope and publication boundaries for saved thesis evidence."""
from datetime import date
from decimal import Decimal
from app.models.analysis import AIAnalysis
from app.models.commentary import ManagementStatement, ManagementGuidance
from app.models.checkpoint import InvestorCheckpoint
from app.models.event import CompanyEvent, Document
from app.models.financial import FinancialFact, FinancialPeriod, FinancialStatus
from app.models.market import MarketSnapshot
from app.services.memory.service import local_day

VERIFIED = {"VERIFIED_PDF", "VERIFIED_STRUCTURED", "MANUALLY_VERIFIED"}


def day(value):
    if not value:
        return None
    if isinstance(value, date):
        return value if not hasattr(value, "hour") else local_day(value)
    return date.fromisoformat(str(value)[:10])


def analysis_evidence(db, company_id, analysis_id, as_of):
    analysis = db.get(AIAnalysis, analysis_id)
    if not analysis or analysis.company_id != company_id:
        raise ValueError("ANALYSIS_COMPANY_MISMATCH")
    if analysis.status not in {"SUCCESS", "VERIFIED_FALLBACK"} or not analysis.structured_output:
        raise ValueError("COMPLETED_REVIEW_REQUIRED")
    if day(analysis.created_at) > as_of:
        raise ValueError("ANALYSIS_NOT_AVAILABLE_AS_OF")
    period = db.get(FinancialPeriod, analysis.financial_period_id)
    event = db.get(CompanyEvent, period.source_event_id)
    if period.company_id != company_id or not event or event.company_id != company_id or day(event.published_at) > as_of or period.period_end > as_of:
        raise ValueError("ANALYSIS_NOT_AVAILABLE_AS_OF")
    report = analysis.structured_output.get("investor_report") or {}
    raw = analysis.evidence_registry or {}
    dates = [day(event.published_at)]
    # Derive the documentary cutoff only from real sources of this saved period.
    for key, row in raw.items():
        if row.get("kind") == "MANAGEMENT_STATEMENT" and key[1:].isdigit():
            statement = db.get(ManagementStatement, int(key[1:]))
            if statement and statement.company_id == company_id and statement.financial_period_id == period.id:
                dates.append(day(statement.published_at))
    cutoff = min(as_of, day(analysis.created_at), max(dates))
    scope = period.scope.value
    cache, visiting = {}, set()

    def owner_ok(owner):
        if not owner or owner.company_id != company_id or owner.scope.value != scope:
            return False
        source = db.get(CompanyEvent, owner.source_event_id)
        return (owner.period_end <= period.period_end and source is not None
                and day(source.published_at) <= cutoff and owner.status == FinancialStatus.VALIDATED)

    def permitted(key):
        if key in cache:
            return cache[key]
        if key in visiting or key not in raw:
            return False
        visiting.add(key)
        row = raw[key]
        kind = row.get("kind")
        valid = False
        if kind == "FACT" and key[1:].isdigit():
            fact = db.get(FinancialFact, int(key[1:]))
            valid = bool(fact and fact.status.value in VERIFIED and
                         row.get("period_id") == fact.financial_period_id and
                         row.get("metric_code") == fact.metric_code.value and
                         row.get("unit") == fact.unit.value and
                         Decimal(str(row.get("value"))) == fact.value and
                         owner_ok(db.get(FinancialPeriod, fact.financial_period_id)))
        elif kind == "METRIC":
            owner = db.get(FinancialPeriod, row.get("period_id")) if row.get("period_id") else None
            inputs = row.get("input_evidence_ids", [])
            valid = bool(owner_ok(owner) and inputs and all(permitted(k) for k in inputs))
        elif kind == "MANAGEMENT_STATEMENT" and key[1:].isdigit():
            statement = db.get(ManagementStatement, int(key[1:]))
            valid = bool(statement and statement.company_id == company_id and
                         day(statement.published_at) <= cutoff and
                         owner_ok(db.get(FinancialPeriod, statement.financial_period_id)))
        elif kind == "CHECKPOINT_RECORD":
            checkpoint = db.get(InvestorCheckpoint, row.get("id"))
            outcome_ids = row.get("actual_outcome", {}).get("evidence_ids", [])
            valid = bool(checkpoint and checkpoint.company_id == company_id
                and owner_ok(db.get(FinancialPeriod, checkpoint.created_period_id))
                and day(row.get("evaluated_as_of")) is not None
                and day(row["evaluated_as_of"]) <= cutoff
                and permitted(row.get("source_statement_id", ""))
                and all(permitted(k) for k in outcome_ids)
                and (row.get("status") not in {"DELIVERED", "MISSED"} or outcome_ids))
        elif kind == "MEMORY_CHUNK":
            document = db.get(Document, row.get("document_id")) if row.get("document_id") else None
            source = db.get(CompanyEvent, document.event_id) if document and getattr(document, "event_id", None) else None
            valid = bool(source and source.company_id == company_id and day(source.published_at) <= cutoff and day(row.get("published_at"))
                and day(row["published_at"]) <= cutoff and row.get("scope") in {None, scope})
        elif kind == "MARKET_SNAPSHOT" and key[1:].isdigit():
            market = db.get(MarketSnapshot, int(key[1:]))
            valid = bool(market and market.company_id == company_id and market.as_of_date <= cutoff)
        elif kind in {"VALUATION", "SCENARIO_ASSUMPTION", "VALUATION_SCENARIO"} or key.startswith("A_"):
            market_date = day(report.get("tested_pe", {}).get("market_date"))
            valid = bool(market_date and market_date <= cutoff and
                         all(permitted(k) for k in row.get("input_evidence_ids", [])))
        elif key == "I_REVIEW":
            valid = True
        visiting.remove(key)
        cache[key] = valid
        return valid

    registry = {key: {**row, "company_id": company_id, "scope": scope}
                for key, row in raw.items() if permitted(key)}
    # Narrative is not an alternative route for filtered future evidence.
    review = analysis.structured_output.get("quarterly_review") or report.get("quarterly_review") or {}
    review_rows = []
    for value in review.values():
        for row in value if isinstance(value, list) else [value]:
            if isinstance(row, dict) and row.get("evidence_ids") and all(k in registry for k in row["evidence_ids"]):
                review_rows.append(row)
    return analysis, period, cutoff, registry, report, review_rows


def expectation_gap(report, registry, cutoff):
    """Interpret Phase 6.3 outputs only over matching EPS CAGR horizons."""
    tested = report.get("tested_pe") or {}
    base = next((s for s in tested.get("scenarios", []) if s.get("name") == "BASE"), {})
    required = (tested.get("reverse_expectations") or {}).get("required_eps_cagr_pct")
    result = {"classification": "INSUFFICIENT_EVIDENCE", "required_eps_cagr_pct": None,
              "observed_eps_cagr_pct": None, "horizon_years": base.get("years"),
              "evidence_ids": [], "reason": "A dated, compatible EPS CAGR comparison is unavailable.",
              "policy": {"moderate_gap_pp": "10", "high_gap_pp": "25"},
              "assumption_not_forecast": True}
    if required is None or not base.get("years") or base["years"] <= 0 or "A_BASE" not in registry or not day(tested.get("market_date")) or day(tested["market_date"]) > cutoff:
        return result
    result["required_eps_cagr_pct"] = str(required)
    result["evidence_ids"] = ["A_BASE"]
    evidence = tested.get("growth_evidence", tested.get("growth_comparisons", []))
    compatible = [row for row in evidence if row.get("metric") == "EPS_CAGR"
                  and row.get("years") == base.get("years") and row.get("evidence_ids")
                  and all(k in registry for k in row["evidence_ids"])]
    if not compatible:
        result["reason"] = "Horizon mismatch: quarterly YoY or a different CAGR horizon cannot establish a gap against the scenario horizon."
        return result
    observed = Decimal(compatible[0]["value"])
    if not observed.is_finite() or not Decimal(str(required)).is_finite():
        return result
    difference = Decimal(str(required)) - observed
    result.update(classification="LOW" if difference <= 0 else "MODERATE" if difference <= 10 else
                  "HIGH" if difference <= 25 else "EXTREME",
                  observed_eps_cagr_pct=str(observed), difference_pp=str(difference),
                  evidence_ids=["A_BASE", *compatible[0]["evidence_ids"]],
                  reason="Required scenario EPS CAGR minus verified historical EPS CAGR over the same number of years. Historical delivery does not establish future delivery.")
    return result
