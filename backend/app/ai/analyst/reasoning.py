"""Deterministic relationships supplied to the analyst; no model arithmetic."""

from decimal import Decimal, ROUND_HALF_UP
from statistics import median


def _number(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def compare_values(primary: Decimal | None, reference: Decimal | None,
                   *, tolerance_pct: Decimal = Decimal("0.5")) -> dict:
    if primary is None or reference is None:
        return {"relationship": "INSUFFICIENT_DATA", "primary_value": None if primary is None else _number(primary),
                "reference_value": None if reference is None else _number(reference),
                "absolute_difference": None, "percentage_difference": None}
    if reference == 0:
        return {"relationship": "NOT_COMPARABLE", "primary_value": _number(primary),
                "reference_value": _number(reference), "absolute_difference": None,
                "percentage_difference": None}
    difference = primary - reference
    percentage = difference / abs(reference) * Decimal(100)
    relationship = ("EQUAL_WITHIN_TOLERANCE" if abs(percentage) <= tolerance_pct else
                    "ABOVE" if difference > 0 else "BELOW")
    return {"relationship": relationship, "primary_value": _number(primary),
            "reference_value": _number(reference), "absolute_difference": _number(difference),
            "percentage_difference": _number(percentage)}


def margin_change_units(current_pct: Decimal, previous_pct: Decimal) -> dict:
    percentage_points = current_pct - previous_pct
    return {"previous_margin_pct": _number(previous_pct), "current_margin_pct": _number(current_pct),
            "change_percentage_points": _number(percentage_points),
            "change_bps": _number(percentage_points * Decimal(100))}


def seasonality_pattern(current_change: Decimal | None, history: list[dict]) -> dict:
    """Compare an adjacent-quarter change with verified equivalent transitions."""
    values = [Decimal(item["change_pct"]) for item in history]
    base = {"current_change_pct": _number(current_change) if current_change is not None else None,
            "historical_count": len(values), "historical_transitions": history,
            "median_change_pct": None, "range_pct": None, "difference_from_median_pct": None,
            "status": "INSUFFICIENT_HISTORY"}
    if len(values) < 3 or current_change is None:
        return base
    middle = Decimal(median(values))
    base.update(median_change_pct=_number(middle),
                range_pct=[_number(min(values)), _number(max(values))],
                difference_from_median_pct=_number(current_change - middle))
    if min(values) < 0 < max(values) or max(values) - min(values) > max(
            Decimal(15), abs(middle) * Decimal("0.75")):
        base["status"] = "NO_CLEAR_PATTERN"
        return base
    tolerance = max(Decimal(3), abs(middle) * Decimal("0.20"))
    if min(values) - tolerance <= current_change <= max(values) + tolerance:
        base["status"] = "CONSISTENT_WITH_HISTORICAL_PATTERN"
    elif abs(current_change) > abs(middle):
        base["status"] = "LARGER_THAN_HISTORICAL_PATTERN"
    else:
        base["status"] = "SMALLER_THAN_HISTORICAL_PATTERN"
    return base


def prioritize_changes(metrics: dict[str, Decimal]) -> list[str]:
    """Ordering hint only; the score is never an investment conclusion."""
    weights = {"REVENUE_YOY": 1, "PAT_YOY": 1, "REVENUE_QOQ": Decimal("0.75"),
               "PAT_QOQ": Decimal("0.75"), "EBITDA_MARGIN_CHANGE_BPS": Decimal("0.1")}
    return sorted((code for code in metrics if code in weights),
                  key=lambda code: abs(metrics[code]) * weights[code], reverse=True)


def required_growth_gap(required_eps_cagr: Decimal, *, latest_eps_growth: Decimal | None = None,
                        historical_eps_cagr: Decimal | None = None,
                        forward_eps_growth: Decimal | None = None) -> dict:
    """Compare an explicit scenario requirement with EPS growth on compatible bases."""
    references = {"latest_eps_growth": latest_eps_growth,
                  "historical_eps_cagr": historical_eps_cagr,
                  "forward_eps_growth": forward_eps_growth}
    return {"required_eps_cagr": _number(required_eps_cagr),
            "comparisons": {name: compare_values(required_eps_cagr, value)
                            for name, value in references.items()}}
