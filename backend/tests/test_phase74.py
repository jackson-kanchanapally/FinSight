"""Investor materiality and investment-story review regressions."""
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.ai.analyst.context_builder import build_context
from app.ai.analyst.quarterly_packet import quarterly_packet
from app.ai.analyst.quarterly_validation import assess_review
from app.ai.analyst.schemas import QuarterlyInvestorReview
from app.ai.analyst.schemas import ReviewItem
from app.ai.analyst.validator import InvalidAnalysis, validate_quarterly_review, validate_review_item
from app.services.materiality import materiality_details, select_material, short_quote
from tests.test_ai_analyst import setup as analysis_setup
from tests.test_financials import financial_env
from tests.test_phase72 import call
from tests.test_phase73 import _unique_review


def _statement(number, text):
    return SimpleNamespace(id=number, evidence_id=f"S{number}", page_number=number,
                           raw_statement=text)


def test_critical_timeline_and_capex_beat_routine_commentary():
    rows = [
        _statement(1, "We launched a minor software feature for internal users this quarter."),
        _statement(2, "Our earlier commissioning date was postponed; revenue now begins in Q3."),
        _statement(3, "We plan INR 150 crore of capex in FY27 for new capacity."),
        _statement(4, "Good morning and thank you for joining the call today."),
        _statement(5, "We target deployment of 300 units by Q4 FY27."),
    ]
    selected = select_material(rows, limit=3, annual_revenue_crore=100)
    assert [row.id for row, _ in selected][:2] == [2, 3]
    assert 4 not in [row.id for row, _ in selected]
    assert materiality_details(rows[2].raw_statement, annual_revenue_crore=100)["tier"] == "CRITICAL"
    assert "MATERIAL_TO_VERIFIED_ANNUAL_REVENUE" not in materiality_details(rows[2].raw_statement)["factors"]


def test_unusual_asset_assumption_survives_excerpt_and_is_a_claim():
    text = ("We collect customer advances. This is an appreciating asset. "
            "This is no longer a depreciating commodity. We do not see customer concentration.")
    details = materiality_details(text)
    assert details["tier"] == "HIGH"
    assert "ASSET_ASSUMPTION" in details["factors"]
    assert "appreciating asset" in short_quote(text, 180)


def test_diversity_preserves_demand_and_asset_claims_under_cap():
    rows = [
        _statement(1, "We expect revenue from the new factory to begin in Q3 FY27."),
        _statement(2, "We expect revenue from another new facility to begin in Q4 FY27."),
        _statement(3, "We expect revenue from the third facility to begin in Q1 FY28."),
        _statement(4, "We have a funnel of 30,000 units but contracts are still being discussed."),
        _statement(5, "We argue these machines are appreciating assets rather than depreciating equipment."),
    ]
    ids = [row.id for row, _ in select_material(rows, limit=3)]
    assert 4 in ids and 5 in ids


def test_what_changed_requires_story_evidence_and_preserves_reported_revision(financial_env):
    _, db, company, period, _, _, _ = analysis_setup(financial_env)
    call(db, company, period, "CEO: Our earlier September launch date was postponed. "
         "We now expect revenue from the new facility in Q3 FY27.")
    context = build_context(db, company.name, company.symbol, period)
    report = context.investor_report
    changes = report["quarterly_review"]["what_changed"]
    assert len(changes) == 1
    assert changes[0]["basis"] == "MANAGEMENT_CLAIM"
    assert "QoQ" not in changes[0]["text"]
    assert changes[0]["source_quote"] in context.registry[changes[0]["evidence_ids"][0]]["source_quote"]
    validate_quarterly_review(QuarterlyInvestorReview.model_validate(
        report["quarterly_review"]), context.registry, period.period_end.year)


def test_no_reported_revision_means_baseline_not_qoq_change(financial_env):
    _, db, company, period, _, _, _ = analysis_setup(financial_env)
    call(db, company, period, "CEO: We target a new service launch in Q3 FY27.")
    report = build_context(db, company.name, company.symbol, period).investor_report
    assert report["quarterly_review"]["what_changed"] == []
    assert "Baseline established this quarter" in report["history_note"]


def test_semantic_restatement_is_rejected_without_losing_other_items():
    draft, registry = _unique_review()
    draft["key_developments"] = [{
        "text": "Revenue grew, but persistence remains untested today.",
        "evidence_ids": ["M1"], "basis": "INTERPRETATION"}]
    assessment = assess_review(draft, registry, 2026, draft)
    assert any(row["reason"] == "DUPLICATE_REVIEW_ITEM" for row in assessment.rejected)
    assert assessment.review.quarterly_investor_view[0].text == draft["quarterly_investor_view"][0]["text"]


def test_validated_review_fills_open_slots_for_material_claims():
    draft, registry = _unique_review()
    draft["key_developments"] = [{
        "text": "The revenue trend needs another period of evidence.",
        "evidence_ids": ["M1"], "basis": "INTERPRETATION"}]
    draft["key_risks"] = [{
        "text": "The persistence of reported growth remains uncertain.",
        "evidence_ids": ["M1"], "basis": "INTERPRETATION"}]
    draft["management_signals"] = [{
        "text": "Management expects stronger customer demand next year.",
        "evidence_ids": ["S1"], "basis": "MANAGEMENT_CLAIM",
        "source_quote": "We expect stronger customer demand next year."}]
    capex = "We plan significant capex for new capacity in FY27."
    funding = "We plan to fund the new capacity using customer advances and debt."
    asset = "We think these machines are appreciating assets."
    for number, quote in enumerate((capex, funding, asset), 2):
        registry[f"S{number}"] = {"kind": "MANAGEMENT_STATEMENT", "source_quote": quote,
                                  "excerpt": quote}
    deterministic = dict(draft)
    deterministic["key_developments"] = [{
        "text": "Management plans significant capex; verify spending and returns.",
        "evidence_ids": ["S2"], "basis": "MANAGEMENT_CLAIM", "source_quote": capex}, {
        "text": "Management proposes advances and debt as funding sources; verify actual receipts and borrowing.",
        "evidence_ids": ["S3"], "basis": "MANAGEMENT_CLAIM", "source_quote": funding}]
    deterministic["key_risks"] = [{
        "text": "Management's asset appreciation assumption requires testing against realized pricing and depreciation.",
        "evidence_ids": ["S4"], "basis": "MANAGEMENT_CLAIM", "source_quote": asset}]
    assessment = assess_review(draft, registry, 2026, deterministic)
    assert any("capex" in row.text for row in assessment.review.key_developments)
    assert any("funding" in row.text for row in assessment.review.management_signals)
    assert any("appreciation" in row.text for row in assessment.review.key_risks)
    assert assessment.fallback_items >= 3


def test_undated_target_cannot_acquire_fiscal_year_and_delay_needs_source():
    quote = "We are targeting PAT margins in the range of 15% to 20%."
    registry = {
        "S1": {"kind": "MANAGEMENT_STATEMENT", "source_quote": quote, "excerpt": quote},
        "K1": {"kind": "CHECKPOINT_RECORD", "source_statement_id": "S1", "status": "NOT_YET_ASSESSABLE",
               "target_period": None, "target_date": None, "target": {"lower": "15", "upper": "20"},
               "unit": "PERCENT"},
    }
    item = ReviewItem(text="Management targets PAT margin of 15% to 20% for FY27.",
                      evidence_ids=["S1", "K1"], basis="MANAGEMENT_CLAIM", source_quote=quote)
    with pytest.raises(InvalidAnalysis, match="UNSUPPORTED_TARGET_PERIOD"):
        validate_review_item(item, "key_developments", registry, 2026)
    deployment = "We target deployment of the new facility next quarter."
    registry["S2"] = {"kind": "MANAGEMENT_STATEMENT", "source_quote": deployment, "excerpt": deployment}
    item = ReviewItem(text="Management claims a delay in deployment.",
                      evidence_ids=["S2"], basis="MANAGEMENT_CLAIM", source_quote=deployment)
    with pytest.raises(InvalidAnalysis, match="CHECKPOINT_OUTCOME_UNSUPPORTED"):
        validate_review_item(item, "management_signals", registry, 2026)


def test_materiality_metadata_does_not_create_a_ratio():
    details = materiality_details("We plan INR 150 crore of capex in FY27 for new capacity.",
                                  annual_revenue_crore=100)
    assert details["tier"] == "CRITICAL"
    assert set(details) == {"category", "tier", "factors"}
    assert all(isinstance(value, str) for value in details["factors"])


def test_packet_prioritizes_cash_gap_and_watch_is_measurable(financial_env):
    _, db, company, period, _, _, _ = analysis_setup(financial_env)
    call(db, company, period, "CEO: We target deployment of 200 GPUs by Q3 FY27.")
    context = build_context(db, company.name, company.symbol, period)
    packet = json.loads(quarterly_packet(context)[0])
    assert "Cash conversion" in packet["research_gaps"]["items"][0]
    assert "priority_factors" in packet["management_statements"][0]
    assert packet["management_statements"][0]["tier"] in {"CRITICAL", "HIGH", "MEDIUM", "LOW", "NOISE"}
    assert packet["management_statements"][0]["page"] is not None
    watch = context.investor_report["quarterly_review"]["watch_next_quarter"]
    assert any("Q3FY27" in row["text"] and "Verify" in row["text"] for row in watch)
    assert len(watch) <= 5


@pytest.mark.skipif(os.getenv("RUN_PHASE74_LIVE") != "1", reason="Uses existing local ESDS corpus")
def test_live_esds_priority_and_validated_review():
    from app.database import SessionLocal
    from app.models.company import Company
    from app.models.financial import FinancialPeriod, FinancialScope
    from app.models.analysis import AIAnalysis
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.symbol == "ESDS"))
        period = db.scalar(select(FinancialPeriod).where(
            FinancialPeriod.company_id == company.id, FinancialPeriod.fiscal_year == "FY2027",
            FinancialPeriod.quarter == 1, FinancialPeriod.scope == FinancialScope.CONSOLIDATED))
        context = build_context(db, company.name, company.symbol, period)
        packet = json.loads(quarterly_packet(context)[0])
        factors = {factor for row in packet["management_statements"] for factor in row["priority_factors"]}
        assert {"TIMELINE_REVISION", "CAPITAL_COMMITMENT", "FUNDING", "DEMAND_CLAIM",
                "SUPPLY_RISK", "ASSET_ASSUMPTION"} <= factors
        assert packet["supported_changes"][0]["type"] == "TIMELINE_DELAYED"
        assert all("QoQ" not in row["text"] for row in context.investor_report["quarterly_review"]["what_changed"])
        saved = db.scalar(select(AIAnalysis).where(
            AIAnalysis.financial_period_id == period.id, AIAnalysis.status == "SUCCESS").order_by(AIAnalysis.id.desc()))
        assert saved is not None
        validate_quarterly_review(QuarterlyInvestorReview.model_validate(
            saved.structured_output["quarterly_review"]), saved.evidence_registry, period.period_end.year)
        db.rollback()
