"""Date-consistent EPS evidence for reverse-valuation scenarios."""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.event import CompanyEvent
from app.models.market import ValuationAssumptions
from app.market.valuation import (
    _available,
    _eligible_periods,
    calculate_valuation,
    reverse_growth,
)
from app.market.service import latest_snapshot
from app.market.historical_pe import historical_pe_summary
from app.services.investor_verdict import classify_valuation
from app.models.financial import (
    FactStatus,
    FinancialPeriod,
    FinancialScope,
    FinancialStatus,
    FinancialUnit,
    MetricCode,
    PeriodType,
)

VERIFIED = {
    FactStatus.VERIFIED_PDF,
    FactStatus.VERIFIED_STRUCTURED,
    FactStatus.MANUALLY_VERIFIED,
}


def latest_quarter_eps_growth(
    db: Session, company_id: int, scope: FinancialScope, as_of_date: date
) -> dict | None:
    """Compare diluted EPS with the same quarter a year earlier, if both were published."""
    periods = list(
        db.scalars(
            select(FinancialPeriod)
            .options(selectinload(FinancialPeriod.facts))
            .where(
                FinancialPeriod.company_id == company_id,
                FinancialPeriod.scope == scope,
                FinancialPeriod.period_type == PeriodType.QUARTERLY,
                FinancialPeriod.status == FinancialStatus.VALIDATED,
                FinancialPeriod.period_end <= as_of_date,
            )
            .order_by(FinancialPeriod.period_end.desc())
            .limit(24)
        )
    )
    eligible = []
    for period in periods:
        event = db.get(CompanyEvent, period.source_event_id)
        if event is None or not _available(event, as_of_date):
            continue
        eps = next(
            (
                fact
                for fact in period.facts
                if fact.metric_code == MetricCode.DILUTED_EPS
                and fact.status in VERIFIED
                and fact.unit == FinancialUnit.INR_PER_SHARE
                and fact.value > 0
            ),
            None,
        )
        if eps:
            eligible.append((period, eps))
    if not eligible:
        return None
    current, current_eps = eligible[0]
    previous = next(
        (
            (period, eps)
            for period, eps in eligible[1:]
            if period.quarter == current.quarter
            and period.period_end.year == current.period_end.year - 1
        ),
        None,
    )
    if previous is None:
        return None
    prior, prior_eps = previous
    growth = (current_eps.value / prior_eps.value - Decimal(1)) * Decimal(100)
    return {
        "latest_eps_growth_pct": growth.quantize(Decimal("0.000001")),
        "latest_period_end": current.period_end,
        "previous_period_end": prior.period_end,
        "input_evidence_ids": [f"F{current_eps.id}", f"F{prior_eps.id}"],
    }


def implied_current_pe(
    growth_pct: Decimal, terminal_pe: Decimal, years: int, required_return_pct: Decimal
) -> Decimal:
    if not all(
        v.is_finite() for v in (growth_pct, terminal_pe, required_return_pct)
    ) or (
        growth_pct <= -100
        or required_return_pct <= -100
        or terminal_pe <= 0
        or not 1 <= years <= 20
    ):
        raise ValueError("INVALID_SCENARIO_ASSUMPTIONS")
    return (
        terminal_pe
        * ((1 + growth_pct / 100) / (1 + required_return_pct / 100)) ** years
    )


def expectations_gap(
    required: Decimal | None, evidence: list[dict], scenario_growth_span: Decimal
) -> dict:
    """Compare with the observed EPS interval, using disclosed dispersion as tolerance."""
    observed = [
        Decimal(item["value"])
        for item in evidence
        if item["metric"] in {"EPS_YOY", "EPS_CAGR"}
    ]
    if required is None or not observed:
        return {
            "classification": "INSUFFICIENT_EVIDENCE",
            "reason": "Comparable verified EPS growth is unavailable.",
        }
    low, high = min(observed), max(observed)
    band = high - low if high > low else abs(scenario_growth_span)
    status = (
        "WELL_ABOVE_EVIDENCE"
        if required > high + band
        else (
            "ABOVE_EVIDENCE"
            if required > high
            else (
                "WELL_BELOW_EVIDENCE"
                if required < low - band
                else "BELOW_EVIDENCE" if required < low else "BROADLY_CONSISTENT"
            )
        )
    )
    return {
        "classification": status,
        "observed_low_pct": str(low),
        "observed_high_pct": str(high),
        "dispersion_pct": str(band),
        "dispersion_source": "OBSERVED" if high > low else "SCENARIO_ASSUMPTION",
        "reason": "Required CAGR compared with observed EPS growth; distance beyond one disclosed dispersion band is 'well above/below'. A quarter's YoY growth is not a multi-year forecast.",
        "evidence_ids": list(
            dict.fromkeys(
                key
                for item in evidence
                if item["metric"] in {"EPS_YOY", "EPS_CAGR"}
                for key in item["evidence_ids"]
            )
        ),
    }


def growth_evidence(
    db: Session, company_id: int, scope: FinancialScope, as_of: date
) -> list[dict]:
    result = []
    latest = latest_quarter_eps_growth(db, company_id, scope, as_of)
    if latest:
        result.append(
            {
                "metric": "EPS_YOY",
                "value": str(latest["latest_eps_growth_pct"]),
                "source_type": "HISTORICAL",
                "evidence_ids": latest["input_evidence_ids"],
                "period_end": latest["latest_period_end"].isoformat(),
            }
        )
    annual = [
        p
        for p in _eligible_periods(db, company_id, as_of, scope)
        if p.period_type == PeriodType.ANNUAL
    ]
    for code, label in (
        (MetricCode.DILUTED_EPS, "EPS_CAGR"),
        (MetricCode.PAT, "PAT_CAGR"),
        (MetricCode.REVENUE_FROM_OPERATIONS, "REVENUE_CAGR"),
    ):
        expected_unit = (
            FinancialUnit.INR_PER_SHARE
            if code == MetricCode.DILUTED_EPS
            else FinancialUnit.INR_CRORE
        )
        values = [
            (p, f)
            for p in annual
            for f in p.facts
            if f.metric_code == code
            and f.status in VERIFIED
            and f.unit == expected_unit
            and f.value > 0
        ]
        if len(values) >= 2:
            current, old = values[0], values[-1]
            years = current[0].period_end.year - old[0].period_end.year
            if years > 0:
                value = (
                    (current[1].value / old[1].value) ** (Decimal(1) / years) - 1
                ) * 100
                result.append(
                    {
                        "metric": label,
                        "value": str(value.quantize(Decimal(".000001"))),
                        "years": years,
                        "source_type": "HISTORICAL",
                        "evidence_ids": [f"F{current[1].id}", f"F{old[1].id}"],
                    }
                )
    return result


def scenario_assessment(
    db: Session,
    company_id: int,
    scope: FinancialScope,
    scenarios: list[dict] | None = None,
    near_pct: Decimal = Decimal(10),
    extreme_pct: Decimal = Decimal(35),
) -> dict:
    market = latest_snapshot(db, company_id)
    _, details = calculate_valuation(db, market, scope) if market else (None, {})
    value = lambda key: (
        Decimal(details[key]["value"])
        if details.get(key, {}).get("value") is not None
        else None
    )
    eps, pe = value("ttm_eps"), value("pe_ttm")
    price = market.close_price if market else None
    evidence = (
        growth_evidence(db, company_id, scope, market.as_of_date) if market else []
    )
    if (
        market
        and market.forward_eps_growth_pct is not None
        and market.growth_source_url
    ):
        evidence.append(
            {
                "metric": "FORWARD_EPS_GROWTH",
                "value": str(market.forward_eps_growth_pct),
                "source_type": market.growth_source_type or "USER_ASSUMPTION",
                "source_url": market.growth_source_url,
                "evidence_ids": [f"Q{market.id}"],
                "note": "Sourced forward assumption; not an observed result or a verified forecast.",
            }
        )
    historical = (
        historical_pe_summary(db, company_id, market, scope) if market else None
    )
    stored = db.scalar(
        select(ValuationAssumptions).where(
            ValuationAssumptions.company_id == company_id,
            ValuationAssumptions.scope == scope.value,
        )
    )
    if scenarios is None and stored:
        scenarios = stored.scenarios
        near_pct, extreme_pct = stored.near_range_pct, stored.extreme_gap_pct
    if scenarios is None:
        anchor_item = next(
            (item for item in evidence if item["metric"] == "EPS_YOY"), None
        )
        anchor = Decimal(anchor_item["value"]) if anchor_item else Decimal(10)
        growths = (
            ((anchor - 100) / 2, anchor, anchor / 2)
            if anchor < 0
            else (anchor / 2, anchor, anchor * Decimal("1.5"))
        )
        growth_method = (
            "Growth anchored to latest observed EPS YoY; downside halfway to -100%, upside halfway to zero. Persistence is assumed."
            if anchor < 0
            else "Growth anchored to latest observed EPS YoY, scaled by 0.5/1/1.5; persistence is assumed."
        )
        # These are visible sensitivity assumptions, never claimed as fair multiples.
        scenarios = [
            {
                "name": name,
                "eps_growth_pct": str(growth),
                "terminal_pe": str(terminal),
                "years": 3,
                "required_return_pct": "12",
                "source_type": "SCENARIO_ASSUMPTION",
                "source": "Illustrative sensitivity: terminal P/E 20/30/40, horizon 3 years, required return 12%. "
                + (
                    growth_method
                    if anchor_item
                    else "Growth 5/10/15% is illustrative; no observed EPS growth anchor."
                ),
                "evidence_ids": next(
                    (
                        item["evidence_ids"]
                        for item in evidence
                        if item["metric"] == "EPS_YOY"
                    ),
                    [],
                ),
            }
            for name, growth, terminal in zip(
                ("CONSERVATIVE", "BASE", "OPTIMISTIC"), growths, (20, 30, 40)
            )
        ]
    missing = [
        label
        for label, item in (("positive verified TTM EPS", eps), ("dated price", price))
        if item is None or item <= 0
    ]
    rows = []
    for index, item in enumerate(scenarios):
        growth, terminal, required = (
            Decimal(str(item[key]))
            for key in ("eps_growth_pct", "terminal_pe", "required_return_pct")
        )
        years = int(item["years"])
        implied = implied_current_pe(growth, terminal, years, required)
        row = {
            **item,
            "evidence_id": f"A_{item['name']}",
            "implied_current_pe": str(implied.quantize(Decimal(".01"))),
        }
        if not missing:
            future_eps = eps * (1 + growth / 100) ** years
            row.update(
                future_eps=str(future_eps.quantize(Decimal(".000001"))),
                future_price=str((future_eps * terminal).quantize(Decimal(".01"))),
                present_value=str((eps * implied).quantize(Decimal(".01"))),
                reverse=reverse_growth(eps, price, terminal, years, required),
            )
        rows.append(row)
    base = next(row for row in rows if row["name"] == "BASE")
    multiples = [
        implied_current_pe(
            Decimal(str(row["eps_growth_pct"])),
            Decimal(str(row["terminal_pe"])),
            int(row["years"]),
            Decimal(str(row["required_return_pct"])),
        )
        for row in rows
    ]
    low, high = min(multiples), max(multiples)
    rounded = lambda number: str(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    verdict = (
        classify_valuation(pe, low, high, near_pct=near_pct, extreme_pct=extreme_pct)
        if not missing
        else "INSUFFICIENT_EVIDENCE"
    )
    age = (
        (datetime.now(ZoneInfo("Asia/Kolkata")).date() - market.as_of_date).days
        if market
        else None
    )
    scenario_verdict = verdict
    if age is not None and not 0 <= age <= 7:
        missing.append("Fresh dated market price for a current valuation verdict")
        verdict = "INSUFFICIENT_EVIDENCE"
    required_growth = (
        Decimal(base["reverse"]["required_eps_cagr_pct"])
        if base.get("reverse")
        else None
    )
    span = max(Decimal(str(r["eps_growth_pct"])) for r in rows) - min(
        Decimal(str(r["eps_growth_pct"])) for r in rows
    )
    return {
        "status": "CALCULATED" if not missing else "INSUFFICIENT_EVIDENCE",
        "label": "SCENARIO — NOT FORECAST",
        "verdict": verdict,
        "scenario_verdict": scenario_verdict,
        "confidence": "LOW" if not missing else "NOT_ASSESSED",
        "current_pe": str(pe.quantize(Decimal(".01"))) if pe else None,
        "current_price": str(price) if price else None,
        "ttm_eps": str(eps) if eps else None,
        "market_date": market.as_of_date.isoformat() if market else None,
        "price_age_days": age,
        "scope": scope.value,
        "scenarios": rows,
        "growth_comparisons": evidence,
        "supported_pe_range": (
            {
                "lower": rounded(low),
                "central": rounded(Decimal(base["implied_current_pe"])),
                "upper": rounded(high),
            }
            if not missing
            else None
        ),
        "current_position": (
            (
                "BELOW_RANGE"
                if pe < low
                else "ABOVE_RANGE" if pe > high else "WITHIN_RANGE"
            )
            if pe
            else None
        ),
        "reverse_expectations": base.get("reverse"),
        "expectations_gap": expectations_gap(required_growth, evidence, span),
        "historical_pe": historical,
        "input_evidence": details.get("pe_ttm", {}).get("inputs", []),
        "price_source": (
            {
                "provider": market.provider,
                "origin": market.data_origin,
                "url": market.source_url,
            }
            if market
            else None
        ),
        "lenses": {
            "forward_earnings": "EXPLICIT_SCENARIOS",
            "historical_valuation": (
                "CALCULATED" if historical and historical["one_year"] else "UNAVAILABLE"
            ),
            "peer_valuation": "UNAVAILABLE",
            "growth_adjusted": "OBSERVED_GROWTH" if evidence else "UNAVAILABLE",
        },
        "missing": missing,
        "quality_adjustment": "NONE — no hidden quality premium or discount",
        "methodology": f"Implied current P/E = terminal P/E × ((1 + EPS growth)/(1 + required return))^years. "
        f"Range is the minimum and maximum across scenarios; central is BASE. Near-range tolerance {near_pct}%; extreme gap {extreme_pct}%. "
        "Valuation is conditional on these disclosed assumptions; historical observations and peers are not silently blended.",
    }
