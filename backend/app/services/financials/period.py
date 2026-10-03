from dataclasses import dataclass
from datetime import date, timedelta

from app.models.financial import PeriodType


@dataclass(frozen=True)
class PeriodIdentity:
    start: date
    end: date
    fiscal_year: str
    quarter: int | None
    period_type: PeriodType


def identify_period(end: date, period_type: PeriodType) -> PeriodIdentity:
    fiscal_end_year = end.year + (end.month >= 4)
    fiscal_year = f"FY{fiscal_end_year}"
    quarter = None
    if period_type == PeriodType.QUARTERLY:
        quarter_ends = {(6, 30): 1, (9, 30): 2, (12, 31): 3, (3, 31): 4}
        quarter = quarter_ends.get((end.month, end.day))
        if quarter is None:
            raise ValueError("Quarter end is not an Indian fiscal quarter boundary")
        start_month = {1: 4, 2: 7, 3: 10, 4: 1}[quarter]
        start_year = end.year if quarter != 4 else end.year
        start = date(start_year, start_month, 1)
    elif period_type == PeriodType.ANNUAL:
        if (end.month, end.day) != (3, 31):
            raise ValueError("Annual period must end on 31 March")
        start = date(end.year - 1, 4, 1)
    elif period_type in (PeriodType.HALF_YEARLY, PeriodType.NINE_MONTH):
        valid = (
            period_type == PeriodType.HALF_YEARLY and (end.month, end.day) == (9, 30)
        ) or (period_type == PeriodType.NINE_MONTH and (end.month, end.day) == (12, 31))
        if not valid:
            raise ValueError("Interim period has an invalid Indian fiscal boundary")
        start = date(end.year, 4, 1)
    else:
        raise ValueError("Unknown period type")
    return PeriodIdentity(start, end, fiscal_year, quarter, period_type)


def previous_quarter_end(identity: PeriodIdentity) -> date | None:
    return (
        identity.start - timedelta(days=1)
        if identity.period_type == PeriodType.QUARTERLY
        else None
    )


def previous_year_end(identity: PeriodIdentity) -> date:
    return identity.end.replace(year=identity.end.year - 1)
