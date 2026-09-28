"""Point-in-time TTM and valuation calculations from verified source facts."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.event import CompanyEvent, EventType, PublicationPrecision
from app.models.financial import (FactStatus, FinancialFact, FinancialPeriod, FinancialScope,
                                  FinancialStatus, FinancialUnit, MetricCode, PeriodType)
from app.models.market import MarketSnapshot, ValuationSnapshot

INDIA = ZoneInfo("Asia/Kolkata")
VERIFIED = {FactStatus.VERIFIED_PDF, FactStatus.VERIFIED_STRUCTURED, FactStatus.MANUALLY_VERIFIED}
ONE_CRORE = Decimal("10000000")
HUNDRED = Decimal("100")
QUANT = Decimal("0.000001")


def _number(value: Decimal) -> str:
    return str(value.quantize(QUANT, rounding=ROUND_HALF_UP))


def _detail(value: Decimal | None, inputs: list[str], formula: str,
            reason: str | None = None, status: str | None = None) -> dict:
    return {"value": _number(value) if value is not None else None,
            "status": status or ("VALID" if value is not None else "INSUFFICIENT_DATA"),
            "inputs": inputs, "formula": formula, "reason": reason}


def _available(event: CompanyEvent, market_date: date) -> bool:
    published = event.published_at
    if published.tzinfo is None:
        published = published.replace(tzinfo=INDIA)
    local = published.astimezone(INDIA)
    if event.publication_precision == PublicationPrecision.DATE:
        # A date-only filing may have appeared after the closing auction.
        return local.date() < market_date
    return local <= datetime.combine(market_date, time(15, 30), INDIA)


def _eligible_periods(db: Session, company_id: int, market_date: date,
                      scope: FinancialScope) -> list[FinancialPeriod]:
    periods = list(db.scalars(select(FinancialPeriod).options(selectinload(FinancialPeriod.facts))
        .where(FinancialPeriod.company_id == company_id,
               FinancialPeriod.scope == scope,
               FinancialPeriod.status == FinancialStatus.VALIDATED,
               FinancialPeriod.period_end <= market_date)
        .order_by(FinancialPeriod.period_end.desc(), FinancialPeriod.id.desc())))
    return [period for period in periods if (event := db.get(CompanyEvent, period.source_event_id))
            is not None and _available(event, market_date)]


def _fact(period: FinancialPeriod, code: MetricCode) -> FinancialFact | None:
    return next((fact for fact in period.facts if fact.metric_code == code
                 and fact.status in VERIFIED and fact.unit in {FinancialUnit.INR_CRORE,
                                                                 FinancialUnit.INR_PER_SHARE}), None)


def _quarter_chain(periods: list[FinancialPeriod]) -> list[FinancialPeriod]:
    quarters = [p for p in periods if p.period_type == PeriodType.QUARTERLY]
    if not quarters:
        return []
    by_end = {p.period_end: p for p in quarters}
    chain = [quarters[0]]
    for _ in range(3):
        previous = by_end.get(chain[-1].period_start - timedelta(days=1))
        if previous is None or previous.period_end >= chain[-1].period_end:
            break
        chain.append(previous)
    return chain


def _annual_bridge(periods: list[FinancialPeriod]) -> list[FinancialPeriod]:
    """FY annual + current Q1 - prior Q1 covers the same trailing year."""
    quarters = [p for p in periods if p.period_type == PeriodType.QUARTERLY and p.quarter == 1]
    if not quarters:
        return []
    current = quarters[0]
    annual_end = current.period_start - timedelta(days=1)
    annual = next((p for p in periods if p.period_type == PeriodType.ANNUAL
                   and p.period_end == annual_end), None)
    prior = next((p for p in quarters if p.period_end.year == current.period_end.year - 1
                  and p.period_end.month == current.period_end.month
                  and p.period_end.day == current.period_end.day), None)
    if annual is None or prior is None or prior.period_start != annual.period_start:
        return []
    return [annual, current, prior]


def _ttm_fact(chain: list[FinancialPeriod], code: MetricCode,
              unit: FinancialUnit = FinancialUnit.INR_CRORE,
              bridge: bool = False) -> dict:
    formula = (f"FY annual + current Q1 - prior Q1 {code.value} facts" if bridge else
               f"Sum of four quarterly {code.value} facts")
    if len(chain) != (3 if bridge else 4):
        return _detail(None, [], f"Sum of four quarterly {code.value} facts",
                       "Four consecutive validated quarters or a verified annual bridge are required")
    facts = [_fact(period, code) for period in chain]
    if any(fact is None or fact.unit != unit for fact in facts):
        return _detail(None, [], formula,
                       "A compatible verified quarterly fact is missing")
    value = facts[0].value + facts[1].value - facts[2].value if bridge else sum(
        (fact.value for fact in facts), Decimal(0))
    return _detail(value, [f"F{fact.id}" for fact in facts], formula)


def _ttm_ebitda(chain: list[FinancialPeriod], bridge: bool = False) -> dict:
    formula = ("FY annual + current Q1 - prior Q1 (PBT + finance costs + depreciation)" if bridge else
               "Sum of four quarterly (PBT + finance costs + depreciation)")
    if len(chain) != (3 if bridge else 4):
        return _detail(None, [], formula,
                       "Four consecutive validated quarters or a verified annual bridge are required")
    all_facts = []
    values = []
    for period in chain:
        facts = [_fact(period, code) for code in (MetricCode.PBT, MetricCode.FINANCE_COST,
                                                   MetricCode.DEPRECIATION)]
        if any(fact is None or fact.unit != FinancialUnit.INR_CRORE for fact in facts):
            return _detail(None, [], formula,
                           "A verified EBITDA input is missing")
        all_facts.extend(facts)
        values.append(sum((fact.value for fact in facts), Decimal(0)))
    value = values[0] + values[1] - values[2] if bridge else sum(values, Decimal(0))
    return _detail(value, [f"F{fact.id}" for fact in all_facts], formula)


def _ttm_eps(db: Session, chain: list[FinancialPeriod], market_date: date,
             bridge: bool = False) -> dict:
    formula = ("FY annual + current Q1 - prior Q1 reported diluted EPS" if bridge else
               "Sum of four reported diluted quarterly EPS values")
    if len(chain) != (3 if bridge else 4):
        return _detail(None, [], formula, "Four consecutive validated quarters or a verified annual bridge are required")
    actions = list(db.scalars(select(CompanyEvent).where(
        CompanyEvent.company_id == chain[0].company_id,
        CompanyEvent.event_type.in_((EventType.STOCK_SPLIT, EventType.BONUS)))))
    earliest = min(period.period_start for period in chain)
    if any(earliest <= (action.published_at if action.published_at.tzinfo else
           action.published_at.replace(tzinfo=INDIA)).astimezone(INDIA).date() <= market_date
           for action in actions):
        return _detail(None, [], formula, "A split or bonus event requires an adjusted EPS history",
                       "NEEDS_REVIEW")
    eps = [_fact(period, MetricCode.DILUTED_EPS) for period in chain]
    pat = [_fact(period, MetricCode.PAT) for period in chain]
    if any(fact is None or fact.unit != FinancialUnit.INR_PER_SHARE for fact in eps):
        return _detail(None, [], formula, "Reported diluted EPS is missing or has a different basis")
    if any(fact is None or fact.unit != FinancialUnit.INR_CRORE for fact in pat):
        return _detail(None, [], formula, "PAT is needed to check implied share-count stability")
    if any(a.value <= 0 or b.value <= 0 for a, b in zip(eps, pat)):
        return _detail(None, [], formula, "Nonpositive quarterly PAT or EPS prevents a share-count check",
                       "NEEDS_REVIEW")
    implied = [b.value * ONE_CRORE / a.value for a, b in zip(eps, pat)]
    if max(implied) / min(implied) > Decimal("1.10"):
        return _detail(None, [f"F{fact.id}" for fact in [*eps, *pat]], formula,
                       "Implied diluted share counts vary by more than 10%; review EPS comparability",
                       "NEEDS_REVIEW")
    value = eps[0].value + eps[1].value - eps[2].value if bridge else sum(
        (fact.value for fact in eps), Decimal(0))
    if value <= 0:
        return _detail(None, [f"F{fact.id}" for fact in [*eps, *pat]], formula,
                       "Trailing diluted EPS is zero or negative", "NOT_MEANINGFUL")
    return _detail(value,
                   [f"F{fact.id}" for fact in [*eps, *pat]],
                   formula + "; implied share-count variation checked")


def ttm_at(db: Session, company_id: int, market_date: date,
           scope: FinancialScope) -> tuple[int | None, dict[str, dict]]:
    eligible = _eligible_periods(db, company_id, market_date, scope)
    chain = _quarter_chain(eligible)
    bridge = False
    if len(chain) != 4:
        annual_bridge = _annual_bridge(eligible)
        if annual_bridge:
            chain = annual_bridge
            bridge = True
    latest_id = eligible[0].id if eligible else None
    result = {
        "revenue": _ttm_fact(chain, MetricCode.REVENUE_FROM_OPERATIONS, bridge=bridge),
        "pat": _ttm_fact(chain, MetricCode.PAT, bridge=bridge),
        "ebitda": _ttm_ebitda(chain, bridge),
        "ebit": _ttm_fact(chain, MetricCode.EBIT, bridge=bridge),
        "eps": _ttm_eps(db, chain, market_date, bridge),
        "operating_cash_flow": _ttm_fact(chain, MetricCode.OPERATING_CASH_FLOW, bridge=bridge),
        "capex": _ttm_fact(chain, MetricCode.CAPITAL_EXPENDITURE, bridge=bridge),
    }
    cash = result["operating_cash_flow"]
    capex = result["capex"]
    result["free_cash_flow"] = (_detail(Decimal(cash["value"]) - abs(Decimal(capex["value"])),
        cash["inputs"] + capex["inputs"], "TTM operating cash flow - absolute TTM capex")
        if cash["value"] is not None and capex["value"] is not None else
        _detail(None, [], "TTM operating cash flow - absolute TTM capex",
                "Operating cash flow or capex is unavailable"))
    result["period_ids"] = [period.id for period in chain]
    result["method"] = "ANNUAL_BRIDGE" if bridge else "FOUR_QUARTERS"
    return latest_id, result


def _latest_balance(periods: list[FinancialPeriod], code: MetricCode) -> dict:
    for period in periods:
        fact = _fact(period, code)
        if fact and fact.unit == FinancialUnit.INR_CRORE:
            return _detail(fact.value, [f"F{fact.id}"], f"Latest verified {code.value} balance")
    return _detail(None, [], f"Latest verified {code.value} balance",
                   "No point-in-time verified balance-sheet fact")


def _divide(numerator: Decimal | None, denominator: Decimal | None,
            inputs: list[str], formula: str, *, meaningful: bool = True) -> dict:
    if numerator is None or denominator is None:
        return _detail(None, inputs, formula, "A required input is unavailable")
    if denominator <= 0 and meaningful:
        return _detail(None, inputs, formula, "Denominator is zero or negative", "NOT_MEANINGFUL")
    if denominator == 0:
        return _detail(None, inputs, formula, "Denominator is zero", "NOT_MEANINGFUL")
    return _detail(numerator / denominator, inputs, formula)


def calculate_valuation(db: Session, snapshot: MarketSnapshot,
                        scope: FinancialScope) -> tuple[int | None, dict[str, dict]]:
    latest_id, ttm = ttm_at(db, snapshot.company_id, snapshot.as_of_date, scope)
    eligible = _eligible_periods(db, snapshot.company_id, snapshot.as_of_date, scope)
    balance = {key: _latest_balance(eligible, code) for key, code in (
        ("debt", MetricCode.TOTAL_DEBT), ("cash", MetricCode.CASH_AND_CASH_EQUIVALENTS),
        ("equity", MetricCode.TOTAL_EQUITY))}
    q = f"Q{snapshot.id}"
    cap = (snapshot.market_cap_crore if snapshot.market_cap_crore is not None else
           snapshot.close_price * snapshot.shares_outstanding / ONE_CRORE
           if snapshot.shares_outstanding is not None else None)
    cap_formula = ("Sourced market capitalization" if snapshot.market_cap_crore is not None else
                   "Price × shares outstanding / 10,000,000")
    metrics = {"market_cap": _detail(cap, [q] if cap is not None else [], cap_formula,
              None if cap is not None else "Market cap or sourced shares outstanding is required"),
               **{f"ttm_{key}": value for key, value in ttm.items() if key not in {"period_ids", "method"}},
               **balance}
    price = snapshot.close_price
    eps = Decimal(ttm["eps"]["value"]) if ttm["eps"]["value"] is not None else None
    revenue = Decimal(ttm["revenue"]["value"]) if ttm["revenue"]["value"] is not None else None
    ebitda = Decimal(ttm["ebitda"]["value"]) if ttm["ebitda"]["value"] is not None else None
    fcf = Decimal(ttm["free_cash_flow"]["value"]) if ttm["free_cash_flow"]["value"] is not None else None
    metrics["pe_ttm"] = _divide(price, eps, [q, *ttm["eps"]["inputs"]], "Price / diluted TTM EPS")
    metrics["ps_ttm"] = _divide(cap, revenue, [q, *ttm["revenue"]["inputs"]],
                                 "Market cap / TTM revenue")
    metrics["earnings_yield_pct"] = (_detail(eps / price * HUNDRED,
        [q, *ttm["eps"]["inputs"]], "Diluted TTM EPS / price × 100") if eps is not None else
        _detail(None, [], "Diluted TTM EPS / price × 100", "Reliable TTM EPS is unavailable"))
    debt = Decimal(balance["debt"]["value"]) if balance["debt"]["value"] is not None else None
    cash = Decimal(balance["cash"]["value"]) if balance["cash"]["value"] is not None else None
    ev = cap + debt - cash if cap is not None and debt is not None and cash is not None else None
    ev_inputs = [q, *balance["debt"]["inputs"], *balance["cash"]["inputs"]]
    metrics["enterprise_value_crore"] = _detail(ev, ev_inputs, "Market cap + total debt - cash",
        None if ev is not None else "Market cap, verified debt, and verified cash are all required")
    metrics["ev_sales_ttm"] = _divide(ev, revenue, [*ev_inputs, *ttm["revenue"]["inputs"]],
                                       "Enterprise value / TTM revenue")
    metrics["ev_ebitda_ttm"] = _divide(ev, ebitda, [*ev_inputs, *ttm["ebitda"]["inputs"]],
                                        "Enterprise value / TTM EBITDA")
    equity = Decimal(balance["equity"]["value"]) if balance["equity"]["value"] is not None else None
    metrics["price_to_book"] = _divide(cap, equity, [q, *balance["equity"]["inputs"]],
                                        "Market cap / latest verified total equity")
    metrics["fcf_yield_pct"] = (_detail(fcf / cap * HUNDRED,
        [q, *ttm["free_cash_flow"]["inputs"]], "TTM free cash flow / market cap × 100",
        "Negative free cash flow" if fcf < 0 else None)
        if fcf is not None and cap is not None and cap > 0 else
        _detail(None, [], "TTM free cash flow / market cap × 100",
                "TTM free cash flow or market cap is unavailable"))
    growth = snapshot.forward_eps_growth_pct
    pe = metrics["pe_ttm"]["value"]
    metrics["peg_forward"] = (_detail(Decimal(pe) / growth,
        [q, *ttm["eps"]["inputs"]], "Calculated P/E TTM / forward annual EPS growth (%)",
        f"{snapshot.growth_source_type or 'USER_ASSUMPTION'}; {snapshot.growth_source_url}")
        if pe is not None and growth is not None and growth > 0 and snapshot.growth_source_url else
        _detail(None, [], "Calculated P/E TTM / forward annual EPS growth (%)",
                "Calculated P/E and sourced positive forward EPS growth are required"))
    metrics["metadata"] = {"market_date": snapshot.as_of_date.isoformat(),
        "scope": scope.value, "period_ids": ttm["period_ids"],
        "ttm_method": ttm["method"],
        "growth_rate": str(growth) if growth is not None else None,
        "growth_type": snapshot.growth_source_type,
        "growth_source": snapshot.growth_source_url,
        "price_source": snapshot.source_url, "price_provider": snapshot.provider}
    return latest_id, metrics


def recalculate(db: Session, snapshot: MarketSnapshot,
                scope: FinancialScope) -> ValuationSnapshot:
    period_id, details = calculate_valuation(db, snapshot, scope)
    record = db.scalar(select(ValuationSnapshot).where(
        ValuationSnapshot.market_snapshot_id == snapshot.id,
        ValuationSnapshot.scope == scope.value))
    if record is None:
        record = ValuationSnapshot(company_id=snapshot.company_id,
            market_snapshot_id=snapshot.id, scope=scope.value)
        db.add(record)
    record.financial_period_id = period_id
    record.metric_details = details
    columns = ("ttm_revenue", "ttm_ebitda", "ttm_pat", "ttm_eps", "pe_ttm", "ps_ttm",
               "ev_sales_ttm", "ev_ebitda_ttm", "earnings_yield_pct", "price_to_book",
               "fcf_yield_pct", "peg_forward", "enterprise_value_crore")
    for name in columns:
        item = details.get(name)
        setattr(record, name, Decimal(item["value"]) if item and item["value"] is not None else None)
    valid = sum(details[name]["status"] == "VALID" for name in columns)
    record.calculation_status = "PARTIAL" if valid else "INSUFFICIENT_DATA"
    db.commit()
    db.refresh(record)
    return record


def scenario(current_eps: Decimal, current_price: Decimal, growth_pct: Decimal,
             terminal_pe: Decimal, years: int) -> dict[str, str]:
    if current_eps <= 0 or current_price <= 0 or terminal_pe <= 0 or years < 1 or growth_pct <= -100:
        raise ValueError("SCENARIO_INPUT_NOT_MEANINGFUL")
    future_eps = current_eps * (Decimal(1) + growth_pct / HUNDRED) ** years
    future_price = future_eps * terminal_pe
    annualized = ((future_price / current_price) ** (Decimal(1) / Decimal(years)) - 1) * HUNDRED
    return {"future_eps": _number(future_eps), "implied_future_price": _number(future_price),
            "total_return_pct": _number((future_price / current_price - 1) * HUNDRED),
            "annualized_return_pct": _number(annualized)}


def reverse_growth(current_eps: Decimal, current_price: Decimal, terminal_pe: Decimal,
                   years: int, target_annualized_return_pct: Decimal) -> dict[str, str]:
    if (current_eps <= 0 or current_price <= 0 or terminal_pe <= 0 or years < 1
            or target_annualized_return_pct <= -100):
        raise ValueError("REVERSE_INPUT_NOT_MEANINGFUL")
    target_price = current_price * (1 + target_annualized_return_pct / HUNDRED) ** years
    target_eps = target_price / terminal_pe
    required = ((target_eps / current_eps) ** (Decimal(1) / Decimal(years)) - 1) * HUNDRED
    return {"target_future_price": _number(target_price), "required_future_eps": _number(target_eps),
            "required_eps_cagr_pct": _number(required), "label": "IMPLIED / REQUIRED GROWTH — not a forecast"}


def valuation_change(old: ValuationSnapshot, new: ValuationSnapshot,
                     old_market: MarketSnapshot, new_market: MarketSnapshot) -> dict:
    if (old.company_id != new.company_id or old.scope != new.scope or
            old_market.as_of_date >= new_market.as_of_date or
            old.ttm_eps is None or new.ttm_eps is None or
            old.pe_ttm is None or new.pe_ttm is None or
            min(old.ttm_eps, new.ttm_eps, old.pe_ttm, new.pe_ttm) <= 0):
        raise ValueError("VALUATION_CHANGE_NOT_COMPARABLE")
    price_growth = (new_market.close_price / old_market.close_price - 1) * HUNDRED
    eps_growth = (new.ttm_eps / old.ttm_eps - 1) * HUNDRED
    pe_growth = (new.pe_ttm / old.pe_ttm - 1) * HUNDRED
    return {"old_date": old_market.as_of_date.isoformat(), "new_date": new_market.as_of_date.isoformat(),
        "old_price": _number(old_market.close_price), "new_price": _number(new_market.close_price),
        "old_eps": _number(old.ttm_eps), "new_eps": _number(new.ttm_eps),
        "old_pe": _number(old.pe_ttm), "new_pe": _number(new.pe_ttm),
        "price_change_pct": _number(price_growth), "eps_change_pct": _number(eps_growth),
        "pe_change_pct": _number(pe_growth), "method": "Price = diluted TTM EPS × calculated P/E"}
