"""Reproducible summaries: one original forecast per ledger, no composite score."""
from collections import defaultdict
from decimal import Decimal
from statistics import median
from sqlalchemy import select, func
from app.config import settings
from app.models.expectations import HistoricalExpectation as Ledger, HistoricalExpectationRevision as Revision
from app.models.thesis import ThesisPillar, ThesisPillarRevision
from app.services.expectations import ledger_query, latest_evaluations, view, assessment_status
from app.services.expectation_math import canonical_unit
from app.services.expectation_evidence import utc

D = Decimal
SCORED = {"MET", "PARTIALLY_MET", "MISSED"}


def calibration(rows, minimum):
    buckets = defaultdict(list)
    eligible = [(r, e) for r, e in rows if r.definition.get("confidence") is not None and e.outcome in SCORED
                and e.prospective_eligible and not r.definition.get("historical_import")
                and r.definition.get("confidence_event") == "MET_UNDER_FROZEN_TOLERANCE"]
    for revision, evaluation in eligible:
        probability = D(revision.definition["confidence"])
        buckets[min(int(probability * 10), 9)].append((probability, D(int(evaluation.outcome == "MET"))))
    output = []
    brier_sum = D(0)
    weighted_error = D(0)
    for bucket, values in sorted(buckets.items()):
        n = len(values)
        predicted = sum(p for p, _ in values) / n
        observed = sum(y for _, y in values) / n
        brier = sum((p - y) ** 2 for p, y in values) / n
        brier_sum += brier * n
        weighted_error += abs(predicted - observed) * n
        output.append({"lower": str(D(bucket) / 10), "upper": str(D(bucket + 1) / 10), "n": n,
            "predicted": str(predicted), "observed": str(observed), "brier": str(brier),
            "status": "SUFFICIENT_DATA" if n >= minimum else "INSUFFICIENT_DATA"})
    n = len(eligible)
    return {"n": n, "status": "SUFFICIENT_DATA" if n >= minimum else "INSUFFICIENT_DATA", "minimum_n": minimum,
        "brier": str(brier_sum / n) if n >= minimum else None,
        "expected_calibration_error": str(weighted_error / n) if n >= minimum else None,
        "buckets": output, "event": "MET_UNDER_FROZEN_TOLERANCE",
        "warning": "Descriptive historical frequency does not establish forecast skill; small buckets remain uncertain.",
        "partial_outcome_policy": "PARTIALLY_MET is failure of the explicitly defined MET event"}


def summarize(rows, minimum):
    n = len(rows)
    met = sum(e.outcome == "MET" for _, e in rows)
    errors = [D(e.result["absolute_error"]) for _, e in rows if e.result.get("absolute_error") is not None]
    ape = [D(e.result["absolute_percentage_error"]) for _, e in rows if e.result.get("absolute_percentage_error") is not None]
    ranges = [e.result["range_coverage"] for _, e in rows if e.result.get("range_coverage") is not None]
    directions = [e.result["directional_correctness"] for _, e in rows if e.result.get("directional_correctness") is not None]
    return {"n": n, "met": met, "missed": sum(e.outcome == "MISSED" for _, e in rows),
        "partially_met": sum(e.outcome == "PARTIALLY_MET" for _, e in rows),
        "status": "SUFFICIENT_DATA" if n >= minimum else "INSUFFICIENT_DATA",
        "met_rate": str(D(met) / n) if n >= minimum else None,
        "absolute_error": {"n": len(errors), "median": str(median(errors)) if errors else None,
            "min": str(min(errors)) if errors else None, "max": str(max(errors)) if errors else None},
        "absolute_percentage_error": {"n": len(ape), "median": str(median(ape)) if ape else None},
        "range_coverage": {"n": len(ranges), "covered": sum(ranges)},
        "directional_correctness": {"n": len(directions), "correct": sum(directions)}}


def statistics(db, company_id=None, **filters):
    query = ledger_query(company_id, **filters)
    # Revisions are auditable, but never additional independent forecast samples.
    pairs = db.execute(select(Ledger, Revision).join(Revision, Revision.expectation_id == Ledger.id).where(
        Ledger.id.in_(query.with_only_columns(Ledger.id)), Revision.version == 1).order_by(Ledger.period_end, Ledger.id)).all()
    evaluations = latest_evaluations(db, [r.id for _, r in pairs])
    from app.services.forecast_observations import observation_history
    primary, _ = observation_history(db, {r.id: r for l, r in pairs if l.source_type == "AI"})
    minimum = settings.expectation_min_sample
    groups = defaultdict(list)
    period_groups = defaultdict(lambda: defaultdict(list))
    counts = defaultdict(int)
    evidence_count = 0
    states = defaultdict(int)
    for ledger, revision in pairs:
        latest = evaluations.get(revision.id)
        evaluation = primary.get(revision.id) if ledger.source_type == "AI" else latest
        state = assessment_status(revision, evaluation or latest)
        if ledger.source_type == "AI" and not evaluation and latest and latest.outcome in SCORED:
            state = "EXCLUDED"
        states[state] += 1
        if latest and latest.result.get("evidence"):
            evidence_count += 1
        if not evaluation or not evaluation.prospective_eligible or evaluation.outcome not in SCORED:
            continue
        counts[evaluation.outcome] += 1
        key = (ledger.metric, ledger.scope, ledger.period_type, ledger.source_type, revision.definition["expectation_type"],
               canonical_unit(ledger.metric), revision.definition["reporting_basis"])
        groups[key].append((revision, evaluation))
        period_groups[key][ledger.period_end.isoformat()].append((revision, evaluation))
    summaries, calibrations = [], []
    for key, values in sorted(groups.items()):
        identity = dict(zip(("metric", "scope", "period_type", "source", "category", "unit", "reporting_basis"), key))
        timeline = [{"period": day, **summarize(rows, minimum)} for day, rows in sorted(period_groups[key].items())]
        rolling = []
        accumulated = []
        for day, rows in sorted(period_groups[key].items()):
            accumulated.extend(rows)
            rolling.append({"through_period": day, **summarize(accumulated[-20:], minimum)})
        summaries.append({**identity, **summarize(values, minimum), "periods": timeline, "rolling_last_20": rolling})
        calibrations.append({**identity, **calibration(values, minimum)})
    total = len(pairs)
    evaluated = sum(counts.values())
    return {"total": total, "evaluated": evaluated, "met": counts["MET"], "missed": counts["MISSED"],
        "partially_met": counts["PARTIALLY_MET"], "unresolved": total - evaluated,
        "pending": states["PENDING"], "excluded": states["EXCLUDED"], "unresolved_evidence": states["UNRESOLVED"],
        "evidence_coverage": {"n": total, "with_evidence": evidence_count, "ratio": str(D(evidence_count) / total) if total else None},
        "groups": summaries, "calibration": calibrations,
        "policy": {"minimum_n": minimum, "amount_met_pct": settings.expectation_met_tolerance_pct,
            "amount_partial_pct": settings.expectation_partial_tolerance_pct, "percentage_met_pp": settings.expectation_percentage_tolerance_pp, "sample": "Original revision only; first eligible actual for AI forecasts, latest evaluation for other sources; imports excluded",
            "aggregation": "Separate metric, scope, period type, source, category, unit and reporting basis; median errors; no composite score",
            "reliability": "No reliability rating below minimum_n; raw error summaries remain descriptive",
            "brier": "mean((probability_of_MET - indicator(outcome == MET))^2); PARTIALLY_MET has indicator 0",
            "rolling": "Last 20 original forecasts, ordered by target period and ledger identity"}}


def thesis_history(db, company_id, *, scope=None, offset=0, limit=50):
    query = ledger_query(company_id, scope=scope)
    ids_query = select(Revision.thesis_revision_id).where(
        Revision.expectation_id.in_(query.with_only_columns(Ledger.id)),
        Revision.thesis_revision_id.is_not(None)).distinct()
    total = db.scalar(select(func.count()).select_from(ids_query.subquery()))
    ids = list(db.scalars(ids_query.order_by(Revision.thesis_revision_id.desc()).offset(offset).limit(limit)))
    revisions = list(db.scalars(select(Revision).where(Revision.thesis_revision_id.in_(ids)).order_by(Revision.id.desc())))
    evaluations = latest_evaluations(db, [r.id for r in revisions])
    grouped = defaultdict(list)
    ledgers = {r.id: r for r in db.scalars(query.where(Ledger.id.in_([r.expectation_id for r in revisions])))}
    theses = {r.id: r for r in db.scalars(select(ThesisPillarRevision).where(ThesisPillarRevision.id.in_(ids)))}
    for revision in revisions:
        grouped[revision.thesis_revision_id].append(view(ledgers[revision.expectation_id], revision, evaluations.get(revision.id)))
    result = []
    for thesis_id, items in grouped.items():
        thesis = theses[thesis_id]
        originals = [i for i in items if i["version"] == 1]
        outcomes = [i["evaluation"]["outcome"] if i["evaluation"] else None for i in originals]
        if all(o == "MET" for o in outcomes):
            outcome = "VALIDATED"
        elif all(o == "MISSED" for o in outcomes):
            outcome = "CONTRADICTED"
        elif any(o in {"MET", "PARTIALLY_MET"} for o in outcomes):
            outcome = "PARTIALLY_VALIDATED"
        elif all(o in {"INSUFFICIENT_EVIDENCE", "NOT_COMPARABLE"} for o in outcomes):
            outcome = "INSUFFICIENT_EVIDENCE"
        else:
            outcome = "UNRESOLVED"
        result.append({"thesis_revision_id": thesis_id, "version": thesis.version, "created_at": utc(thesis.created_at),
            "original_thesis": thesis.definition, "outcome": outcome, "expectations": items,
            "validated": [i["revision_id"] for i in originals if i["evaluation"] and i["evaluation"]["outcome"] == "MET"],
            "contradicted": [i["revision_id"] for i in originals if i["evaluation"] and i["evaluation"]["outcome"] == "MISSED"],
            "unresolved": [i["revision_id"] for i in originals if not i["evaluation"] or i["evaluation"]["outcome"] not in SCORED],
            "interpretation": "Assumption accuracy only; business deterioration and investment verdicts are not inferred."})
    choices = select(ThesisPillarRevision).join(ThesisPillar).where(ThesisPillar.company_id == company_id)
    if scope:
        choices = choices.where(ThesisPillar.scope == scope)
    available = [{"id": r.id, "name": r.definition["name"], "version": r.version, "created_at": utc(r.created_at)}
        for r in db.scalars(choices.order_by(ThesisPillarRevision.id.desc()).limit(200))]
    return {"items": result, "offset": offset, "limit": limit, "pagination_unit": "thesis versions", "total": total,
            "available_theses": available}
