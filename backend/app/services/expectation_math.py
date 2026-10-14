"""Decimal-only error calculations. Policies are frozen when a forecast is saved."""
from decimal import Decimal
from app.models.financial import OriginalUnit
from app.services.financials.calculator import normalize_currency

D = Decimal
VERSION = "EXPECTATION_V1"
MARGINS = {"EBITDA_MARGIN", "PAT_MARGIN"}
GROWTH = {"REVENUE_YOY", "REVENUE_QOQ", "PAT_YOY", "PAT_QOQ"}


def canonical_unit(metric):
    if metric in MARGINS | GROWTH:
        return "PERCENT"
    if metric == "EBITDA_MARGIN_CHANGE_BPS":
        return "BASIS_POINTS"
    if metric in {"BASIC_EPS", "DILUTED_EPS"}:
        return "INR_PER_SHARE"
    if metric in {"BUSINESS_MILESTONE", "THESIS_CLAIM"}:
        return "EVENT"
    return "INR_CRORE"


def normalize(value, unit, metric):
    canonical = canonical_unit(metric)
    value = D(str(value))
    if unit == canonical:
        return value
    if canonical == "INR_CRORE" and unit in {u.value for u in OriginalUnit}:
        return normalize_currency(value, OriginalUnit(unit))
    if canonical == "PERCENT" and unit == "BASIS_POINTS":
        return value / 100
    if canonical == "BASIS_POINTS" and unit == "PERCENTAGE_POINTS":
        return value * 100
    raise ValueError("INCOMPATIBLE_UNITS: percentage levels, relative growth and percentage-point changes are distinct")


def tolerance_policy(metric):
    from app.config import settings
    unit = canonical_unit(metric)
    return {"version": VERSION, "relative_met_pct": str(settings.expectation_met_tolerance_pct),
        "relative_partial_pct": str(settings.expectation_partial_tolerance_pct),
        "absolute_met": str(D(str(settings.expectation_percentage_tolerance_pp)) * (100 if unit == "BASIS_POINTS" else 1)) if unit in {"PERCENT", "BASIS_POINTS"} else "0.000001",
        "percentage_floor": "0.000001", "absolute_only": unit in {"PERCENT", "BASIS_POINTS"}}


def compare(definition, actual):
    metric, unit = definition["metric"], definition["unit"]
    actual = D(str(actual))
    policy = definition["tolerance"]
    floor, absolute = D(policy["percentage_floor"]), D(policy["absolute_met"])
    met = absolute if policy["absolute_only"] else max(absolute, abs(actual) * D(policy["relative_met_pct"]) / 100)
    partial = absolute * 2 if policy["absolute_only"] else max(absolute * 2, abs(actual) * D(policy["relative_partial_pct"]) / 100)
    kind = definition["expectation_type"]
    result = {"actual_value": str(actual), "normalized_unit": canonical_unit(metric),
        "signed_error": None, "absolute_error": None, "percentage_error": None, "absolute_percentage_error": None,
        "range_coverage": None, "directional_correctness": None, "tolerance_met": str(met), "tolerance_partial": str(partial),
        "error_convention": "forecast minus actual; range error uses nearest boundary; percentage denominator is abs(actual)",
        "tolerance_policy": policy}
    if kind == "DIRECTIONAL":
        baseline = D(definition["baseline"]["value"])
        delta = actual - baseline
        observed = "UP" if delta > met else "DOWN" if delta < -met else "UNCHANGED"
        correct = observed == definition["direction"]
        return {**result, "baseline_value": str(baseline), "actual_change": str(delta),
                "observed_direction": observed, "directional_correctness": correct, "outcome": "MET" if correct else "MISSED"}
    if kind == "RANGE":
        lower = normalize(definition["lower_value"], unit, metric)
        upper = normalize(definition["upper_value"], unit, metric)
        target = min(max(actual, lower), upper)
        result.update(normalized_lower=str(lower), normalized_upper=str(upper), range_coverage=lower <= actual <= upper)
    else:
        target = normalize(definition["expected_value"], unit, metric)
        result["normalized_expected"] = str(target)
    error = target - actual
    distance = abs(error)
    outcome = "MET" if distance <= met else "PARTIALLY_MET" if distance <= partial else "MISSED"
    # A change from profit to loss (or vice versa) is not hidden by a tolerance.
    if target * actual < 0 and distance > absolute:
        outcome = "MISSED"
    result.update(outcome=outcome, signed_error=str(error), absolute_error=str(distance))
    if abs(actual) > floor and not policy["absolute_only"]:
        result.update(percentage_error=str(error / abs(actual) * 100), absolute_percentage_error=str(distance / abs(actual) * 100))
    else:
        result["percentage_error_note"] = "Use absolute unit error for zero/near-zero actuals or percentage-point metrics"
    return result
