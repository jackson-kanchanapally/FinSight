"""Versioned expectations and immutable, idempotent evaluations of verified outcomes."""
from datetime import datetime
import re
from sqlalchemy import select, func
from app.models.expectations import HistoricalExpectation as Ledger, HistoricalExpectationRevision as Revision, ExpectationEvaluation as Evaluation
from app.models.thesis import ThesisPillar, ThesisPillarRevision
from app.models.commentary import ManagementGuidance
from app.models.checkpoint import InvestorCheckpoint
from app.services.thesis import lock_company
from app.services.thesis_monitoring_evidence import digest, collect_evidence
from app.services.expectation_evidence import utc, utcnow, resolve_source, actual_evidence
from app.services.expectation_math import compare, normalize, canonical_unit, tolerance_policy, VERSION


def lock(db, company_id):
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    lock_company(db, company_id)


def owned(db, company_id, expectation_id):
    row = db.get(Ledger, expectation_id)
    if not row or row.company_id != company_id:
        raise ValueError("EXPECTATION_NOT_FOUND")
    return row


def latest_revision(db, expectation_id):
    return db.scalar(select(Revision).where(Revision.expectation_id == expectation_id).order_by(Revision.version.desc()))


def save(db, company_id, data, expectation_id=None):
    lock(db, company_id)
    now = utcnow()
    definition = data.model_dump(mode="json", exclude={"expected_version"})
    metric = definition["metric"]
    if data.expectation_type != "QUALITATIVE":
        normalize(0, data.unit, metric)
        expected_currency = "INR" if canonical_unit(metric).startswith("INR") else "NONE"
        if data.currency != expected_currency:
            raise ValueError("CURRENCY_UNIT_MISMATCH")
    if data.reporting_basis != ("ADJUSTED" if metric == "ADJUSTED_EBITDA" else "REPORTED"):
        raise ValueError("METRIC_REPORTING_BASIS_MISMATCH")
    withdrawal = expectation_id is not None and data.lifecycle == "WITHDRAWN"
    if not data.historical_import and data.cutoff_at <= now and not withdrawal:
        raise ValueError("PAST_CUTOFF_REQUIRES_HISTORICAL_IMPORT")
    source = resolve_source(db, data.evidence_id, company_id, data.financial_scope, now)
    if data.evidence_id and not source:
        raise ValueError("INVALID_OR_UNVERIFIED_SOURCE_EVIDENCE")
    if source and utc(datetime.fromisoformat(source.get("available_at", source["published_at"]))) > data.cutoff_at and not withdrawal:
        raise ValueError("SOURCE_PUBLISHED_AFTER_CUTOFF")
    if source and any(datetime.fromisoformat(c["corrected_at"]) >= data.cutoff_at for c in source.get("corrections", [])):
        raise ValueError("SOURCE_CORRECTED_AFTER_CUTOFF")
    if data.issued_at and (data.issued_at > now or data.issued_at > data.cutoff_at):
        raise ValueError("INVALID_HISTORICAL_ISSUE_DATE")
    if data.thesis_revision_id:
        thesis = db.get(ThesisPillarRevision, data.thesis_revision_id)
        pillar = db.get(ThesisPillar, thesis.pillar_id) if thesis else None
        if not pillar or pillar.company_id != company_id or pillar.scope != data.financial_scope:
            raise ValueError("THESIS_COMPANY_SCOPE_MISMATCH")
        if utc(thesis.created_at) > data.cutoff_at:
            raise ValueError("THESIS_CREATED_AFTER_CUTOFF")
    if data.checkpoint_id:
        checkpoint = db.get(InvestorCheckpoint, data.checkpoint_id)
        if (not checkpoint or checkpoint.company_id != company_id
            or (not withdrawal and data.evidence_id != f"S{checkpoint.statement_id}")
            or checkpoint.target_date != data.period):
            raise ValueError("CHECKPOINT_SOURCE_OR_PERIOD_MISMATCH")
    if data.source_type == "MANAGEMENT" and data.expectation_type != "QUALITATIVE" and not withdrawal:
        # A source quotation alone does not validate a caller-supplied numerical
        # target. Reuse the existing, source-validated guidance normalization.
        normalized = db.scalar(select(ManagementGuidance).where(ManagementGuidance.statement_id == int(data.evidence_id[1:])))
        if not normalized or data.expectation_type not in {"EXACT", "RANGE"}:
            raise ValueError("NO_VERIFIED_NORMALIZED_MANAGEMENT_TARGET")
        if data.guidance_id and data.guidance_id != normalized.id:
            raise ValueError("GUIDANCE_SOURCE_MISMATCH")
        data = data.model_copy(update={"guidance_id": normalized.id})
        definition["guidance_id"] = normalized.id
        if not data.checkpoint_id:
            checkpoint = db.scalar(select(InvestorCheckpoint).where(InvestorCheckpoint.guidance_id == normalized.id))
            if checkpoint and checkpoint.target_date == data.period:
                data = data.model_copy(update={"checkpoint_id": checkpoint.id})
                definition["checkpoint_id"] = checkpoint.id
    if data.guidance_id:
        from app.services.commentary import guidance_target_end
        from app.services.expectation_guidance import METRICS
        guidance = db.get(ManagementGuidance, data.guidance_id)
        if (not guidance or guidance.company_id != company_id or data.evidence_id != f"S{guidance.statement_id}"
            or data.source_type != "MANAGEMENT") and not withdrawal:
            raise ValueError("GUIDANCE_SOURCE_MISMATCH")
        if not withdrawal:
            low = data.lower_value if data.expectation_type == "RANGE" else data.expected_value
            high = data.upper_value if data.expectation_type == "RANGE" else data.expected_value
            if (METRICS.get(guidance.metric) != metric or guidance_target_end(guidance.target_period) != data.period
                or definition["period_type"] != ("QUARTERLY" if guidance.target_period.startswith("Q") else "ANNUAL")
                or data.unit != guidance.unit or low != guidance.lower_bound or high != guidance.upper_bound):
                raise ValueError("GUIDANCE_NORMALIZED_TARGET_MISMATCH")
    if data.expectation_type == "DIRECTIONAL":
        baseline = resolve_source(db, data.baseline_evidence_id, company_id, data.financial_scope, now)
        if (not baseline or baseline["metric_code"] != metric or baseline["period_type"] != definition["period_type"]
            or baseline["unit"] != canonical_unit(metric)
            or baseline["period_end"] >= data.period.isoformat()
            or datetime.fromisoformat(baseline.get("available_at", baseline["published_at"])) >= data.cutoff_at
            or any(datetime.fromisoformat(c["corrected_at"]) >= data.cutoff_at for c in baseline["corrections"])):
            raise ValueError("BASELINE_REQUIRES_COMPARABLE_PRE_CUTOFF_EVIDENCE")
        definition["baseline"] = baseline
    original = owned(db, company_id, expectation_id) if expectation_id else None
    previous = latest_revision(db, original.id) if original else None
    if previous:
        if data.expected_version != previous.version:
            raise ValueError("EXPECTATION_VERSION_CONFLICT")
        for key in ("metric", "financial_scope", "period", "period_type", "source_type", "reporting_basis", "currency", "thesis_revision_id", "historical_import"):
            if definition[key] != previous.definition[key]:
                raise ValueError("EXPECTATION_IDENTITY_IS_IMMUTABLE")
        if data.cutoff_at != utc(previous.cutoff_at):
            raise ValueError("CUTOFF_IS_IMMUTABLE")
        # A post-result edit is historical even if its original was prospective.
        if now >= utc(previous.cutoff_at) and not withdrawal and not data.historical_import:
            raise ValueError("REVISION_CUTOFF_PASSED")
        if withdrawal:
            if (data.source_type != "MANAGEMENT" or not source or data.evidence_id == previous.definition["evidence_id"]
                or not re.search(r"withdraw|cancel", source.get("source_quote", ""), re.I)
                or not re.search(r"previous|prior|original", source.get("source_quote", ""), re.I)):
                raise ValueError("WITHDRAWAL_REQUIRES_NEW_OFFICIAL_EVIDENCE")
            for key in ("expected_value", "lower_value", "upper_value", "expectation_type", "unit", "confidence", "confidence_event", "direction", "guidance_id", "checkpoint_id", "baseline_evidence_id"):
                if definition[key] != previous.definition[key]:
                    raise ValueError("WITHDRAWAL_CANNOT_REWRITE_FORECAST")
    elif data.expected_version:
        raise ValueError("EXPECTATION_NOT_FOUND")
    definition["tolerance"] = previous.definition["tolerance"] if withdrawal and previous else tolerance_policy(metric)
    provenance = {"capture": "HISTORICAL_IMPORT" if data.historical_import else "CONTEMPORANEOUS_CAPTURE",
        "independently_verified_import": False, "source_type": data.source_type,
        "source_reference": data.source_reference, "evidence_id": data.evidence_id,
        "source_snapshot": source, "claimed_issued_at": definition.get("issued_at"),
        "note": "Historical imports are excluded from prospective statistics; an issue date is not proof of capture."}
    # Content identity excludes caller timestamps and confidence; retries cannot create
    # independent samples of the same proposition. Changed targets require revisions.
    identity = digest({k: definition[k] for k in ("metric", "financial_scope", "period", "period_type", "source_type", "source_reference", "description", "thesis_revision_id")})
    if not original:
        original = db.scalar(select(Ledger).where(Ledger.company_id == company_id, Ledger.identity_key == identity))
        if original:
            existing = latest_revision(db, original.id)
            if existing.definition != definition:
                raise ValueError("DUPLICATE_EXPECTATION_USE_REVISION")
            return original, existing
        original = Ledger(company_id=company_id, identity_key=identity, metric=metric,
            scope=data.financial_scope, period_end=data.period, period_type=definition["period_type"], source_type=data.source_type,
            created_at=now)
        db.add(original)
        db.flush()
    row = Revision(expectation_id=original.id, version=previous.version + 1 if previous else 1,
        cutoff_at=data.cutoff_at, definition=definition, provenance=provenance,
        statement_id=int(data.evidence_id[1:]) if data.evidence_id and data.evidence_id.startswith("S") else None,
        guidance_id=data.guidance_id, checkpoint_id=data.checkpoint_id, thesis_revision_id=data.thesis_revision_id,
        document_id=source.get("document_id") if source else None, created_at=now)
    db.add(row)
    db.flush()
    return original, row


def evaluate_revision(db, ledger, revision):
    now = utcnow()
    definition = revision.definition
    result = {"outcome": "INSUFFICIENT_EVIDENCE", "reason": "No matching verified result is available", "evidence": {},
        "actual_value": None, "policy_version": VERSION, "assessment_kind": "FORECAST_ACCURACY",
        "business_deterioration": "NOT_INFERRED_FROM_FORECAST_ERROR"}
    period, actual, registry = (None, None, {})
    withdrawn = definition["lifecycle"] == "WITHDRAWN"
    if definition["expectation_type"] == "QUALITATIVE":
        # Existing checkpoint engine is the authority; no LLM or free-text verdicts.
        evidence = collect_evidence(db, ledger.company_id, ledger.scope, now.date()) if revision.checkpoint_id else None
        if evidence:
            _, _, all_sources = evidence
            checkpoint = all_sources.get(f"K{revision.checkpoint_id}")
            if checkpoint:
                ids = checkpoint.get("actual_outcome", {}).get("evidence_ids", [])
                registry = {k: all_sources[k] for k in ids if k in all_sources}
                withdrawn = withdrawn or checkpoint["status"] == "WITHDRAWN"
                if ids and len(registry) == len(ids) and checkpoint["status"] in {"DELIVERED", "MISSED"}:
                    result.update(outcome="MET" if checkpoint["status"] == "DELIVERED" else "MISSED",
                                  reason="Verified checkpoint outcome", checkpoint=checkpoint)
                    # Do not count a management claim as independent delivery evidence.
                    if any(v.get("kind") not in {"FACT", "METRIC", "CORPORATE_DISCLOSURE"} for v in registry.values()):
                        result.update(outcome="INSUFFICIENT_EVIDENCE", reason="Checkpoint lacks independent outcome evidence")
    else:
        period, actual, registry = actual_evidence(db, ledger, now)
        if actual:
            if actual["unit"] != canonical_unit(ledger.metric):
                result.update(outcome="NOT_COMPARABLE", reason="Actual units do not match the metric definition")
            else:
                result.update(compare(definition, actual["value"]), reason="Deterministic comparison with verified result")
    timestamps = [datetime.fromisoformat(r["published_at"]) for r in registry.values() if r.get("published_at")]
    # Publication precision uses start-of-day for date-only disclosures.
    published = min(timestamps) if timestamps else None
    if actual and actual.get("input_evidence_ids"):
        # Old comparison inputs are known baselines, not the target-period outcome.
        target_times = [datetime.fromisoformat(r["published_at"]) for r in registry.values()
                        if r.get("period_id") == period.id and r.get("kind") == "FACT"]
        published = min(target_times) if target_times else published
    cutoff = utc(revision.cutoff_at)
    captured = utc(revision.created_at)
    eligible = not definition["historical_import"] and captured < cutoff
    if published and (cutoff > published or captured >= published):
        eligible = False
        result.update(outcome="NOT_COMPARABLE", reason="Expectation cutoff or capture is after outcome publication")
    if now < cutoff:
        result.update(outcome="INSUFFICIENT_EVIDENCE", reason="Expectation cutoff has not been reached")
    # Later withdrawals need their own source snapshot. They never erase a result
    # that was already public, or manufacture a failed business outcome.
    withdrawals = db.scalars(select(Revision).where(Revision.expectation_id == ledger.id,
        Revision.version > revision.version).order_by(Revision.version))
    withdrawal_sources = []
    for newer in withdrawals:
        if newer.definition["lifecycle"] == "WITHDRAWN":
            snapshot = newer.provenance.get("source_snapshot")
            if snapshot and (not published or datetime.fromisoformat(snapshot["published_at"]) < published):
                withdrawn = True
                withdrawal_sources.append(snapshot)
    if revision.checkpoint_id:
        from app.services.checkpoints import evaluate_checkpoint
        from app.models.financial import FinancialPeriod
        checkpoint = db.get(InvestorCheckpoint, revision.checkpoint_id)
        owner = period or db.scalar(select(FinancialPeriod).where(FinancialPeriod.company_id == ledger.company_id,
            FinancialPeriod.scope == ledger.scope, FinancialPeriod.status == "VALIDATED",
            FinancialPeriod.period_end <= now.date()).order_by(FinancialPeriod.period_end.desc()))
        if checkpoint and owner:
            status, details = evaluate_checkpoint(db, checkpoint, owner, now.date())
            if status == "WITHDRAWN":
                for key in details.get("evidence_ids", []):
                    snapshot = resolve_source(db, key, ledger.company_id, ledger.scope, now)
                    if snapshot and (not published or datetime.fromisoformat(snapshot["published_at"]) < published):
                        withdrawn = True
                        withdrawal_sources.append(snapshot)
    if withdrawn:
        result["withdrawal_evidence"] = withdrawal_sources
        result.update(outcome="INSUFFICIENT_EVIDENCE", reason="Guidance withdrawn; withdrawal is not proof of failure")
    if definition.get("guidance_kind") in {"ASPIRATION", "CONDITIONAL"}:
        eligible = False
        result["eligibility_note"] = "Aspirational or conditional guidance is descriptive, excluded from credibility statistics"
    if definition["source_type"] == "AI" and definition["historical_import"]:
        result.update(outcome="INSUFFICIENT_EVIDENCE", reason="Imported AI expectations have no independently verified pre-outcome capture")
    result.update(evidence=registry, outcome_published_at=published.isoformat() if published else None,
        cutoff_at=cutoff.isoformat(), captured_at=captured.isoformat(), withdrawn=withdrawn)
    existing = db.scalar(select(Evaluation).where(Evaluation.revision_id == revision.id).order_by(Evaluation.id.desc()))
    if existing and digest(existing.result) == digest(result):
        return existing
    # A -> unavailable -> A is a new evidence state; returning the first A would
    # leave the dashboard pointing at the intervening unavailable evaluation.
    fingerprint = digest({"result": result, "previous_evaluation_id": existing.id if existing else None})
    evaluation = Evaluation(revision_id=revision.id, financial_period_id=period.id if period else None,
        fingerprint=fingerprint, outcome=result["outcome"], prospective_eligible=eligible,
        result=result, created_at=now)
    db.add(evaluation)
    db.flush()
    return evaluation


def evaluate(db, company_id, expectation_id):
    lock(db, company_id)
    ledger = owned(db, company_id, expectation_id)
    evaluations = []
    # Earlier predictions remain accountable after revisions and withdrawals.
    for revision in db.scalars(select(Revision).where(Revision.expectation_id == ledger.id).order_by(Revision.version)):
        evaluations.append(evaluate_revision(db, ledger, revision))
    return evaluations[-1]


def evaluation_view(row):
    return {"id": row.id, "revision_id": row.revision_id, "financial_period_id": row.financial_period_id,
        "outcome": row.outcome, "prospective_eligible": row.prospective_eligible, "result": row.result,
        "created_at": utc(row.created_at)}


def view(ledger, revision, evaluation=None, superseded=False):
    status = "SUPERSEDED" if superseded else "PENDING" if not evaluation else (
        "PENDING" if evaluation.result.get("reason") == "Expectation cutoff has not been reached" else
        "UNVERIFIABLE" if evaluation.outcome in {"NOT_COMPARABLE", "INSUFFICIENT_EVIDENCE"} else "EVALUATED")
    return {"id": ledger.id, "company_id": ledger.company_id, "revision_id": revision.id, "version": revision.version,
        "created_at": utc(revision.created_at), "cutoff_at": utc(revision.cutoff_at), "definition": revision.definition,
        "provenance": revision.provenance, "status": status,
        "evaluation": evaluation_view(evaluation) if evaluation else None}


def latest_evaluations(db, revision_ids):
    latest = select(Evaluation.revision_id, func.max(Evaluation.id).label("last_id")).where(
        Evaluation.revision_id.in_(revision_ids)).group_by(Evaluation.revision_id).subquery()
    return {r.revision_id: r for r in db.scalars(select(Evaluation).join(latest, Evaluation.id == latest.c.last_id))}


def ledger_query(company_id=None, scope=None, period=None, metric=None, source=None):
    query = select(Ledger)
    for column, value in ((Ledger.company_id, company_id), (Ledger.scope, scope), (Ledger.period_end, period), (Ledger.metric, metric), (Ledger.source_type, source)):
        if value is not None:
            query = query.where(column == value)
    return query


def list_expectations(db, company_id=None, *, scope=None, period=None, metric=None, source=None, offset=0, limit=50):
    query = ledger_query(company_id, scope, period, metric, source)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    ledgers = list(db.scalars(query.order_by(Ledger.id.desc()).offset(offset).limit(limit)))
    latest = select(Revision.expectation_id, func.max(Revision.version).label("v")).where(
        Revision.expectation_id.in_([r.id for r in ledgers])).group_by(Revision.expectation_id).subquery()
    revisions = {r.expectation_id: r for r in db.scalars(select(Revision).join(latest,
        (Revision.expectation_id == latest.c.expectation_id) & (Revision.version == latest.c.v)))}
    evaluations = latest_evaluations(db, [r.id for r in revisions.values()])
    return {"items": [view(row, revisions[row.id], evaluations.get(revisions[row.id].id)) for row in ledgers],
            "total": total, "offset": offset, "limit": limit}


def revisions_view(db, company_id, expectation_id, *, offset=0, limit=200):
    ledger = owned(db, company_id, expectation_id)
    last_version = db.scalar(select(func.max(Revision.version)).where(Revision.expectation_id == ledger.id))
    revisions = list(db.scalars(select(Revision).where(Revision.expectation_id == ledger.id).order_by(Revision.version).offset(offset).limit(limit)))
    evaluations = latest_evaluations(db, [r.id for r in revisions])
    return [view(ledger, r, evaluations.get(r.id), r.version != last_version) for r in revisions]


def refresh_expectations(db, company_id):
    """Runs after committed ingestion. Safe to retry explicitly or on the next ingest."""
    import logging
    try:
        lock(db, company_id)
        from app.services.expectation_guidance import register_guidance
        register_guidance(db, company_id, utcnow())
        rows = db.scalars(select(Ledger).where(Ledger.company_id == company_id)).all()
        for row in rows:
            evaluate(db, company_id, row.id)
        db.commit()
        return len(rows)
    except Exception:
        db.rollback()
        logging.getLogger(__name__).exception("expectations.evaluation_failed company_id=%s", company_id)
        return None
