import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ai.analyst.context_builder import build_context
from app.ai.analyst.fallback import verified_fallback
from app.ai.analyst.prompts import PROMPT_VERSION
from app.ai.analyst.service import analyze_period
from app.ai.analyst.validator import InvalidAnalysis, validate_output
from app.ai.providers.base import LLMError, LLMProvider
from app.ai.providers.ollama import OllamaProvider
from app.models.analysis import AIAnalysis
from app.models.company import Company
from app.models.financial import FinancialFact, FinancialPeriod, FinancialScope, FinancialStatus
from app.models.market import MarketSnapshot
from app.services.financials.service import process_financial_event
from tests.test_financials import financial_env


class FakeProvider(LLMProvider):
    name = "fake"
    model = "fixture-v1"

    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = 0
        self.prompts = []
        self.schemas = []

    def generate(self, system, user, schema):
        self.calls += 1
        self.prompts.append(user)
        self.schemas.append(schema)
        assert "almonds" in system and "CONSOLIDATED" in user
        assert {"what_might_happen", "result_view", "valuation_assessment", "research_action"}.issubset(schema["required"])
        if self.error:
            raise LLMError(self.error)
        return self.responses.pop(0)


def test_ollama_request_expands_context_window(monkeypatch):
    from app.ai.providers import ollama

    def fake_post(url, **kwargs):
        assert url == "http://localhost:11434/api/chat"
        assert kwargs["json"]["options"]["num_ctx"] == 8192
        assert kwargs["json"]["options"]["temperature"] == 0.1

        class Response:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": "{}"}}

        return Response()

    monkeypatch.setattr(ollama.httpx, "post", fake_post)
    assert OllamaProvider("http://localhost:11434", "qwen3:8b", 120, 0.1).generate(
        "system", "user", {}) == "{}"


def setup(financial_env):
    client, db, storage, event_id = financial_env
    process_financial_event(db, event_id, storage=storage)
    company = db.scalar(select(Company).where(Company.symbol == "ESDS"))
    period = db.scalar(select(FinancialPeriod).where(FinancialPeriod.company_id == company.id,
        FinancialPeriod.scope == FinancialScope.CONSOLIDATED,
        FinancialPeriod.period_end == "2026-06-30"))
    context = build_context(db, company.name, company.symbol, period)
    revenue = next(key for key, item in context.registry.items()
                   if item["kind"] == "FACT" and item["metric_code"] == "REVENUE_FROM_OPERATIONS"
                   and item["period_id"] == period.id)
    yoy = next(key for key, item in context.registry.items()
               if item["kind"] == "METRIC" and item["metric_code"] == "REVENUE_YOY")
    return client, db, company, period, context, revenue, yoy


def valid(revenue, yoy, *, extended=True):
    result = {"summary": "Revenue increased year over year; quarter comparison is also available.",
        "summary_evidence_ids": [yoy],
        "positives": [{"title": "Revenue improved", "explanation": "Revenue rose 7.28% YoY.",
                       "evidence_ids": [yoy]}], "concerns": [],
        "material_changes": [{"metric": "Revenue YoY", "change": "+7.28%",
                              "interpretation": "Reported revenue is ₹133.66 Cr.",
                              "evidence_ids": [yoy, revenue]}],
        "questions": ["What explains the quarter change?"],
        "data_quality_notes": ["Only validated data was considered."]}
    if extended:
        result.update(
            what_might_happen={"text": "If revenue growth persists, the next results could confirm the trend.",
                               "evidence_ids": [yoy]},
            result_view={"sentiment": "POSITIVE", "reason": "Revenue improved YoY.",
                         "evidence_ids": [yoy]},
            valuation_assessment={"status": "INSUFFICIENT_DATA",
                                  "verdict": "INCONCLUSIVE",
                                  "text": "A sourced market snapshot is unavailable.",
                                  "evidence_ids": []},
            research_action={"action": "INVESTIGATE",
                             "reason": "Investigate the drivers of the reported revenue change.",
                             "evidence_ids": [yoy]},
        )
    return json.dumps(result)


def test_valid_persistence_prompt_model_and_duplicate(financial_env):
    client, db, company, period, context, revenue, yoy = setup(financial_env)
    provider = FakeProvider([valid(revenue, yoy)])
    first = analyze_period(db, company, period, provider)
    assert first.status == "SUCCESS" and first.summary
    assert any("QoQ" in claim["explanation"] for claim in first.structured_output["concerns"])
    assert first.prompt_version == PROMPT_VERSION
    assert first.model_provider == "fake" and first.model_name == "fixture-v1"
    assert first.context_hash == context.hash and first.attempt_count == 1
    assert first.evidence_registry[yoy]["input_evidence_ids"]
    assert analyze_period(db, company, period, provider).id == first.id
    assert provider.calls == 1
    assert client.get(f"/api/analysis/{first.id}").json()["structured_output"]["positives"]
    assert client.get(f"/api/analysis/{first.id}").json()["current_prompt_version"] == PROMPT_VERSION
    assert client.get("/api/companies/ESDS/analysis").json()[0]["id"] == first.id
    regenerated = analyze_period(db, company, period, FakeProvider([valid(revenue, yoy)]), regenerate=True)
    assert regenerated.id != first.id and regenerated.status == "SUCCESS"
    assert db.scalar(select(FinancialFact).where(FinancialFact.id == int(revenue[1:]))).value == Decimal("133.657")


@pytest.mark.parametrize("first,reason", [
    ("{broken", "INVALID_JSON_OR_SCHEMA"),
    (json.dumps({"summary": "x"}), "INVALID_JSON_OR_SCHEMA"),
    (json.dumps({"summary": "Revenue", "summary_evidence_ids": ["F1"], "positives": [{"title": "a", "explanation": "b",
                 "evidence_ids": ["F999"]}], "concerns": [], "material_changes": [],
                 "questions": [], "data_quality_notes": []}), "UNKNOWN_EVIDENCE_ID"),
])
def test_invalid_first_response_retries(financial_env, first, reason):
    _, db, company, period, _, revenue, yoy = setup(financial_env)
    record = analyze_period(db, company, period, FakeProvider([first, valid(revenue, yoy)]))
    assert record.status == "SUCCESS" and record.attempt_count == 2


def test_model_prompt_uses_current_period_citation_guide(financial_env):
    _, db, company, period, context, revenue, yoy = setup(financial_env)
    assert "document_url" not in context.text
    assert context.registry[revenue]["document_url"]
    provider = FakeProvider([valid(revenue, yoy)])
    assert analyze_period(db, company, period, provider).status == "SUCCESS"
    guide = provider.prompts[0].split("CITATION GUIDE FOR THIS ANALYSIS", 1)[1]
    assert f"{yoy}: REVENUE_YOY" in provider.prompts[0]
    assert f"{revenue}: current REVENUE_FROM_OPERATIONS" in guide
    assert "M1:" not in guide
    assert provider.schemas[0]["properties"]["summary_evidence_ids"]["items"] == {"type": "string"}


def test_numeric_repair_names_matching_metric_id(financial_env):
    _, db, company, period, _, revenue, yoy = setup(financial_env)
    invalid = json.loads(valid(revenue, yoy))
    invalid["positives"][0]["evidence_ids"] = [revenue]
    provider = FakeProvider([json.dumps(invalid), valid(revenue, yoy)])
    record = analyze_period(db, company, period, provider)
    assert record.status == "SUCCESS" and record.attempt_count == 2
    assert f"{yoy} (REVENUE_YOY)" in provider.prompts[1]


def test_numeric_repair_lists_all_bad_fields(financial_env):
    _, db, company, period, context, revenue, yoy = setup(financial_env)
    pat_yoy = next(key for key, item in context.registry.items()
                   if item["kind"] == "METRIC" and item["metric_code"] == "PAT_YOY")
    invalid = json.loads(valid(revenue, yoy))
    invalid["summary"] = "Revenue rose 7.28% YoY and PAT rose 13.98% YoY."
    invalid["material_changes"].append({"metric": "PAT YoY", "change": "+13.984816%",
                                         "interpretation": "Profit grew.", "evidence_ids": [yoy]})
    provider = FakeProvider([json.dumps(invalid), valid(revenue, yoy)])
    record = analyze_period(db, company, period, provider)
    assert record.status == "SUCCESS" and record.attempt_count == 2
    assert f"summary: UNSUPPORTED_NUMBER 13.98" in provider.prompts[1]
    assert f"material_changes[1]: UNSUPPORTED_NUMBER +13.984816" in provider.prompts[1]
    assert f"{pat_yoy} (PAT_YOY)" in provider.prompts[1]


def test_invalid_after_two_attempts_uses_verified_fallback(financial_env):
    _, db, company, period, context, _, _ = setup(financial_env)
    record = analyze_period(db, company, period, FakeProvider(["{", "{"]))
    assert record.status == "VERIFIED_FALLBACK" and record.failure_reason == "INVALID_JSON_OR_SCHEMA"
    assert record.attempt_count == 2 and db.get(AIAnalysis, record.id)
    assert record.structured_output["summary_evidence_ids"]
    assert record.structured_output["what_might_happen"]
    assert record.structured_output["result_view"]["sentiment"] == "NEUTRAL"
    validate_output(json.dumps(record.structured_output), context.registry, period.period_end.year,
                    require_extended=True)


def test_reported_esds_citation_errors_return_verified_report(financial_env, monkeypatch):
    client, db, company, period, context, revenue, yoy = setup(financial_env)
    first = json.loads(valid(revenue, yoy))
    first["positives"][0]["evidence_ids"] = [revenue]
    second = json.loads(valid(revenue, yoy))
    second["summary_evidence_ids"] = ["M1"]
    from app.ai.analyst import service
    provider = FakeProvider([json.dumps(first), json.dumps(second)])
    monkeypatch.setattr(service, "configured_provider", lambda: provider)
    response = client.post(f"/api/companies/ESDS/financials/{period.id}/analyze")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "VERIFIED_FALLBACK"
    assert data["failure_reason"] == "UNKNOWN_EVIDENCE_ID"
    assert data["structured_output"]["positives"]
    assert data["structured_output"]["concerns"]
    validate_output(json.dumps(data["structured_output"]), context.registry,
                    period.period_end.year, require_extended=True)


def test_missing_extended_analysis_retries_with_injected_provider(financial_env):
    _, db, company, period, _, revenue, yoy = setup(financial_env)
    provider = FakeProvider([valid(revenue, yoy, extended=False), valid(revenue, yoy)])
    record = analyze_period(db, company, period, provider)
    assert record.status == "SUCCESS" and record.attempt_count == 2
    assert record.structured_output["what_might_happen"] is not None


@pytest.mark.parametrize("reason", ["OLLAMA_UNAVAILABLE", "OLLAMA_TIMEOUT", "MODEL_UNAVAILABLE"])
def test_provider_failure_does_not_modify_financial_data(financial_env, reason):
    _, db, company, period, _, _, _ = setup(financial_env)
    before = db.query(FinancialFact).count()
    record = analyze_period(db, company, period, FakeProvider(error=reason))
    assert record.status == "FAILED" and record.failure_reason == reason
    assert record.attempt_count == 1 and db.query(FinancialFact).count() == before


def test_unknown_evidence_unsupported_number_and_recommendation(financial_env):
    _, db, company, period, context, revenue, yoy = setup(financial_env)
    base = json.loads(valid(revenue, yoy))
    for mutate, reason in [
        (lambda x: x["positives"][0].update(evidence_ids=["M999"]), "UNKNOWN_EVIDENCE_ID"),
        (lambda x: x["material_changes"][0].update(change="+91.44%"), "UNSUPPORTED_NUMBER"),
        (lambda x: x.update(summary="Buy this stock"), "RECOMMENDATION_FORBIDDEN"),
        (lambda x: x["positives"][0].update(evidence_ids=[]), "INVALID_JSON_OR_SCHEMA"),
    ]:
        data = json.loads(json.dumps(base))
        mutate(data)
        with pytest.raises(InvalidAnalysis) as exc:
            validate_output(json.dumps(data), context.registry, period.period_end.year)
        assert exc.value.reason == reason


def test_current_margin_cannot_support_change_claim(financial_env):
    _, db, company, period, context, revenue, yoy = setup(financial_env)
    data = json.loads(valid(revenue, yoy))
    margin = next(key for key, item in context.registry.items()
                  if item["kind"] == "METRIC" and item["metric_code"] == "PAT_MARGIN")
    data["concerns"] = [{"title": "PAT margin contraction", "explanation": "PAT margin dropped compared to the previous quarter.",
                         "evidence_ids": [margin]}]
    with pytest.raises(InvalidAnalysis, match="UNSUPPORTED_MARGIN_COMPARISON"):
        validate_output(json.dumps(data), context.registry, period.period_end.year)


def test_comparison_direction_must_match_python_metric(financial_env):
    _, db, company, period, context, revenue, yoy = setup(financial_env)
    data = json.loads(valid(revenue, yoy))
    data["positives"][0]["title"] = "Revenue decline"
    data["positives"][0]["explanation"] = "Revenue decreased 7.28% YoY."
    with pytest.raises(InvalidAnalysis, match="COMPARISON_DIRECTION_MISMATCH"):
        validate_output(json.dumps(data), context.registry, period.period_end.year)


@pytest.mark.parametrize("joiner", [" while ", " and "])
def test_different_comparisons_in_one_summary_use_their_own_evidence(financial_env, joiner):
    _, db, company, period, context, revenue, yoy = setup(financial_env)
    data = json.loads(valid(revenue, yoy))
    pat_qoq = next(key for key, item in context.registry.items()
                   if item["kind"] == "METRIC" and item["metric_code"] == "PAT_QOQ")
    data["summary"] = f"Revenue rose 7.28% YoY{joiner}PAT fell QoQ."
    data["summary_evidence_ids"] = [yoy, pat_qoq]
    validate_output(json.dumps(data), context.registry, period.period_end.year)


def test_supported_date_and_field_citation(financial_env):
    _, db, company, period, context, revenue, yoy = setup(financial_env)
    data = json.loads(valid(revenue, yoy))
    data["summary"] = "Revenue rose YoY for the quarter ended June 30, 2026."
    validate_output(json.dumps(data), context.registry, period.period_end.year)
    data["positives"][0]["explanation"] = "Revenue rose 7.28% YoY; PAT was ₹29.28 Cr."
    with pytest.raises(InvalidAnalysis) as exc:
        validate_output(json.dumps(data), context.registry, period.period_end.year)
    assert exc.value.reason == "UNSUPPORTED_NUMBER"
    assert exc.value.field == "positives" and exc.value.token == "29.28"


def test_review_facts_and_periods_cannot_enter_context(financial_env):
    _, db, company, period, _, _, _ = setup(financial_env)
    fact = period.facts[0]
    fact.status = "NEEDS_REVIEW"
    db.commit()
    context = build_context(db, company.name, company.symbol, period)
    assert f"F{fact.id}" not in context.registry
    assert all(f"F{fact.id}" not in item.get("input_evidence_ids", []) for item in context.registry.values())
    period.status = FinancialStatus.NEEDS_REVIEW
    db.commit()
    with pytest.raises(ValueError, match="PERIOD_NOT_VALIDATED"):
        analyze_period(db, company, period, FakeProvider([]))


def test_api_scope_validation_and_failure_response(financial_env, monkeypatch):
    client, db, company, period, _, _, _ = setup(financial_env)
    from app.ai.analyst import service
    monkeypatch.setattr(service, "configured_provider", lambda: FakeProvider(error="OLLAMA_UNAVAILABLE"))
    response = client.post(f"/api/companies/ESDS/financials/{period.id}/analyze")
    assert response.status_code == 200
    assert response.json()["status"] == "FAILED"
    assert response.json()["failure_reason"] == "OLLAMA_UNAVAILABLE"
    assert client.post(f"/api/companies/OTHER/financials/{period.id}/analyze").status_code == 404
    assert client.get("/api/analysis/9999").status_code == 404


def test_api_success_and_cached_response(financial_env, monkeypatch):
    client, db, company, period, _, revenue, yoy = setup(financial_env)
    from app.ai.analyst import service
    provider = FakeProvider([valid(revenue, yoy, extended=True)])
    monkeypatch.setattr(service, "configured_provider", lambda: provider)
    path = f"/api/companies/ESDS/financials/{period.id}/analyze"
    first = client.post(path)
    assert first.status_code == 200 and first.json()["status"] == "SUCCESS"
    assert first.json()["structured_output"]["summary_evidence_ids"] == [yoy]
    assert client.post(path).json()["id"] == first.json()["id"]
    assert provider.calls == 1


def test_api_returns_and_caches_verified_fallback(financial_env, monkeypatch):
    client, db, company, period, _, _, _ = setup(financial_env)
    from app.ai.analyst import service
    provider = FakeProvider(["{", "{"])
    monkeypatch.setattr(service, "configured_provider", lambda: provider)
    path = f"/api/companies/ESDS/financials/{period.id}/analyze"
    first = client.post(path)
    assert first.status_code == 200
    assert first.json()["status"] == "VERIFIED_FALLBACK"
    assert first.json()["structured_output"]["material_changes"]
    assert client.post(path).json()["id"] == first.json()["id"]
    assert provider.calls == 2


def test_sourced_forward_growth_calculates_peg_and_bounds_valuation(financial_env):
    client, db, company, period, _, revenue, yoy = setup(financial_env)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    base = {"close_price": "100.00", "pe_ratio": "30.00", "market_cap_crore": None,
            "as_of_date": today, "source_url": "https://example.com/price"}
    path = "/api/companies/ESDS/market-snapshot"
    assert client.put(path, json={**base, "forward_eps_growth_pct": "15"}).status_code == 422
    response = client.put(path, json={**base, "forward_eps_growth_pct": "15",
                                      "growth_source_url": "https://example.com/growth"})
    assert response.status_code == 200
    assert Decimal(response.json()["peg_ratio"]) == Decimal("2")
    assert db.scalar(select(MarketSnapshot)).growth_source_url == "https://example.com/growth"
    context = build_context(db, company.name, company.symbol, period)
    quote_id = next(key for key, item in context.registry.items() if item["kind"] == "MARKET_SNAPSHOT")
    assert context.registry[quote_id]["peg_ratio"] == "2.0000"
    output = json.loads(valid(revenue, yoy))
    output["valuation_assessment"] = {"status": "ASSESSED", "verdict": "INCONCLUSIVE",
        "text": "At ₹100.00, P/E is 30.00 and PEG is 2.00 using 15% forward EPS growth.",
        "evidence_ids": [quote_id, revenue, yoy]}
    validate_output(json.dumps(output), context.registry, period.period_end.year, require_extended=True)
    output["valuation_assessment"]["evidence_ids"] = [output["summary_evidence_ids"][0]]
    with pytest.raises(InvalidAnalysis, match="UNSUPPORTED_NUMBER|VALUATION_EVIDENCE_MISSING"):
        validate_output(json.dumps(output), context.registry, period.period_end.year, require_extended=True)
    fallback = verified_fallback(context.registry, period)
    assert fallback.result_view.sentiment == "NEUTRAL"
    assert fallback.valuation_assessment.status == "ASSESSED"
    assert "PEG" in fallback.valuation_assessment.text
    no_growth = client.put(path, json={**base, "forward_eps_growth_pct": "-5",
                                       "growth_source_url": "https://example.com/growth"})
    assert no_growth.status_code == 200 and no_growth.json()["peg_ratio"] is None
    context = build_context(db, company.name, company.symbol, period)
    assert verified_fallback(context.registry, period).valuation_assessment.status == "INSUFFICIENT_DATA"


def test_result_view_cannot_reverse_the_only_cited_comparison(financial_env):
    _, db, company, period, context, revenue, yoy = setup(financial_env)
    output = json.loads(valid(revenue, yoy))
    output["result_view"]["sentiment"] = "NEGATIVE"
    with pytest.raises(InvalidAnalysis, match="RESULT_VIEW_DIRECTION_MISMATCH"):
        validate_output(json.dumps(output), context.registry, period.period_end.year,
                        require_extended=True)
