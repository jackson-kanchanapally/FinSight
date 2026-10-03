"""Golden checks for deterministic Phase 6.1 investor reasoning."""

import json
from datetime import date
from decimal import Decimal

import pytest

from app.ai.analyst.quality import build_quality_report
from app.ai.analyst.schemas import GeneratedAnalystOutput
from app.ai.analyst.reasoning import (
    compare_values,
    margin_change_units,
    required_growth_gap,
    seasonality_pattern,
)
from app.ai.analyst.validator import InvalidAnalysis, validate_output
from app.market.expectations import latest_quarter_eps_growth
from app.services.financials.calculator import margin_change_bps
from tests.test_ai_analyst import setup, valid
from tests.test_financials import financial_env


def test_reference_pe_is_a_crosscheck_and_below_source():
    result = compare_values(Decimal("138.22"), Decimal("163"))
    assert result["relationship"] == "BELOW"
    assert Decimal(result["absolute_difference"]) == Decimal("-24.78")
    assert Decimal(result["percentage_difference"]).quantize(Decimal(".01")) == Decimal(
        "-15.20"
    )


def test_margin_units_are_percentage_points_and_basis_points():
    result = margin_change_units(Decimal("19.35"), Decimal("18.20"))
    assert Decimal(result["change_percentage_points"]) == Decimal("1.15")
    assert Decimal(result["change_bps"]) == Decimal("115")
    assert margin_change_bps(Decimal("19.35"), Decimal("18.20")) == Decimal("115.00")


def test_equivalent_quarter_seasonality_needs_three_patterns():
    history = [{"change_pct": value} for value in ("-19", "-22", "-17")]
    result = seasonality_pattern(Decimal("-20"), history)
    assert result["status"] == "CONSISTENT_WITH_HISTORICAL_PATTERN"
    assert result["median_change_pct"] == "-19.000000"
    assert result["range_pct"] == ["-22.000000", "-17.000000"]
    assert (
        seasonality_pattern(Decimal("-20"), history[:2])["status"]
        == "INSUFFICIENT_HISTORY"
    )
    assert (
        seasonality_pattern(Decimal("-35"), history)["status"]
        == "LARGER_THAN_HISTORICAL_PATTERN"
    )


def test_explicit_reverse_expectations_compare_eps_bases_only():
    gap = required_growth_gap(
        Decimal("28"),
        latest_eps_growth=Decimal("14"),
        historical_eps_cagr=Decimal("21"),
    )
    assert gap["comparisons"]["latest_eps_growth"]["relationship"] == "ABOVE"
    assert gap["comparisons"]["historical_eps_cagr"]["relationship"] == "ABOVE"
    assert (
        gap["comparisons"]["forward_eps_growth"]["relationship"] == "INSUFFICIENT_DATA"
    )


def _registry(revenue="20", pat="30", pe="35", cash=None, forward=None):
    registry = {
        "M_REVENUE": {
            "kind": "METRIC",
            "metric_code": "REVENUE_YOY",
            "value": revenue,
            "unit": "PERCENT",
            "input_evidence_ids": [],
        },
        "M_PAT": {
            "kind": "METRIC",
            "metric_code": "PAT_YOY",
            "value": pat,
            "unit": "PERCENT",
            "input_evidence_ids": [],
        },
        "V_PE": {"kind": "VALUATION", "metric_code": "PE_TTM", "value": pe},
        "Q1": {
            "kind": "MARKET_SNAPSHOT",
            "price": "100",
            "as_of_date": "2026-09-28",
            "forward_eps_growth_pct": forward,
        },
    }
    if cash is not None:
        registry["V_CASH"] = {
            "kind": "VALUATION",
            "metric_code": "TTM_OPERATING_CASH_FLOW",
            "value": cash,
        }
        registry["V_PAT"] = {
            "kind": "VALUATION",
            "metric_code": "TTM_PAT",
            "value": "100",
        }
    return registry


def test_golden_a_supportive_growth_margin_cash():
    registry = _registry(cash="135", forward="20")
    reasoning = {
        "relationships": {
            "pat_vs_revenue_growth": {
                "evidence_id": "R_GROWTH",
                "relationship": "ABOVE",
            },
            "ebitda_margin_change": {
                "evidence_id": "R_MARGIN",
                "change_bps": "200",
                "change_percentage_points": "2",
                "input_evidence_ids": [],
            },
            "cash_vs_pat": {"evidence_id": "R_CASH", "relationship": "ABOVE"},
        }
    }
    report = build_quality_report(registry, reasoning, None)
    assert report.results_vs_valuation.assessment == "MODERATE_SUPPORT"
    assert "200.00 basis points" in report.earnings_quality.text
    assert "operating cash flow" in report.earnings_quality.text


def test_golden_b_high_pe_low_growth_missing_forward():
    report = build_quality_report(_registry(revenue="5", pat="8", pe="100"), {}, None)
    assert report.results_vs_valuation.assessment == "LIMITED_SUPPORT"
    assert "No sourced forward EPS growth" in report.valuation_expectations.text
    assert any(gap.type == "FORWARD_GROWTH" for gap in report.research_gaps)


def test_golden_c_seasonal_decline_is_not_automatic_deterioration():
    registry = _registry()
    registry["M_QOQ"] = {
        "kind": "METRIC",
        "metric_code": "REVENUE_QOQ",
        "value": "-20",
        "unit": "PERCENT",
        "input_evidence_ids": [],
    }
    pattern = seasonality_pattern(
        Decimal("-20"), [{"change_pct": value} for value in ("-19", "-22", "-17")]
    )
    report = build_quality_report(
        registry,
        {"seasonality": {"revenue": {"evidence_id": "H_REVENUE", **pattern}}},
        None,
    )
    assert report.seasonality_context.status == "CONSISTENT_WITH_HISTORICAL_PATTERN"
    assert "historical median" in report.seasonality_context.text.lower()


def test_golden_d_cash_below_pat_requires_investigation():
    registry = _registry(cash="75")
    report = build_quality_report(
        registry,
        {
            "relationships": {
                "cash_vs_pat": {"evidence_id": "R_CASH", "relationship": "BELOW"}
            }
        },
        None,
    )
    assert "below" in report.earnings_quality.text
    assert any(gap.type == "CASH_CONVERSION" for gap in report.research_gaps)
    assert report.weakening_evidence


def test_golden_e_reference_difference_never_means_intrinsic_value():
    registry = _registry(pe="100")
    registry["Q1"]["pe_ratio"] = "120"
    comparison = compare_values(Decimal("100"), Decimal("120"))
    report = build_quality_report(
        registry,
        {"relationships": {"pe_reference": {"evidence_id": "R_PE", **comparison}}},
        None,
    )
    assert "calculation cross-check" in report.valuation_expectations.text
    assert "valuation benchmark" in report.valuation_expectations.text
    assert "cheap" not in report.valuation_expectations.text


def test_validator_rejects_false_pe_relationship_and_unsupported_cause(financial_env):
    _, _, _, period, context, revenue, yoy = setup(financial_env)
    registry = dict(context.registry)
    registry["R_PE"] = {
        "kind": "RELATIONSHIP",
        "metric_code": "PE_REFERENCE_CROSSCHECK",
        "relationship": "BELOW",
        "primary_value": "100",
        "reference_value": "120",
        "absolute_difference": "-20",
        "percentage_difference": "-16.666667",
    }
    output = json.loads(valid(revenue, yoy))
    output["summary"] = "Almonds P/E 100 is higher than reference P/E 120."
    output["summary_evidence_ids"] = ["R_PE"]
    with pytest.raises(InvalidAnalysis, match="NUMERICAL_RELATIONSHIP_MISMATCH"):
        validate_output(json.dumps(output), registry, period.period_end.year)
    output = json.loads(valid(revenue, yoy))
    output["questions"] = ["Does revenue growth indicate continued demand?"]
    with pytest.raises(InvalidAnalysis, match="UNSUPPORTED_CAUSAL_EXPLANATION"):
        validate_output(json.dumps(output), context.registry, period.period_end.year)
    output["questions"] = ["Does this prove the stock is overvalued?"]
    with pytest.raises(InvalidAnalysis, match="UNSUPPORTED_VALUATION_JUDGMENT"):
        validate_output(json.dumps(output), context.registry, period.period_end.year)


def test_margin_unit_hallucination_is_rejected(financial_env):
    _, _, _, period, context, revenue, yoy = setup(financial_env)
    registry = dict(context.registry)
    registry["R_MARGIN"] = {
        "kind": "RELATIONSHIP",
        "metric_code": "EBITDA_MARGIN_CHANGE",
        "previous_margin_pct": "18.20",
        "current_margin_pct": "19.35",
        "change_percentage_points": "1.15",
        "change_bps": "115",
        "input_evidence_ids": [],
    }
    output = json.loads(valid(revenue, yoy))
    output["summary"] = "EBITDA margin improved 115 percentage points."
    output["summary_evidence_ids"] = ["R_MARGIN"]
    with pytest.raises(InvalidAnalysis, match="MARGIN_UNIT_MISMATCH"):
        validate_output(json.dumps(output), registry, period.period_end.year)


def test_quality_report_is_optional_for_existing_model_contract():
    schema = GeneratedAnalystOutput.model_json_schema()
    assert "quality_report" not in schema.get("required", [])
    report = build_quality_report(_registry(), {}, None)
    assert any(gap.type == "MANAGEMENT_GUIDANCE" for gap in report.research_gaps)
    assert report.confidence == "LOW"


def test_reverse_expectations_use_verified_same_quarter_eps(financial_env):
    _, db, company, period, _, _, _ = setup(financial_env)
    observed = latest_quarter_eps_growth(
        db, company.id, period.scope, date(2026, 9, 28)
    )
    assert observed is not None
    assert observed["latest_period_end"] == date(2026, 6, 30)
    assert observed["previous_period_end"] == date(2025, 6, 30)
    assert len(observed["input_evidence_ids"]) == 2
    gap = required_growth_gap(
        Decimal("28"), latest_eps_growth=observed["latest_eps_growth_pct"]
    )
    assert gap["comparisons"]["latest_eps_growth"]["relationship"] in {
        "ABOVE",
        "BELOW",
        "EQUAL_WITHIN_TOLERANCE",
    }
    assert (
        latest_quarter_eps_growth(db, company.id, period.scope, date(2026, 6, 30))
        is None
    )
