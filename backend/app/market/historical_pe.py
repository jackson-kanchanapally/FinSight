"""Dated P/E observations calculated only from facts published by each quote date."""

from datetime import timedelta
from decimal import Decimal
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.market.valuation import calculate_valuation
from app.models.financial import FinancialScope
from app.models.market import MarketSnapshot


def historical_pe_summary(
    db: Session, company_id: int, current: MarketSnapshot, scope: FinancialScope
) -> dict:
    observations = list(
        db.scalars(
            select(MarketSnapshot)
            .where(
                MarketSnapshot.company_id == company_id,
                MarketSnapshot.as_of_date <= current.as_of_date,
                MarketSnapshot.as_of_date >= current.as_of_date - timedelta(days=1095),
            )
            .order_by(MarketSnapshot.as_of_date.desc(), MarketSnapshot.id.desc())
        )
    )
    by_date: dict = {}
    for market in observations:
        # Keep one observation per date; official raw EOD takes priority over manual.
        existing = by_date.get(market.as_of_date)
        if (
            existing is None
            or existing.data_origin != "EOD"
            and market.data_origin == "EOD"
        ):
            by_date[market.as_of_date] = market
    points = []
    for market in by_date.values():
        _, details = calculate_valuation(db, market, scope)
        pe = details["pe_ttm"]["value"]
        if pe is not None and Decimal(pe) > 0:
            points.append((market.as_of_date, Decimal(pe)))
    points.sort()
    _, current_details = calculate_valuation(db, current, scope)
    current_pe = (
        Decimal(current_details["pe_ttm"]["value"])
        if current_details["pe_ttm"]["value"] is not None
        else None
    )

    def coverage(days: int, min_count: int, min_span: int) -> dict:
        selected = [
            (day, value)
            for day, value in points
            if day >= current.as_of_date - timedelta(days=days)
        ]
        span = (selected[-1][0] - selected[0][0]).days if selected else 0
        return {
            "count": len(selected),
            "required_count": min_count,
            "span_days": span,
            "required_span_days": min_span,
            "first_date": selected[0][0].isoformat() if selected else None,
            "last_date": selected[-1][0].isoformat() if selected else None,
        }

    def window(days: int, min_count: int, min_span: int) -> dict | None:
        selected = [
            (day, value)
            for day, value in points
            if day >= current.as_of_date - timedelta(days=days)
        ]
        if (
            len(selected) < min_count
            or (selected[-1][0] - selected[0][0]).days < min_span
        ):
            return None
        values = [value for _, value in selected]
        return {
            "median": str(Decimal(median(values)).quantize(Decimal("0.01"))),
            "low": str(min(values).quantize(Decimal("0.01"))),
            "high": str(max(values).quantize(Decimal("0.01"))),
            "percentile": (
                str(
                    (
                        Decimal(sum(value <= current_pe for value in values))
                        / Decimal(len(values))
                        * 100
                    ).quantize(Decimal("0.1"))
                )
                if current_pe is not None
                else None
            ),
            "count": len(selected),
            "first_date": selected[0][0].isoformat(),
            "last_date": selected[-1][0].isoformat(),
        }

    available_period = None
    if len(points) >= 2:
        values = [value for _, value in points]
        available_period = {
            "median": str(Decimal(median(values)).quantize(Decimal("0.01"))),
            "low": str(min(values).quantize(Decimal("0.01"))),
            "high": str(max(values).quantize(Decimal("0.01"))),
            "count": len(points),
            "first_date": points[0][0].isoformat(),
            "last_date": points[-1][0].isoformat(),
        }
    return {
        "one_year": window(365, 20, 120),
        "three_year": window(1095, 60, 730),
        "one_year_coverage": coverage(365, 20, 120),
        "three_year_coverage": coverage(1095, 60, 730),
        "available_period": available_period,
        "first_stored_price_date": min(by_date).isoformat() if by_date else None,
        "methodology": "Each dated close divided by diluted TTM EPS from validated filings publicly available by that date; "
        "one observation per date, official EOD preferred; 1Y requires 20 dates spanning 120 days, "
        "3Y requires 60 dates spanning 730 days.",
        "available_points": len(points),
    }
