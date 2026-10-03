"""Phase 6.3 judgments, explicit scenarios, evidence discipline and diagnostics."""

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app
from app.market.expectations import (
    implied_current_pe,
    expectations_gap,
    latest_quarter_eps_growth,
    scenario_assessment,
)
from app.market.valuation import reverse_growth
from app.models.financial import FinancialScope
from app.models.market import ValuationAssumptions
from app.services.investor_verdict import results_verdict, classify_valuation
from app.ai.analyst.service import analyze_period, model_schema
from app.ai.analyst.context_builder import build_context
from app.ai.analyst.validator import _check_numbers, InvalidAnalysis, validate_output
from app.ai.analyst.validator import _check_valuation_judgment
from tests.test_market_valuation import (
    market_db,
    company,
    four_quarters,
    quote,
    add_quarter,
)
from tests.test_phase62 import _registry
from tests.test_financials import financial_env
from tests.test_ai_analyst import setup, FakeProvider, valid


def test_partial_evidence_reduces_confidence_without_erasing_judgment():
    partial = results_verdict(_registry(revenue=15))
    assert partial["verdict"] == "POSITIVE" and partial["confidence"] == "LOW"
    complete = results_verdict(_registry(revenue=15, pat=18, margin=40))
    assert complete["verdict"] == "POSITIVE" and complete["confidence"] == "MEDIUM"
    assert results_verdict({})["verdict"] == "INSUFFICIENT_EVIDENCE"
    sequential = results_verdict(_registry(revenue_qoq=17, pat_qoq=-47))
    assert sequential["verdict"] == "NEGATIVE" and sequential["confidence"] == "LOW"


def test_reverse_and_implied_pe_are_algebraic_inverses():
    result = reverse_growth(Decimal(10), Decimal(500), Decimal(25), 3, Decimal(12))
    assert Decimal(result["target_future_price"]) == Decimal("702.464000")
    assert Decimal(result["required_future_eps"]) == Decimal("28.098560")
    growth = Decimal(result["required_eps_cagr_pct"])
    assert abs(
        implied_current_pe(growth, Decimal(25), 3, Decimal(12)) - Decimal(50)
    ) < Decimal(".00001")
    assert implied_current_pe(Decimal(12), Decimal(40), 5, Decimal(12)) == 40
    with pytest.raises(ValueError):
        implied_current_pe(Decimal(-100), Decimal(20), 3, Decimal(12))


def test_pe_level_does_not_determine_verdict_and_results_are_independent():
    assert (
        classify_valuation(Decimal(150), Decimal(140), Decimal(170)) == "FAIRLY_VALUED"
    )
    assert (
        classify_valuation(Decimal(12), Decimal(5), Decimal(7))
        == "EXTREMELY_OVERVALUED"
    )
    positive = results_verdict(_registry(30, 35, 150, cash=120))
    assert positive["verdict"] == "VERY_POSITIVE"
    assert (
        classify_valuation(Decimal(100), Decimal(20), Decimal(30))
        == "EXTREMELY_OVERVALUED"
    )


def test_expectations_gap_uses_evidence_dispersion_not_a_universal_threshold():
    data = [
        {"metric": "EPS_YOY", "value": "10", "evidence_ids": ["F1"]},
        {"metric": "EPS_CAGR", "value": "20", "evidence_ids": ["F2"]},
    ]
    assert (
        expectations_gap(Decimal(15), data, Decimal(99))["classification"]
        == "BROADLY_CONSISTENT"
    )
    assert (
        expectations_gap(Decimal(25), data, Decimal(99))["classification"]
        == "ABOVE_EVIDENCE"
    )
    assert (
        expectations_gap(Decimal(31), data, Decimal(99))["classification"]
        == "WELL_ABOVE_EVIDENCE"
    )
    assert (
        expectations_gap(Decimal(-1), data, Decimal(99))["classification"]
        == "WELL_BELOW_EVIDENCE"
    )
    assert (
        expectations_gap(Decimal(40), [], Decimal(20))["classification"]
        == "INSUFFICIENT_EVIDENCE"
    )
    # Revenue guidance remains a management claim, not comparable EPS proof.
    guidance = [
        {
            "metric": "REVENUE_GROWTH",
            "value": "80",
            "evidence_ids": ["S1"],
            "source_type": "MANAGEMENT_GUIDANCE",
        }
    ]
    assert (
        expectations_gap(Decimal(40), guidance, Decimal(20))["classification"]
        == "INSUFFICIENT_EVIDENCE"
    )


def test_eps_comparison_has_no_same_day_date_only_lookahead(market_db):
    issuer = company(market_db)
    add_quarter(market_db, issuer, date(2025, 6, 30), date(2025, 7, 20), eps="1")
    add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20), eps="1.2")
    assert (
        latest_quarter_eps_growth(
            market_db, issuer.id, FinancialScope.CONSOLIDATED, date(2026, 7, 20)
        )
        is None
    )
    assert (
        latest_quarter_eps_growth(
            market_db, issuer.id, FinancialScope.CONSOLIDATED, date(2026, 7, 21)
        )["latest_eps_growth_pct"]
        == 20
    )


def test_three_scenarios_are_explicit_saved_and_reloaded(market_db):
    issuer = company(market_db)
    four_quarters(market_db, issuer)
    quote(market_db, issuer, date(2026, 9, 29), close_price="100")
    initial = scenario_assessment(market_db, issuer.id, FinancialScope.CONSOLIDATED)
    assert initial["status"] == "CALCULATED"
    assert initial["quality_adjustment"].startswith("NONE")
    assert all(
        s["source_type"] == "SCENARIO_ASSUMPTION" and s["source"]
        for s in initial["scenarios"]
    )
    scenarios = [
        {
            "name": name,
            "eps_growth_pct": str(growth),
            "terminal_pe": str(terminal),
            "years": 3,
            "required_return_pct": "12",
            "source": "Sensitivity selected for research",
        }
        for name, growth, terminal in [
            ("CONSERVATIVE", 5, 20),
            ("BASE", 15, 30),
            ("OPTIMISTIC", 25, 40),
        ]
    ]
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/companies/TEST/valuation/tested-pe", json={"scenarios": scenarios}
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["scenarios"][1]["source_type"] == "USER_ASSUMPTION"
            assert data["reverse_expectations"] and data["supported_pe_range"]
            assert (
                Decimal(data["supported_pe_range"]["lower"])
                < Decimal(data["supported_pe_range"]["central"])
                < Decimal(data["supported_pe_range"]["upper"])
            )
            assert market_db.query(ValuationAssumptions).count() == 1
            again = client.get("/api/companies/TEST/valuation/tested-pe").json()
            assert again["supported_pe_range"] == data["supported_pe_range"]
            scenarios[1]["name"] = "CONSERVATIVE"
            assert (
                client.post(
                    "/api/companies/TEST/valuation/tested-pe",
                    json={"scenarios": scenarios},
                ).status_code
                == 422
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_exact_source_precision_is_valid_but_neighbor_value_is_rejected():
    registry = {
        "M1": {
            "kind": "METRIC",
            "metric_code": "PAT_QOQ",
            "value": "-47.126437",
            "unit": "PERCENT",
            "input_evidence_ids": [],
        }
    }
    _check_numbers("PAT QoQ -47.126437%", registry, 2026, ["M1"], "summary")
    _check_numbers("PAT declined 47.13% QoQ", registry, 2026, ["M1"], "summary")
    with pytest.raises(InvalidAnalysis) as error:
        _check_numbers("PAT QoQ -47.126438%", registry, 2026, ["M1"], "summary")
    diagnostic = error.value.diagnostic()
    assert (
        diagnostic["code"] == "UNSUPPORTED_NUMBER" and diagnostic["field"] == "summary"
    )
    assert diagnostic["model_value"] == "-47.126438" and diagnostic["expected"]


def test_qwen_can_explain_but_cannot_change_conditional_python_valuation():
    registry = {
        "A_BASE": {"kind": "SCENARIO", "valuation_verdict": "EXTREMELY_OVERVALUED"}
    }
    _check_valuation_judgment(
        "Extremely overvalued under these explicit scenarios.",
        ["A_BASE"],
        registry,
        "summary",
    )
    for text, ids in [
        ("Extremely undervalued under these scenarios.", ["A_BASE"]),
        ("Extremely overvalued.", ["A_BASE"]),
        ("Not extremely overvalued under these scenarios.", ["A_BASE"]),
        ("Extremely overvalued under these scenarios.", []),
    ]:
        with pytest.raises(InvalidAnalysis, match="UNSUPPORTED_VALUATION_JUDGMENT"):
            _check_valuation_judgment(text, ids, registry, "summary")


def test_qwen_cannot_inject_deterministic_verdict_and_failure_has_diagnostics(
    financial_env,
):
    _, db, issuer, period, _, revenue, yoy = setup(financial_env)
    model = json.loads(valid(revenue, yoy))
    model["investor_report"] = {"valuation": {"verdict": "EXTREMELY_UNDERVALUED"}}
    record = analyze_period(
        db, issuer, period, FakeProvider([json.dumps(model), json.dumps(model)])
    )
    assert record.status == "VERIFIED_FALLBACK"
    assert record.validation_diagnostics[0]["field"] == "investor_report"
    report = record.structured_output["investor_report"]
    assert report["valuation"]["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert report["investor_stance"] and report["investor_conclusion"]
    assert report["results"]["confidence"] == "MEDIUM"


def test_deterministic_report_available_without_qwen(financial_env):
    client, db, issuer, period, _, _, _ = setup(financial_env)
    response = client.post(
        f"/api/companies/{issuer.symbol}/financials/{period.id}/analyze?deterministic_only=true"
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["model_provider"] == "deterministic" and not data["failure_reason"]
    report = data["structured_output"]["investor_report"]
    assert report["earnings_quality"]["pat_bridge"]
    assert report["tested_pe"]["scenarios"] and report["research_gaps"]


def test_schema_failure_reports_missing_field():
    with pytest.raises(InvalidAnalysis) as error:
        validate_output("{}", {}, 2026)
    diagnostic = error.value.diagnostic()
    assert (
        diagnostic["code"] == "MISSING_REQUIRED_FIELD"
        and diagnostic["field"] == "summary"
    )


def test_scenario_inputs_resolve_to_source_pages_and_changes_invalidate_context(
    market_db,
):
    issuer = company(market_db)
    periods = four_quarters(market_db, issuer)
    for period in periods:
        for fact in period.facts:
            fact.source_page = 1
    market_db.commit()
    quote(market_db, issuer, date(2026, 9, 29), close_price="100")
    original = build_context(market_db, issuer.name, issuer.symbol, periods[-1])
    schema = model_schema(original.registry, periods[-1].id)
    assert schema["$defs"]["GeneratedValuationView"]["properties"]["status"][
        "enum"
    ] == ["ASSESSED"]
    for key in original.registry["A_BASE"]["input_evidence_ids"]:
        assert key in original.registry
        item = original.registry[key]
        if item["kind"] == "FACT":
            assert item["document_url"] and item["page"]
    scenarios = original.investor_report["tested_pe"]["scenarios"]
    scenarios[1] = {
        **scenarios[1],
        "terminal_pe": "70",
        "source_type": "USER_ASSUMPTION",
    }
    market_db.add(
        ValuationAssumptions(
            company_id=issuer.id, scope="CONSOLIDATED", scenarios=scenarios
        )
    )
    market_db.commit()
    revised = build_context(market_db, issuer.name, issuer.symbol, periods[-1])
    assert revised.hash != original.hash
    assert revised.investor_report["results"] == original.investor_report["results"]
    assert (
        revised.investor_report["tested_pe"]["supported_pe_range"]
        != original.investor_report["tested_pe"]["supported_pe_range"]
    )


def test_negative_eps_growth_defaults_remain_valid_and_disclosed(market_db):
    issuer = company(market_db)
    add_quarter(market_db, issuer, date(2025, 6, 30), date(2025, 7, 20), eps="10")
    four_quarters(market_db, issuer)
    quote(market_db, issuer, date(2026, 9, 29), close_price="100")
    result = scenario_assessment(market_db, issuer.id, FinancialScope.CONSOLIDATED)
    assert all(Decimal(row["eps_growth_pct"]) > -100 for row in result["scenarios"])
    assert "halfway" in result["scenarios"][0]["source"]
