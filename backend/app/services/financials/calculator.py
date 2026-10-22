from decimal import Decimal
import re

from app.models.financial import OriginalUnit

HUNDRED = Decimal("100")


def printed_rounding(value: Decimal, unit: OriginalUnit) -> Decimal:
    """Half of the least displayed unit, normalized to the stored unit."""
    half = Decimal(1).scaleb(value.as_tuple().exponent) / 2
    return half if unit == OriginalUnit.INR_PER_SHARE else normalize_currency(half, unit)


def reconciliation_tolerance(facts) -> Decimal:
    facts = list(facts)
    floor = Decimal(".000001") if facts and all(f.rounding_tolerance is not None for f in facts) else Decimal(".03")
    return max(floor, sum((f.rounding_tolerance or Decimal(0) for f in facts), Decimal(0)))


def exceptional_effect(value, label):
    """Do not guess the sign of an ambiguously labelled exceptional item."""
    gain = bool(re.search(r"\b(?:gain|income)\b", label, re.I))
    loss = bool(re.search(r"\b(?:loss|expense)\b", label, re.I))
    if gain == loss:
        return None
    return value if gain else -abs(value)


def normalize_currency(value: Decimal, unit: OriginalUnit) -> Decimal:
    factors = {
        OriginalUnit.INR: Decimal("0.0000001"),
        OriginalUnit.INR_LAKH: Decimal("0.01"),
        OriginalUnit.INR_MILLION: Decimal("0.1"),
        OriginalUnit.INR_CRORE: Decimal("1"),
    }
    if unit not in factors:
        raise ValueError("Per-share values are not currency totals")
    return value * factors[unit]


def growth(current: Decimal | None, previous: Decimal | None) -> Decimal | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / abs(previous) * HUNDRED


def margin(numerator: Decimal | None, revenue: Decimal | None) -> Decimal | None:
    if numerator is None or revenue is None or revenue == 0:
        return None
    return numerator / revenue * HUNDRED


def margin_change_bps(
    current: Decimal | None, previous: Decimal | None
) -> Decimal | None:
    if current is None or previous is None:
        return None
    return (current - previous) * HUNDRED
