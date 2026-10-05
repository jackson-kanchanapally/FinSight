"""Evidence-safe synthesis and selective repair of quarterly reviews."""

import json
import os

import pytest

from app.ai.analyst.quarterly_packet import quarterly_packet
from app.ai.analyst.context_builder import build_context
from app.ai.analyst.quarterly_validation import assess_review, apply_repairs, repair_prompt
from app.ai.analyst.schemas import ReviewItem
from app.ai.analyst.service import analyze_period
from app.ai.analyst.prompts import PROMPT_VERSION
from app.ai.analyst.schemas import QuarterlyInvestorReview
from app.ai.analyst.validator import InvalidAnalysis, validate_review_item, validate_quarterly_review
from app.ai.providers.base import LLMProvider
from tests.test_ai_analyst import setup as analysis_setup
from tests.test_financials import financial_env
from tests.test_phase72 import call, review_fixture


def _unique_review():
    draft, registry = review_fixture()
    draft["quarterly_investor_view"] = [
        {"text": "Revenue grew, but persistence remains untested.", "evidence_ids": ["M1"], "basis": "INTERPRETATION"},
        {"text": "The next quarter will help establish whether growth persists.", "evidence_ids": ["M1"], "basis": "INTERPRETATION"},
    ]
    draft["key_developments"] = []
    draft["watch_next_quarter"] = []
    draft["investor_conclusion"] = {"text": "The reported growth warrants a follow-up check.",
        "evidence_ids": ["M1"], "basis": "INTERPRETATION"}
    draft["valuation_implication"] = {"text": "There is too little evidence to reassess assumptions.",
        "evidence_ids": ["I_REVIEW"], "basis": "INTERPRETATION", "status": "INSUFFICIENT_EVIDENCE"}
    return draft, registry


def test_multi_evidence_synthesis_keeps_source_as_claim():
    _, registry = _unique_review()
    registry["S1"]["source_quote"] = "We target deployment of the new service next quarter."
    item = ReviewItem.model_validate({
        "text": "Revenue grew; management targets a new service, whose delivery remains to be verified.",
        "evidence_ids": ["M1", "S1"], "basis": "INTERPRETATION",
        "source_quote": "We target deployment of the new service next quarter.",
    })
    validate_review_item(item, "key_developments", registry, 2026)
    with pytest.raises(InvalidAnalysis, match="MANAGEMENT_CLAIM_IS_NOT_FACT"):
        validate_review_item(item.model_copy(update={"basis": "VERIFIED_FACT"}),
                             "key_developments", registry, 2026)


def test_named_speaker_and_checkpoint_quote_are_attributed():
    _, registry = _unique_review()
    registry["S1"].update({"speaker_name": "Anita Rao", "source_quote":
        "We target deployment of the new service next quarter."})
    registry["K1"] = {"kind": "CHECKPOINT_RECORD", "source_statement_id": "S1",
        "status": "PENDING", "target_period": "Q2FY2027"}
    item = ReviewItem(text="Anita Rao says management targets the new service next quarter.",
        evidence_ids=["K1"], basis="MANAGEMENT_CLAIM",
        source_quote="We target deployment of the new service next quarter.")
    validate_review_item(item, "management_signals", registry, 2026)
    with pytest.raises(InvalidAnalysis, match="CHECKPOINT_OUTCOME_UNSUPPORTED"):
        validate_review_item(item.model_copy(update={"text": "Anita Rao says the target was missed."}),
                             "management_signals", registry, 2026)


def test_management_explanation_remains_attributed():
    _, registry = _unique_review()
    quote = "We believe timing of customer approvals delayed the launch."
    registry["S1"].update({"source_quote": quote, "excerpt": quote,
                            "speaker_name": "Anita Rao"})
    attributed = ReviewItem(text="Anita Rao attributes the launch delay to customer approval timing.",
        evidence_ids=["S1"], basis="MANAGEMENT_CLAIM", source_quote=quote,
        causal_basis="MANAGEMENT_EXPLANATION")
    validate_review_item(attributed, "management_signals", registry, 2026)
    with pytest.raises(InvalidAnalysis, match="MANAGEMENT_ATTRIBUTION_REQUIRED|CAUSAL_ATTRIBUTION_REQUIRED"):
        validate_review_item(attributed.model_copy(update={"text": "Customer approvals caused the delay."}),
                             "management_signals", registry, 2026)


@pytest.mark.parametrize("text,basis,causal,reason", [
    ("Cost control drove profit higher.", "INTERPRETATION", None, "UNSUPPORTED_CAUSAL_EXPLANATION"),
    ("Profit rose because of cost control.", "INTERPRETATION", "ESTABLISHED_CAUSE", "UNSUPPORTED_CAUSAL_EXPLANATION"),
    ("The shares are undervalued at 999 times earnings.", "INTERPRETATION", None, "UNSUPPORTED_NUMBER"),
    ("Profit rose due to unknown factors.", "INTERPRETATION", None, "CAUSAL_BASIS_REQUIRED"),
    ("Profit rose aided by lower tax expenses.", "INTERPRETATION", None, "CAUSAL_BASIS_REQUIRED"),
])
def test_unsafe_causal_or_valuation_claims_rejected(text, basis, causal, reason):
    _, registry = _unique_review()
    item = ReviewItem(text=text, evidence_ids=["M1"], basis=basis, causal_basis=causal)
    with pytest.raises(InvalidAnalysis, match=reason):
        validate_review_item(item, "key_developments", registry, 2026)


def test_explicit_unknown_and_conditional_hypothesis():
    _, registry = _unique_review()
    for text, causal in [
        ("The cause of the reported growth is unknown.", "UNKNOWN_CAUSE"),
        ("If growth persists, the earnings case could strengthen.", "INVESTOR_HYPOTHESIS"),
    ]:
        validate_review_item(ReviewItem(text=text, evidence_ids=["M1"],
                             basis="INTERPRETATION", causal_basis=causal),
                             "key_developments", registry, 2026)


def test_pat_bridge_cannot_explain_revenue_causally():
    _, registry = _unique_review()
    registry["J1"] = {"kind": "METRIC", "metric_code": "PAT_BRIDGE_TOTAL_TAX_EXPENSE_YOY",
                      "value": "2", "unit": "INR_CRORE", "input_evidence_ids": []}
    item = ReviewItem(text="Revenue grew because of lower tax expenses.",
                      evidence_ids=["M1", "J1"], basis="VERIFIED_FACT",
                      causal_basis="ESTABLISHED_CAUSE")
    with pytest.raises(InvalidAnalysis, match="CAUSAL_BASIS_UNSUPPORTED"):
        validate_review_item(item, "key_developments", registry, 2026)


def test_research_gap_claim_requires_its_context_evidence():
    _, registry = _unique_review()
    registry["I_REVIEW"]["research_gaps"] = [
        "Historical valuation coverage is too short to establish a representative P/E range."]
    item = ReviewItem(text="Historical valuation coverage is too short for a representative P/E range.",
                      evidence_ids=["M1"], basis="INTERPRETATION")
    with pytest.raises(InvalidAnalysis, match="RESEARCH_GAP_UNSUPPORTED"):
        validate_review_item(item, "key_risks", registry, 2026)
    validate_review_item(item.model_copy(update={"evidence_ids": ["I_REVIEW"]}),
                         "key_risks", registry, 2026)


def test_comparable_data_contradiction_and_unbenchmarked_growth_rejected():
    _, registry = _unique_review()
    for text, reason in [
        ("No prior comparable data is available for comparison.", "COMPARABLE_DATA_CONTRADICTION"),
        ("No comparable data is available to validate seasonality.", "COMPARABLE_DATA_CONTRADICTION"),
        ("The company delivered strong YoY growth.", "UNSUPPORTED_QUALITATIVE_STRENGTH"),
    ]:
        item = ReviewItem(text=text, evidence_ids=["M1"], basis="INTERPRETATION")
        with pytest.raises(InvalidAnalysis, match=reason):
            validate_review_item(item, "investor_conclusion", registry, 2026)


def test_partial_validation_and_repair_preserve_valid_items():
    draft, registry = _unique_review()
    draft["key_developments"] = [{"text": "Cost control drove profit higher.",
        "evidence_ids": ["M1"], "basis": "INTERPRETATION"}]
    first = assess_review(draft, registry, 2026, _unique_review()[0])
    assert first.accepted_count == 4
    assert first.rejected[0]["section"] == "key_developments"
    assert first.diagnostics[0]["item_index"] == 0
    repaired = apply_repairs(draft, {"repairs": [
        {"section": "key_developments", "index": 0,
         "item": {"text": "The cause of the reported growth is unknown.",
                  "evidence_ids": ["M1"], "basis": "INTERPRETATION",
                  "causal_basis": "UNKNOWN_CAUSE"}},
        {"section": "investor_conclusion", "index": -1,
         "item": {"text": "The shares are cheap.", "evidence_ids": ["M1"],
                  "basis": "INTERPRETATION"}},
    ]}, first.rejected)
    assert repaired["investor_conclusion"] == draft["investor_conclusion"]
    final = assess_review(repaired, registry, 2026, _unique_review()[0])
    assert final.accepted_count == 5 and not final.rejected


def test_valid_checkpoint_development_leads_two_fact_only_opening():
    draft, registry = _unique_review()
    for item in draft["quarterly_investor_view"]:
        item["basis"] = "VERIFIED_FACT"
    quote = "We target the new service deployment next quarter."
    registry["S1"].update({"source_quote": quote, "excerpt": quote})
    registry["K1"] = {"kind": "CHECKPOINT_RECORD", "source_statement_id": "S1",
                      "status": "PENDING", "target_period": "Q2FY2027"}
    draft["key_developments"] = [{"text": "Management targets a new service next quarter.",
        "evidence_ids": ["S1", "K1"], "basis": "MANAGEMENT_CLAIM", "source_quote": quote}]
    assessment = assess_review(draft, registry, 2026, _unique_review()[0])
    assert not assessment.rejected
    assert assessment.review.quarterly_investor_view[1].text == draft["key_developments"][0]["text"]
    assert assessment.review.key_developments[0].text == draft["quarterly_investor_view"][1]["text"]


def test_speculative_fact_is_rejected_and_attributed_development_fills_view():
    draft, registry = _unique_review()
    draft["quarterly_investor_view"][0]["basis"] = "VERIFIED_FACT"
    draft["quarterly_investor_view"][1] = {"text": "Growth suggests seasonal weakness.",
        "evidence_ids": ["M1"], "basis": "VERIFIED_FACT"}
    quote = "We target a new service launch next quarter."
    registry["S1"].update({"source_quote": quote, "excerpt": quote})
    draft["key_developments"] = [{"text": "Management targets a new service launch next quarter.",
        "evidence_ids": ["S1"], "basis": "MANAGEMENT_CLAIM", "source_quote": quote}]
    assessment = assess_review(draft, registry, 2026, _unique_review()[0])
    assert assessment.rejected[0]["reason"] == "INTERPRETATION_MISCLASSIFIED"
    assert assessment.review.quarterly_investor_view[1].text == draft["key_developments"][0]["text"]
    assert assessment.fallback_items == 0


def test_repair_receives_original_response_and_only_relevant_source_rows():
    packet = json.dumps({"management_statements": [
        {"evidence_id": "S1", "statement": "We target a launch next quarter."},
        {"evidence_id": "S2", "statement": "Unrelated long statement."}],
        "checkpoints": [], "historical_memory": {"passages": [], "guidance": []}})
    raw = {"management_signals": [{"text": "Unsupported claim", "evidence_ids": ["S1"]}]}
    rejected = [{"section": "management_signals", "index": 0,
                 "reason": "MANAGEMENT_QUOTE_NOT_IN_SOURCE", "original": raw["management_signals"][0]}]
    prompt = repair_prompt(packet, raw, rejected, ["S1", "S2"])
    assert "ORIGINAL_STRUCTURED_OUTPUT" in prompt
    relevant = json.loads(prompt.split("RELEVANT_EVIDENCE_PACKET:", 1)[1])
    assert [row["evidence_id"] for row in relevant["management_statements"]] == ["S1"]


def test_model_cannot_cite_registry_evidence_omitted_from_packet():
    draft, registry = _unique_review()
    registry["Q1"] = {"kind": "MARKET_SNAPSHOT", "pe_ratio": "163"}
    draft["key_developments"] = [{"text": "A reference P/E is available.",
        "evidence_ids": ["Q1"], "basis": "INTERPRETATION"}]
    assessment = assess_review(draft, registry, 2026, _unique_review()[0],
                               allowed_ids=["M1", "I_REVIEW"])
    assert assessment.rejected[0]["reason"] == "EVIDENCE_NOT_IN_PACKET"


def test_packet_keeps_page_provenance_and_gaps_without_reference_pe(financial_env):
    _, _, _, _, context, _, _ = analysis_setup(financial_env)
    packet, ids, count = quarterly_packet(context)
    data = json.loads(packet)
    assert data["verified_facts"]
    assert all("source_document_id" in row and "page" in row for row in data["verified_facts"])
    assert data["research_gaps"]["evidence_id"] == "I_REVIEW"
    assert data["research_gaps"]["items"] == context.investor_report["research_gaps"][:4]
    assert all(row["evidence_id"] in ids for row in data["seasonality_history"])
    assert data["source"]["origin"]
    assert "reference_pe" not in packet.lower()
    assert all(key in context.registry for key in ids)
    assert count == len(data["historical_memory"]["passages"])
    assert quarterly_packet(context)[1] == ids
    assert data["period"]["scope"] == context.registry[data["verified_facts"][0]["evidence_id"]]["scope"]
    assert all(row["period_end"] <= data["period"]["end"] for row in data["verified_facts"])


def test_deterministic_review_items_are_valid_with_and_without_commentary(financial_env):
    _, db, company, period, context, _, _ = analysis_setup(financial_env)
    validate_quarterly_review(QuarterlyInvestorReview.model_validate(
        context.investor_report["quarterly_review"]), context.registry, period.period_end.year)
    call(db, company, period, "CEO: We target deployment of the new service by Q3 FY27.")
    updated = build_context(db, company.name, company.symbol, period)
    validate_quarterly_review(QuarterlyInvestorReview.model_validate(
        updated.investor_report["quarterly_review"]), updated.registry, period.period_end.year)


def test_service_repairs_only_bad_item_and_keeps_valid_reasoning(financial_env):
    _, db, company, period, _, _, yoy = analysis_setup(financial_env)
    draft, _ = _unique_review()
    for section in ("quarterly_investor_view", "investor_conclusion"):
        rows = draft[section] if isinstance(draft[section], list) else [draft[section]]
        for item in rows:
            item["evidence_ids"] = [yoy]
    draft["key_developments"] = [{"text": "Cost control drove profits higher.",
        "evidence_ids": [yoy], "basis": "INTERPRETATION"}]

    class Provider(LLMProvider):
        supports_quarterly_review = True
        name, model = "repair-test", "fixture"
        calls = 0

        def generate(self, system, user, schema):
            self.calls += 1
            if self.calls == 1:
                return json.dumps({"quarterly_review": draft})
            assert schema["required"] == ["repairs"]
            return json.dumps({"repairs": [{"section": "key_developments", "index": 0,
                "item": {"text": "The cause of the reported growth remains unknown.",
                         "evidence_ids": [yoy], "basis": "INTERPRETATION",
                         "causal_basis": "UNKNOWN_CAUSE"}}]})

    provider = Provider()
    result = analyze_period(db, company, period, provider)
    assert result.status == "SUCCESS", result.validation_diagnostics
    assert provider.calls == 2
    trace = result.structured_output["review_trace"]
    assert trace["generation_status"] == "VALIDATED_AFTER_REPAIR"
    assert trace["items_rejected_initially"] == 1
    assert trace["items_rejected_finally"] == 0
    assert result.structured_output["quarterly_review"]["quarterly_investor_view"][0]["text"] == draft["quarterly_investor_view"][0]["text"]


def test_unrepaired_optional_item_keeps_supported_review(financial_env):
    _, db, company, period, _, _, yoy = analysis_setup(financial_env)
    draft, _ = _unique_review()
    for section in ("quarterly_investor_view", "investor_conclusion"):
        rows = draft[section] if isinstance(draft[section], list) else [draft[section]]
        for item in rows:
            item["evidence_ids"] = [yoy]
    draft["key_developments"] = [{"text": "Cost control drove profit higher.",
        "evidence_ids": [yoy], "basis": "INTERPRETATION"}]

    class Provider(LLMProvider):
        supports_quarterly_review = True
        name, model = "partial-test", "fixture"
        calls = 0

        def generate(self, system, user, schema):
            self.calls += 1
            return json.dumps({"quarterly_review": draft}) if self.calls == 1 else json.dumps({"repairs": []})

    provider = Provider()
    result = analyze_period(db, company, period, provider)
    assert result.status == "SUCCESS"
    assert provider.calls == 2
    assert result.structured_output["review_trace"]["generation_status"] == "PARTIAL_VALIDATED"
    assert result.structured_output["review_trace"]["items_rejected_finally"] == 1
    assert result.structured_output["quarterly_review"]["key_developments"]
    assert all("Cost control" not in row["text"] for row in
               result.structured_output["quarterly_review"]["key_developments"])


def test_unusable_quarterly_json_uses_bounded_deterministic_fallback(financial_env):
    _, db, company, period, _, _, _ = analysis_setup(financial_env)

    class Provider(LLMProvider):
        supports_quarterly_review = True
        name, model = "invalid-test", "fixture"
        calls = 0

        def generate(self, system, user, schema):
            self.calls += 1
            return "{invalid"

    provider = Provider()
    result = analyze_period(db, company, period, provider)
    assert result.status == "VERIFIED_FALLBACK"
    assert result.failure_reason == "INVALID_JSON_OR_SCHEMA"
    assert provider.calls == 2
    assert result.structured_output["review_trace"]["generation_status"] == "VERIFIED_FALLBACK"


@pytest.mark.skipif(os.getenv("RUN_PHASE73_LIVE") != "1", reason="Uses existing local ESDS corpus")
def test_live_esds_saved_qwen_review_is_evidence_valid():
    """Read-only check of the current local corpus; never creates facts or documents."""
    from sqlalchemy import select
    from app.database import SessionLocal
    from app.models.analysis import AIAnalysis
    from app.models.company import Company
    from app.models.financial import FinancialPeriod, FinancialScope

    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.symbol == "ESDS"))
        assert company is not None
        period = db.scalar(select(FinancialPeriod).where(
            FinancialPeriod.company_id == company.id,
            FinancialPeriod.fiscal_year == "FY2027", FinancialPeriod.quarter == 1,
            FinancialPeriod.scope == FinancialScope.CONSOLIDATED))
        assert period is not None
        analysis = db.scalar(select(AIAnalysis).where(
            AIAnalysis.financial_period_id == period.id,
            AIAnalysis.prompt_version == PROMPT_VERSION,
            AIAnalysis.model_provider == "ollama").order_by(AIAnalysis.id.desc()))
        assert analysis is not None and analysis.status == "SUCCESS"
        trace = analysis.structured_output["review_trace"]
        assert trace["model_items_accepted"] >= 2
        assert trace["fallback_reason"] is None
        validate_quarterly_review(QuarterlyInvestorReview.model_validate(
            analysis.structured_output["quarterly_review"]), analysis.evidence_registry,
            period.period_end.year)
