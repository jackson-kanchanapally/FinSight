import json
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ai.analyst.context_builder import build_context
from app.ai.analyst.prompts import PROMPT_VERSION
from app.ai.analyst.service import analyze_period
from app.ai.analyst.validator import InvalidAnalysis, validate_output
from app.ai.providers.base import LLMError, LLMProvider
from app.models.analysis import AIAnalysis
from app.models.company import Company
from app.models.financial import FinancialFact, FinancialPeriod, FinancialScope, FinancialStatus
from app.services.financials.service import process_financial_event
from tests.test_financials import financial_env


class FakeProvider(LLMProvider):
    name = "fake"
    model = "fixture-v1"

    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = 0

    def generate(self, system, user, schema):
        self.calls += 1
        assert "FinSight" in system and "CONSOLIDATED" in user
        if self.error:
            raise LLMError(self.error)
        return self.responses.pop(0)


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


def valid(revenue, yoy):
    return json.dumps({"summary": "Revenue increased year over year; quarter comparison is also available.",
        "summary_evidence_ids": [yoy],
        "positives": [{"title": "Revenue improved", "explanation": "Revenue rose 7.28% YoY.",
                       "evidence_ids": [yoy]}], "concerns": [],
        "material_changes": [{"metric": "Revenue YoY", "change": "+7.28%",
                              "interpretation": "Reported revenue is ₹133.66 Cr.",
                              "evidence_ids": [yoy, revenue]}],
        "questions": ["What explains the quarter change?"],
        "data_quality_notes": ["Only validated data was considered."]})


def test_valid_persistence_prompt_model_and_duplicate(financial_env):
    client, db, company, period, context, revenue, yoy = setup(financial_env)
    provider = FakeProvider([valid(revenue, yoy)])
    first = analyze_period(db, company, period, provider)
    assert first.status == "SUCCESS" and first.summary
    assert first.prompt_version == PROMPT_VERSION
    assert first.model_provider == "fake" and first.model_name == "fixture-v1"
    assert first.context_hash == context.hash and first.attempt_count == 1
    assert first.evidence_registry[yoy]["input_evidence_ids"]
    assert analyze_period(db, company, period, provider).id == first.id
    assert provider.calls == 1
    assert client.get(f"/api/analysis/{first.id}").json()["structured_output"]["positives"]
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


def test_invalid_after_two_attempts_is_persisted(financial_env):
    _, db, company, period, _, _, _ = setup(financial_env)
    record = analyze_period(db, company, period, FakeProvider(["{", "{"]))
    assert record.status == "INVALID" and record.failure_reason == "INVALID_JSON_OR_SCHEMA"
    assert record.attempt_count == 2 and db.get(AIAnalysis, record.id)


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
    provider = FakeProvider([valid(revenue, yoy)])
    monkeypatch.setattr(service, "configured_provider", lambda: provider)
    path = f"/api/companies/ESDS/financials/{period.id}/analyze"
    first = client.post(path)
    assert first.status_code == 200 and first.json()["status"] == "SUCCESS"
    assert first.json()["structured_output"]["summary_evidence_ids"] == [yoy]
    assert client.post(path).json()["id"] == first.json()["id"]
    assert provider.calls == 1
