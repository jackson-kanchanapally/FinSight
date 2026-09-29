"""Date-consistent EPS evidence for reverse-valuation scenarios."""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.event import CompanyEvent
from app.models.financial import (FactStatus, FinancialPeriod, FinancialScope,
                                  FinancialStatus, MetricCode, PeriodType)

VERIFIED = {FactStatus.VERIFIED_PDF, FactStatus.VERIFIED_STRUCTURED,
            FactStatus.MANUALLY_VERIFIED}


def latest_quarter_eps_growth(db: Session, company_id: int, scope: FinancialScope,
                              as_of_date: date) -> dict | None:
    """Compare diluted EPS with the same quarter a year earlier, if both were published."""
    periods = list(db.scalars(select(FinancialPeriod).options(selectinload(FinancialPeriod.facts))
        .where(FinancialPeriod.company_id == company_id, FinancialPeriod.scope == scope,
               FinancialPeriod.period_type == PeriodType.QUARTERLY,
               FinancialPeriod.status == FinancialStatus.VALIDATED,
               FinancialPeriod.period_end <= as_of_date)
        .order_by(FinancialPeriod.period_end.desc()).limit(24)))
    eligible = []
    for period in periods:
        event = db.get(CompanyEvent, period.source_event_id)
        if event is None or event.published_at.date() > as_of_date:
            continue
        eps = next((fact for fact in period.facts if fact.metric_code == MetricCode.DILUTED_EPS
                    and fact.status in VERIFIED and fact.value > 0), None)
        if eps:
            eligible.append((period, eps))
    if not eligible:
        return None
    current, current_eps = eligible[0]
    previous = next(((period, eps) for period, eps in eligible[1:]
                     if period.quarter == current.quarter
                     and period.period_end.year == current.period_end.year - 1), None)
    if previous is None:
        return None
    prior, prior_eps = previous
    growth = (current_eps.value / prior_eps.value - Decimal(1)) * Decimal(100)
    return {"latest_eps_growth_pct": growth.quantize(Decimal("0.000001")),
            "latest_period_end": current.period_end,
            "previous_period_end": prior.period_end,
            "input_evidence_ids": [f"F{current_eps.id}", f"F{prior_eps.id}"]}
