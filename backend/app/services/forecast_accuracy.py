"""Primary forecast accuracy from immutable original predictions and actuals."""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from app.config import settings
from app.models.expectations import HistoricalExpectation as Ledger, HistoricalExpectationRevision as Revision
from app.services.expectation_evidence import utc, utcnow
from app.services.expectation_math import canonical_unit, normalize
from app.services.financials.period import identify_period
from app.services.forecast_observations import observation_history, has_corrections
from app.services.forecast_scoring import VERSION, SCORED, continuous_metrics, calibration

D = Decimal


def observation_rows(db, company_id, scope, *, metric=None, horizon_quarters=None,
                     period_type=None, date_from=None, date_to=None):
    query = select(Ledger, Revision).join(Revision).where(
        Ledger.company_id == company_id, Ledger.scope == scope,
        Ledger.source_type == "AI", Revision.version == 1)
    if metric is not None:
        query = query.where(Ledger.metric == metric)
    if period_type is not None:
        query = query.where(Ledger.period_type == period_type)
    if horizon_quarters is not None:
        query = query.where(Revision.definition["forecast"]["horizon_quarters"].as_integer() == horizon_quarters)
    pairs = db.execute(query.order_by(Ledger.period_end, Ledger.id)).all()
    revisions = {r.id: r for _, r in pairs}
    primary, histories = observation_history(db, revisions)
    output = []
    for ledger, revision in pairs:
        definition = revision.definition
        meta = definition.get("forecast", {})
        history = histories[revision.id]
        first, latest = primary.get(revision.id), history[-1] if history else None
        # Dates filter the first actual publication (target period for unmatched
        # rows); repeated evaluations never move an observation into a new window.
        result = first.result if first else latest.result if latest else {}
        evaluation_day = date.fromisoformat(result["outcome_published_at"][:10]) if result.get("outcome_published_at") else ledger.period_end
        if (date_from and evaluation_day < date_from) or (date_to and evaluation_day > date_to):
            continue
        if definition.get("historical_import"):
            state, reason = "EXCLUDED", "HISTORICAL_CAPTURE_UNVERIFIED"
        elif definition["expectation_type"] != "EXACT":
            state, reason = "EXCLUDED", "POINT_FORECAST_REQUIRED"
        elif first:
            state, reason = "EVALUATED", "FIRST_ELIGIBLE_ACTUAL"
        elif latest and not latest.prospective_eligible:
            state, reason = "EXCLUDED", "CAPTURE_OR_CUTOFF_AFTER_PUBLICATION"
        elif utcnow() < utc(revision.cutoff_at):
            state, reason = "PENDING", "CUTOFF_PENDING"
        elif latest and latest.outcome in SCORED and has_corrections(latest):
            state, reason = "EXCLUDED", "ORIGINAL_ACTUAL_SNAPSHOT_UNAVAILABLE"
        elif latest and latest.outcome == "NOT_COMPARABLE":
            state, reason = "EXCLUDED", latest.result.get("reason_code", "ACTUAL_NOT_COMPARABLE")
        else:
            state, reason = "MISSING_ACTUAL", result.get("reason_code", "EVALUATION_NOT_RUN" if not latest else "ACTUAL_NOT_AVAILABLE")
        identity = identify_period(ledger.period_end, ledger.period_type) if definition["expectation_type"] != "QUALITATIVE" else None
        unit = canonical_unit(ledger.metric)
        forecast = normalize(definition["expected_value"], definition["unit"], ledger.metric) if definition.get("expected_value") is not None else None
        interval = result.get("forecast_interval") or {}
        low, high = interval.get("low", meta.get("low")), interval.get("high", meta.get("high"))
        actual = result.get("actual_value") if first else None
        # Keep historical score calculations exactly as stored, not recomputed
        # using today's tolerance settings or confidence labels.
        output.append(dict(expectation_id=ledger.id, revision_id=revision.id, metric=ledger.metric,
            metric_definition=ledger.metric, scope=ledger.scope, period_type=ledger.period_type,
            period_start=identity.start if identity else None, period_end=ledger.period_end,
            fiscal_year=identity.fiscal_year if identity else None,
            reporting_basis=definition["reporting_basis"], unit=unit, currency=definition["currency"],
            predicted=str(forecast) if forecast is not None else None,
            original_predicted=definition.get("expected_value"), original_unit=definition["unit"],
            actual=actual, created_at=utc(revision.created_at), cutoff_at=utc(revision.cutoff_at),
            information_cutoff=meta.get("information_cutoff", utc(revision.created_at).isoformat()),
            horizon_quarters=meta.get("horizon_quarters"), horizon_days=meta.get("horizon_days", (ledger.period_end-utc(revision.created_at).date()).days),
            engine_version=meta.get("engine_version", "LEGACY_AI"), model=meta.get("model", "UNSPECIFIED"),
            status=state, reason_code=reason, reason=result.get("reason", "No evaluation has run"),
            evaluation_id=first.id if first else None, evaluated_at=utc(first.created_at) if first else None,
            actual_published_at=result.get("outcome_published_at") if first else None,
            signed_error=result.get("signed_error") if first else None,
            absolute_error=result.get("absolute_error") if first else None,
            absolute_percentage_error=result.get("absolute_percentage_error") if first else None,
            directional_correctness=result.get("directional_correctness") if first else None,
            evidence=result.get("evidence", {}), forecast_provenance=revision.provenance,
            calculation_version=result.get("scoring_version", result.get("policy_version", "UNSCORED")),
            latest_evaluation_id=latest.id if latest else None,
            later_evaluations=[dict(evaluation_id=e.id, evaluated_at=utc(e.created_at), outcome=e.outcome,
                actual=e.result.get("actual_value"), absolute_error=e.result.get("absolute_error"),
                actual_published_at=e.result.get("outcome_published_at"), evidence=e.result.get("evidence", {}),
                reason=e.result.get("reason")) for e in history if first and e.id > first.id],
            low=low, high=high, interval_probability=interval.get("probability"),
            probability=definition.get("confidence") if definition.get("confidence_event") == "MET_UNDER_FROZEN_TOLERANCE" else None,
            confidence_label=definition.get("confidence_label"), met=first.outcome == "MET" if first else False))
    return output


def summarize(rows):
    metrics = continuous_metrics([(r["predicted"], r["actual"], r["low"], r["high"]) for r in rows],
                                 percentage_units=rows[0]["unit"] in {"PERCENT", "BASIS_POINTS"})
    directions = [r["directional_correctness"] for r in rows if r["directional_correctness"] is not None]
    return {**metrics, "directional_n": len(directions),
            "directional_accuracy": str(D(sum(directions))/len(directions)) if directions else None}


def forecast_accuracy(db, company_id, scope, *, offset=0, limit=50, **filters):
    rows = observation_rows(db, company_id, scope, **filters)
    counts = Counter(r["status"] for r in rows)
    groups = defaultdict(list)
    identity_fields = ("metric", "engine_version", "model", "horizon_quarters", "period_type", "unit", "reporting_basis", "currency")
    for row in rows:
        if row["status"] == "EVALUATED":
            groups[tuple(row[k] for k in identity_fields)].append(row)
    result = []
    minimum = settings.expectation_min_sample
    for key, values in sorted(groups.items(), key=lambda item: str(item[0])):
        values.sort(key=lambda r: (r["actual_published_at"], r["revision_id"]))
        identity = dict(zip(identity_fields, key))
        rolling = []
        for i, row in enumerate(values):
            window = values[max(0, i-19):i+1]
            rolling.append(dict(through_period=row["period_end"], **summarize(window),
                                evaluation_ids=[v["evaluation_id"] for v in window],
                                calibration=calibration(window, minimum)))
        result.append({**identity, **summarize(values), "scope": scope,
            "error_unit": "PERCENTAGE_POINTS" if identity["unit"] == "PERCENT" else identity["unit"],
            "status": "INSUFFICIENT_DATA" if len(values) < minimum else "DESCRIPTIVE_SAMPLE",
            "interval_interpretation": "Scenario envelopes have no nominal probability; only declared prediction intervals enter interval calibration.",
            "calibration": calibration(values, minimum), "rolling_last_20": rolling[-20:],
            "evaluation_ids": [r["evaluation_id"] for r in values],
            "error_trend": [{k: r[k] for k in ("revision_id", "evaluation_id", "signed_error", "absolute_error", "directional_correctness")}
                | {"period": r["period_end"], "published_at": r["actual_published_at"]} for r in values[-20:]]})
    return dict(minimum_n=minimum, scoring_version=VERSION,
        sample="Original Almonds point forecasts; first eligible uncorrected actual snapshot. Imports and validation hindcasts excluded.",
        methodology="Equal weight per forecast within metric, period type, basis, unit, currency, model and horizon. WAPE weights absolute actual size within each group. Positive bias means overprediction. Negative actuals excluded from MAPE only; outliers retained. No composite score. Rolling windows use first actual publication order.",
        calibration_note="Only frozen nominal intervals or explicit probabilities are probabilistic. Confidence labels show descriptive MET frequency only. Wilson 95% bounds assume independent outcomes; overlapping horizons may be dependent. No learned mapping or retrospective confidence is used.",
        overview=dict(total=len(rows), evaluated=counts["EVALUATED"], eligible_sample=counts["EVALUATED"],
            pending=counts["PENDING"], missing_actual=counts["MISSING_ACTUAL"], excluded=counts["EXCLUDED"],
            status="INSUFFICIENT_DATA" if not result or all(r["n"] < minimum for r in result) else "DESCRIPTIVE_SAMPLE"),
        exclusions=[dict(reason_code=k, n=v) for k, v in sorted(Counter(r["reason_code"] for r in rows if r["status"] != "EVALUATED").items())],
        groups=result[offset:offset+limit], total=len(result), offset=offset, limit=limit)


def comparisons(db, company_id, scope, *, offset=0, limit=50, exclusions_only=False, **filters):
    rows = observation_rows(db, company_id, scope, **filters)
    if exclusions_only:
        rows = [r for r in rows if r["status"] != "EVALUATED"]
    rows.reverse()
    return dict(items=rows[offset:offset+limit], total=len(rows), offset=offset, limit=limit)
