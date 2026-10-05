"""Focused zero-touch thesis tests with synthetic records and an opt-in ESDS check."""
from datetime import date
import os
import pytest
from sqlalchemy import select, func
from app.api.thesis import current
from app.ai.analyst.context_builder import build_context
from app.ai.analyst.service import analyze_period
from app.models.thesis import ThesisPillar, ThesisPillarEvaluation, ThesisEvaluationRun
from app.services.thesis import thesis_view, save_pillar
from app.services.thesis_auto import ensure_thesis, grouped_proposals
from app.services.thesis_evaluation import evaluate_thesis
from app.services.thesis_presentation import present_thesis
from tests.test_phase71 import db, issuer, result_period
from tests.test_phase72 import call
from tests.test_phase8 import saved, pillar
from tests.test_financials import financial_env
from tests.test_ai_analyst import setup as analysis_setup
from tests.test_ai_analyst import FakeProvider, valid


def researched(db):
    company = issuer(db, "RESEARCHED")
    period = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    call(db, company, period,
         "CEO: We target 200 GPUs and will deploy those by Q3 FY27.\n"
         "CFO: We are targeting PAT margins in the range of 15% to 20%.")
    analysis, _ = saved(db, company, period)
    context = build_context(db, company.name, company.symbol, period)
    analysis.evidence_registry = context.registry
    analysis.structured_output = {"investor_report": context.investor_report}
    db.flush()
    return company, period, analysis


def test_grouping_retains_distinct_evidence_and_does_not_force_unrelated_topics():
    from app.schemas.thesis import PillarInput, ExpectationInput
    def proposal(name, category, key, metric=None):
        return PillarInput(name=name, category=category, description="This requirement needs verified delivery.",
            created_from="SYSTEM_INFERRED", source_analysis_id=1, evidence_ids=[key],
            expectations=[ExpectationInput(description="Check the linked target.",
                expectation_type="CHECKPOINT", checkpoint_id=int(key[1:]))] if metric else []).model_dump(mode="json")
    parts = [proposal("Revenue commencement", "GROWTH", "K1", "REVENUE_START"),
             proposal("Capacity deployment", "EXECUTION", "K2", "GPU_DEPLOYMENT"),
             proposal("Profitability", "PROFITABILITY", "K3", "PAT_MARGIN"),
             proposal("Capital spending", "CAPITAL_ALLOCATION", "K4", "CAPEX"),
             proposal("Funding capacity", "BALANCE_SHEET", "S5"),
             proposal("Demand conversion", "GROWTH", "S6"),
             proposal("Valuation expectations", "VALUATION", "A_BASE"),
             proposal("Customer concentration", "BUSINESS_QUALITY", "S7")]
    registry = {"K1": {"source_quote": "Revenue from the AI factory is expected later."}}
    groups = grouped_proposals(parts, registry)
    assert len(groups) == 6
    assert len({definition.name for definition, _ in groups}) == 6
    capital = next((definition, originals) for definition, originals in groups if definition.name == "Profitability & Capital Returns")
    assert len(capital[1]) == 3 and set(capital[0].evidence_ids) == {"K3", "K4", "S5"}
    assert next(definition for definition, _ in groups if definition.name == "Customer concentration").category == "BUSINESS_QUALITY"


def test_automatic_bootstrap_is_idempotent_and_preserves_source_provenance(db):
    company, period, analysis = researched(db)
    created = ensure_thesis(db, company.id, analysis.id, today=date(2026, 8, 1))
    assert len(created) >= 2
    assert ensure_thesis(db, company.id, analysis.id, today=date(2026, 8, 1)) == []
    view = thesis_view(db, company.id, as_of=date(2026, 8, 1))
    assert len(view["pillars"]) == len(created)
    assert all(p["created_from"] == "SYSTEM_INFERRED" and not p["accepted_by_user"] for p in view["pillars"])
    assert all(p["source_components"] and p["source_evidence"] for p in view["pillars"])
    assert all(t["created_from"] == "SYSTEM_INFERRED" for p in view["pillars"] for t in p["expectations"])
    assert thesis_view(db, company.id, as_of=date(2026, 7, 31))["pillars"] == []


def test_current_read_bootstraps_existing_research_and_historical_read_is_read_only(db):
    company, period, analysis = researched(db)
    old = current("RESEARCHED", "CONSOLIDATED", date(2026, 7, 31), db)
    assert old["pillars"] == []
    assert db.scalar(select(func.count()).select_from(ThesisPillar)) == 0
    data = current("RESEARCHED", "CONSOLIDATED", None, db)
    assert len(data["overview"]["cards"]) == len(data["pillars"]) >= 2
    assert data["overview"]["generated_from_research"]
    assert data["overview"]["summary"] and data["overview"]["next_big_test"]
    count = db.scalar(select(func.count()).select_from(ThesisPillar))
    runs = db.scalar(select(func.count()).select_from(ThesisEvaluationRun))
    current("RESEARCHED", "CONSOLIDATED", None, db)
    assert db.scalar(select(func.count()).select_from(ThesisPillar)) == count
    assert db.scalar(select(func.count()).select_from(ThesisEvaluationRun)) == runs
    assert current("RESEARCHED", "CONSOLIDATED", date(2026, 7, 31), db)["overview"]["cards"] == []


def test_saved_quarterly_review_bootstraps_and_evaluates_automatically(financial_env):
    client, db, company, period, context, key, yoy = analysis_setup(financial_env)
    call(db, company, period,
         "CEO: We target 200 GPUs and will deploy those by Q3 FY27.\n"
         "CFO: We are targeting PAT margins in the range of 15% to 20%.")
    analysis = analyze_period(db, company, period, deterministic_only=True, force=True)
    count = db.scalar(select(func.count()).select_from(ThesisPillar))
    assert count >= 2
    impact = client.get(f"/api/analysis/{analysis.id}/thesis-impact").json()
    assert impact["run_id"] and len(impact["impacts"]) == count
    assert all(row["method"] == "DETERMINISTIC" for row in impact["impacts"])
    assert all(row["new_status"] not in {"STRENGTHENING", "BROKEN"} for row in impact["impacts"])


def test_successful_model_review_updates_thesis_without_another_model_call(financial_env):
    client, db, company, period, context, key, yoy = analysis_setup(financial_env)
    call(db, company, period,
         "CEO: We target 200 GPUs and will deploy those by Q3 FY27.\n"
         "CFO: We are targeting PAT margins in the range of 15% to 20%.")
    provider = FakeProvider([valid(key, yoy)])
    analysis = analyze_period(db, company, period, provider=provider, force=True)
    assert analysis.status == "SUCCESS"
    assert provider.calls == 1
    assert db.scalar(select(func.count()).select_from(ThesisPillar)) >= 2
    assert db.scalar(select(func.count()).select_from(ThesisEvaluationRun)) == 1


def test_manual_edits_and_evidence_rules_survive_auto_bootstrap(db):
    company, period, analysis = researched(db)
    created = ensure_thesis(db, company.id, analysis.id, today=date(2026, 8, 1))
    run = evaluate_thesis(db, company.id, analysis.id, today=date(2026, 8, 1), use_ai=False)
    first = thesis_view(db, company.id, as_of=date(2026, 8, 1))["pillars"][0]
    new = pillar(expected_version=first["version"]).model_copy(update={
        "name": first["name"], "category": first["category"],
        "description": first["description"], "created_from": first["created_from"],
        "scope": first["scope"], "source_analysis_id": first["source_analysis_id"], "evidence_ids": first["evidence_ids"],
        "metric_codes": first["metric_codes"], "importance": "SECONDARY", "reason": "Investor adjusted importance"})
    save_pillar(db, company.id, new, created[0].id, today=date(2026, 8, 2))
    assert thesis_view(db, company.id, as_of=date(2026, 8, 1))["pillars"][0]["importance"] == first["importance"]
    assert thesis_view(db, company.id, as_of=date(2026, 8, 2))["pillars"][0]["importance"] == "SECONDARY"
    assert db.scalar(select(func.count()).select_from(ThesisPillarEvaluation)) == len(run["impacts"])
    assert thesis_view(db, company.id, as_of=date(2026, 8, 1))["history"]


def test_manual_unverified_pillar_is_not_presented_as_verified_research(db):
    company = issuer(db, "PERSONAL")
    save_pillar(db, company.id, pillar(), today=date(2026, 8, 1))
    overview = present_thesis(thesis_view(db, company.id, as_of=date(2026, 8, 1)))
    assert overview["summary"].startswith("Your investment thesis includes")
    assert not overview["generated_from_research"]


@pytest.mark.skipif(os.getenv("ALMONDS_LIVE_REGRESSION") != "1", reason="Existing PostgreSQL corpus, opt in")
def test_esds_zero_touch_thesis_uses_existing_corpus_without_retaining_beliefs():
    from app.database import SessionLocal
    from app.models.analysis import AIAnalysis
    from app.models.company import Company
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.symbol == "ESDS"))
        analysis = db.scalar(select(AIAnalysis).where(AIAnalysis.company_id == company.id,
            AIAnalysis.status == "SUCCESS").order_by(AIAnalysis.id.desc()))
        before = db.scalar(select(func.count()).select_from(ThesisPillar).where(
            ThesisPillar.company_id == company.id))
        created = ensure_thesis(db, company.id, analysis.id)
        assert (3 <= len(created) <= 6) if before == 0 else created == []
        run = evaluate_thesis(db, company.id, analysis.id, use_ai=False)
        view = thesis_view(db, company.id)
        from app.models.financial import FinancialPeriod
        period = db.get(FinancialPeriod, analysis.financial_period_id)
        overview = present_thesis(view, analysis=analysis, period=period, registry=run["evidence"])
        assert overview["generated_from_research"]
        assert 3 <= len(overview["cards"]) <= 6
        assert overview["next_big_test"]["target"] == "Q3 FY27"
        assert "Sydney" in overview["next_big_test"]["question"]
        assert len({p["id"] for p in view["pillars"]}) == len(view["pillars"])
        assert 3 <= len(view["pillars"]) <= 6
        db.rollback()
