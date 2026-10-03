from decimal import Decimal

from app.models.financial import OriginalUnit

HUNDRED = Decimal("100")


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
