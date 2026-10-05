"""Persistent investment definitions; every edit appends an auditable revision."""
from datetime import date
from uuid import uuid4
from sqlalchemy import select
from app.models.company import Company
from app.models.financial import FinancialPeriod, MetricCode, FinancialUnit
from app.models.checkpoint import InvestorCheckpoint
from app.models.thesis import (ThesisPillar, ThesisPillarRevision, ThesisExpectationRevision,
                              ThesisEvaluationRun, ThesisPillarEvaluation)
from app.schemas.thesis import PillarInput, ExpectationInput
from app.services.thesis_evidence import analysis_evidence, expectation_gap


def lock_company(db, company_id):
    if not db.scalar(select(Company).where(Company.id == company_id).with_for_update()):
        raise ValueError("COMPANY_NOT_FOUND")


def revision_at(db, pillar_id, as_of):
    return db.scalar(select(ThesisPillarRevision).where(
        ThesisPillarRevision.pillar_id == pillar_id, ThesisPillarRevision.effective_date <= as_of
    ).order_by(ThesisPillarRevision.version.desc()))


def expectation_revisions(db, pillar_id, as_of):
    rows = db.scalars(select(ThesisExpectationRevision).where(
        ThesisExpectationRevision.pillar_id == pillar_id,
        ThesisExpectationRevision.effective_date <= as_of).order_by(ThesisExpectationRevision.version.desc()))
    latest = {}
    for row in rows:
        latest.setdefault(row.expectation_key, row)
    return sorted(latest.values(), key=lambda row: row.id)


def state_at(db, revision, as_of):
    evaluation = db.scalar(select(ThesisPillarEvaluation).join(ThesisEvaluationRun).where(
        ThesisPillarEvaluation.revision_id == revision.id,
        ThesisEvaluationRun.as_of_date <= as_of).order_by(
            ThesisEvaluationRun.as_of_date.desc(), ThesisPillarEvaluation.id.desc()))
    return (evaluation.new_status, evaluation.reason, evaluation) if evaluation else (
        revision.initial_status, revision.reason, None)


def owned_pillar(db, company_id, pillar_id):
    pillar = db.get(ThesisPillar, pillar_id)
    if not pillar or pillar.company_id != company_id:
        raise ValueError("PILLAR_COMPANY_MISMATCH")
    return pillar


def _definition(db, company_id, data, today):
    if any(code not in {v.value for v in MetricCode} for code in data.metric_codes):
        raise ValueError("UNKNOWN_FINANCIAL_METRIC")
    definition = data.model_dump(mode="json", exclude={"expectations", "expected_version", "reason"})
    if data.created_from != "USER" and (not data.source_analysis_id or not data.evidence_ids):
        raise ValueError("INFERRED_THESIS_REQUIRES_EVIDENCE")
    if data.evidence_ids:
        if not data.source_analysis_id:
            raise ValueError("SOURCE_ANALYSIS_REQUIRED")
        _, period, _, registry, _, _ = analysis_evidence(db, company_id, data.source_analysis_id, today)
        if period.scope.value != data.scope or any(key not in registry for key in data.evidence_ids):
            raise ValueError("INVALID_THESIS_EVIDENCE")
        definition["source_evidence"] = {key: registry[key] for key in data.evidence_ids}
    else:
        definition["source_evidence"] = {}
    definition["accepted_by_user"] = True
    return definition


def save_pillar(db, company_id, data: PillarInput, pillar_id=None, *, today=None,
                accepted_by_user=True, source_components=None):
    today = today or date.today()
    lock_company(db, company_id)
    definition = _definition(db, company_id, data, today)
    definition["accepted_by_user"] = accepted_by_user
    if source_components is not None:
        definition["source_components"] = source_components
    previous_status = None
    status = "UNPROVEN"
    version = 1
    if pillar_id is None:
        pillar = ThesisPillar(company_id=company_id, scope=data.scope)
        db.add(pillar)
        db.flush()
    else:
        pillar = owned_pillar(db, company_id, pillar_id)
        old = revision_at(db, pillar.id, today)
        if not old or data.expected_version != old.version:
            raise ValueError("THESIS_VERSION_CONFLICT")
        if pillar.scope != data.scope:
            raise ValueError("THESIS_SCOPE_IS_IMMUTABLE")
        if data.created_from != old.definition["created_from"]:
            raise ValueError("THESIS_ORIGIN_IS_IMMUTABLE")
        definition["source_components"] = old.definition.get("source_components", [])
        version = old.version + 1
        previous_status, _, _ = state_at(db, old, today)
        material_edit = any(definition.get(key) != old.definition.get(key) for key in
                            ("name", "category", "description", "metric_codes", "evidence_ids"))
        status = "UNPROVEN" if material_edit else previous_status
        if data.expectations:
            raise ValueError("EDIT_EXPECTATIONS_THROUGH_THEIR_REVISION_API")
    revision = ThesisPillarRevision(pillar_id=pillar.id, version=version,
        effective_date=today, definition=definition, previous_status=previous_status,
        initial_status=status, reason=data.reason)
    db.add(revision)
    db.flush()
    for expectation in data.expectations:
        save_expectation(db, company_id, pillar.id, expectation, today=today, lock=False,
                         created_from="USER" if accepted_by_user else "SYSTEM_INFERRED")
    db.flush()
    return pillar


def save_expectation(db, company_id, pillar_id, data: ExpectationInput, expectation_key=None, *, today=None, lock=True,
                     created_from="USER"):
    today = today or date.today()
    if lock:
        lock_company(db, company_id)
    pillar = owned_pillar(db, company_id, pillar_id)
    revision = revision_at(db, pillar.id, today)
    if not revision or not revision.definition["is_active"]:
        raise ValueError("ACTIVE_PILLAR_REQUIRED")
    if data.checkpoint_id:
        cp = db.get(InvestorCheckpoint, data.checkpoint_id)
        owner = db.get(FinancialPeriod, cp.created_period_id) if cp else None
        if not cp or cp.company_id != company_id or owner.scope.value != pillar.scope:
            raise ValueError("CHECKPOINT_COMPANY_SCOPE_MISMATCH")
    if data.expectation_type == "METRIC":
        if data.metric_code not in {v.value for v in MetricCode} or data.unit not in {v.value for v in FinancialUnit}:
            raise ValueError("UNKNOWN_METRIC_OR_UNIT")
    version = 1
    if expectation_key:
        old = db.scalar(select(ThesisExpectationRevision).where(
            ThesisExpectationRevision.expectation_key == expectation_key,
            ThesisExpectationRevision.pillar_id == pillar.id).order_by(ThesisExpectationRevision.version.desc()))
        if not old or old.version != data.expected_version:
            raise ValueError("EXPECTATION_VERSION_CONFLICT")
        version = old.version + 1
    row = ThesisExpectationRevision(expectation_key=expectation_key or str(uuid4()),
        pillar_id=pillar.id, version=version, effective_date=today, checkpoint_id=data.checkpoint_id,
        definition={**data.model_dump(mode="json", exclude={"expected_version", "reason", "checkpoint_id"}),
                    "created_from": created_from,
                    "authority": "INVESTOR_REQUIREMENT" if created_from == "USER" else "SYSTEM_HYPOTHESIS"},
        reason=data.reason)
    db.add(row)
    db.flush()
    return row


def thesis_view(db, company_id, scope="CONSOLIDATED", as_of=None):
    as_of = as_of or date.today()
    pillars, timeline, groups = [], [], {}
    runs = list(db.scalars(select(ThesisEvaluationRun).where(
        ThesisEvaluationRun.company_id == company_id, ThesisEvaluationRun.scope == scope,
        ThesisEvaluationRun.as_of_date <= as_of).order_by(ThesisEvaluationRun.id.desc())))
    run_map = {run.id: run for run in runs}
    for pillar in db.scalars(select(ThesisPillar).where(
            ThesisPillar.company_id == company_id, ThesisPillar.scope == scope).order_by(ThesisPillar.id)):
        revision = revision_at(db, pillar.id, as_of)
        if not revision:
            continue
        status, reason, evaluation = state_at(db, revision, as_of)
        definitions = list(db.scalars(select(ThesisPillarRevision).where(
            ThesisPillarRevision.pillar_id == pillar.id, ThesisPillarRevision.effective_date <= as_of
        ).order_by(ThesisPillarRevision.version)))
        for row in definitions:
            timeline.append({"kind": "CREATED" if row.version == 1 else "EDITED", "pillar_id": pillar.id,
                "name": row.definition["name"], "effective_date": row.effective_date.isoformat(),
                "previous_status": row.previous_status, "new_status": row.initial_status,
                "reason": row.reason, "evidence_ids": row.definition["evidence_ids"],
                "evidence": row.definition.get("source_evidence", {}), "analysis_id": row.definition.get("source_analysis_id"),
                "recorded_at": row.created_at.isoformat(), "definition": row.definition, "version": row.version})
        evaluations = list(db.scalars(select(ThesisPillarEvaluation).where(
            ThesisPillarEvaluation.pillar_id == pillar.id, ThesisPillarEvaluation.run_id.in_(run_map or [-1])
        ).order_by(ThesisPillarEvaluation.id)))
        for row in evaluations:
            run = run_map[row.run_id]
            historical_revision = next(v for v in definitions if v.id == row.revision_id)
            timeline.append({"kind": "EVALUATED", "pillar_id": pillar.id,
                "name": historical_revision.definition["name"], "effective_date": run.as_of_date.isoformat(),
                "evidence_as_of": run.evidence_as_of.isoformat(),
                "previous_status": row.previous_status, "new_status": row.new_status,
                "reason": row.reason, "evidence_ids": row.evidence_ids,
                "evidence": {key: run.evidence_registry[key] for key in row.evidence_ids if key in run.evidence_registry},
                "analysis_id": run.analysis_id, "recorded_at": row.created_at.isoformat(),
                "contradiction": row.contradiction, "method": row.method})
        tests = []
        evaluated_tests = {r["expectation_key"]: r for r in evaluation.expectations} if evaluation else {}
        for test in expectation_revisions(db, pillar.id, as_of):
            outcome = evaluated_tests.get(test.expectation_key)
            # Editing a test never inherits an outcome from its old definition.
            if outcome and outcome["revision_id"] != test.id:
                outcome = None
            tests.append({**test.definition, "expectation_key": test.expectation_key, "version": test.version,
                "checkpoint_id": test.checkpoint_id, "outcome": outcome,
                "effective_date": test.effective_date.isoformat()})
        all_test_revisions = db.scalars(select(ThesisExpectationRevision).where(
            ThesisExpectationRevision.pillar_id == pillar.id, ThesisExpectationRevision.effective_date <= as_of))
        for test in all_test_revisions:
            timeline.append({"kind": "EXPECTATION_EDITED" if test.version > 1 else "EXPECTATION_CREATED",
                "pillar_id": pillar.id, "name": revision.definition["name"],
                "effective_date": test.effective_date.isoformat(), "recorded_at": test.created_at.isoformat(),
                "previous_status": None, "new_status": None, "reason": test.reason,
                "definition": test.definition, "expectation_key": test.expectation_key,
                "version": test.version, "evidence_ids": [], "evidence": {}, "analysis_id": None})
        item = {**revision.definition, "id": pillar.id, "version": revision.version, "status": status,
                "status_reason": reason, "last_evaluated_at": evaluation.created_at.isoformat() if evaluation else None,
                "expectations": tests, "research_gaps": evaluation.research_gaps if evaluation else [],
                "contradiction": evaluation.contradiction if evaluation else False}
        item["status_evidence_ids"] = evaluation.evidence_ids if evaluation else []
        item["status_evidence"] = {k: run_map[evaluation.run_id].evidence_registry[k]
            for k in evaluation.evidence_ids if k in run_map[evaluation.run_id].evidence_registry} if evaluation else {}
        pillars.append(item)
        if item["is_active"]:
            groups.setdefault(status, []).append({"id": pillar.id, "name": item["name"], "importance": item["importance"]})
    timeline.sort(key=lambda row: (row["effective_date"], row["recorded_at"]), reverse=True)
    return {"company_id": company_id, "scope": scope, "as_of_date": as_of.isoformat(),
            "pillars": pillars, "current_investment_case": groups, "history": timeline,
            "expectation_gap": runs[0].expectation_gap if runs else {
                "classification": "INSUFFICIENT_EVIDENCE", "reason": "No thesis evaluation recorded."},
            "research_gaps": list(dict.fromkeys(gap for p in pillars if p["is_active"] for gap in p["research_gaps"]))}


CHECKPOINT_PILLARS = {
    "REVENUE_START": ("Revenue commencement", "GROWTH", ["REVENUE_FROM_OPERATIONS"]),
    "GPU_DEPLOYMENT": ("Capacity deployment", "EXECUTION", []),
    "PAT_MARGIN": ("Profitability", "PROFITABILITY", ["PAT_MARGIN"]),
    "EBITDA_MARGIN": ("Operating profitability", "PROFITABILITY", ["EBITDA_MARGIN"]),
    "CAPEX": ("Capital spending", "CAPITAL_ALLOCATION", ["CAPITAL_EXPENDITURE"]),
    "CAPEX_SCOPE_UNCLEAR": ("Capital spending", "CAPITAL_ALLOCATION", ["CAPITAL_EXPENDITURE"]),
}


def bootstrap(db, company_id, analysis_id, *, today=None):
    """Proposals only. Acceptance is a separate, user-visible write."""
    today = today or date.today()
    analysis, period, cutoff, registry, report, _ = analysis_evidence(db, company_id, analysis_id, today)
    proposals, seen = [], set()
    for key, source in registry.items():
        if source["kind"] != "CHECKPOINT_RECORD":
            continue
        name, category, metrics = CHECKPOINT_PILLARS.get(source.get("metric"),
            ("Management execution", "MANAGEMENT", []))
        if name in seen:
            continue
        seen.add(name)
        description = f"The investment case requires measurable delivery of the disclosed {source.get('metric', 'operating').lower().replace('_', ' ')} commitment."
        proposals.append(PillarInput(name=name, category=category, description=description,
            importance="CORE" if category in {"GROWTH", "EXECUTION"} else "IMPORTANT",
            scope=period.scope.value, created_from="MANAGEMENT_DERIVED", source_analysis_id=analysis.id,
            evidence_ids=[key, source["source_statement_id"]], metric_codes=metrics,
            expectations=[ExpectationInput(description="Verify the disclosed commitment through the existing management checkpoint.",
                expectation_type="CHECKPOINT", checkpoint_id=source["id"])]).model_dump(mode="json"))
    for statement in report.get("review_packet", {}).get("material_statements", []):
        key = statement["evidence_id"]
        if key not in registry:
            continue
        factors = set(statement.get("priority_factors", []))
        for factor, name, category, description in (
            ("DEMAND_CLAIM", "Demand conversion", "GROWTH",
             "The investment case requires disclosed demand to convert into signed business, revenue and cash; a management funnel is not earned revenue."),
            ("ASSET_ASSUMPTION", "Asset economics", "CAPITAL_ALLOCATION",
             "The investment case requires realized utilization, pricing and cash returns to substantiate management's asset economics assumptions."),
            ("FUNDING", "Funding capacity", "BALANCE_SHEET",
             "The investment case requires funding and cash generation to support planned investment without unproven financing assumptions."),
        ):
            if factor in factors and name not in seen:
                seen.add(name)
                proposals.append(PillarInput(name=name, category=category, description=description,
                    scope=period.scope.value, created_from="SYSTEM_INFERRED", source_analysis_id=analysis.id,
                    evidence_ids=[key], expectations=[ExpectationInput(description=description)]).model_dump(mode="json"))
    if "A_BASE" in registry:
        proposals.append(PillarInput(name="Valuation expectations", category="VALUATION",
            description="The investment case requires verified earnings execution to support the growth assumed in the existing valuation scenarios.",
            scope=period.scope.value, created_from="RESEARCH_DERIVED", source_analysis_id=analysis.id,
            evidence_ids=["A_BASE"], metric_codes=["BASIC_EPS"]).model_dump(mode="json"))
    return {"analysis_id": analysis.id, "evidence_as_of": cutoff.isoformat(), "proposals": proposals,
            "evidence": registry, "expectation_gap": expectation_gap(report, registry, cutoff),
            "note": "Proposed assumptions for your review. Accepting today establishes a new baseline; no earlier belief is inferred."}
