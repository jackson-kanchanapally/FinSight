"""Auditable historical valuations and user-defined scenarios."""

from decimal import Decimal
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.market.service import latest_snapshot, market_history
from app.market.valuation import recalculate, scenario, reverse_growth, valuation_change
from app.market.reference import comparisons
from app.market.expectations import implied_current_pe, latest_quarter_eps_growth, scenario_assessment
from app.ai.analyst.reasoning import required_growth_gap
from app.config import settings
from app.models.company import Company
from app.models.financial import FinancialScope
from app.models.market import MarketSnapshot, ValuationSnapshot, ValuationAssumptions
from app.services.investor_verdict import tested_pe_range
from app.market.historical_pe import historical_pe_summary
from app.schemas.market import MarketSnapshotRead
from app.schemas.valuation import (
    ScenarioRequest,
    ScenarioResult,
    ReverseRequest,
    ReverseResult,
    ValuationRead,
)

router = APIRouter(tags=["valuation"])


class InvestorScenarioInput(BaseModel):
    name: Literal["CONSERVATIVE", "BASE", "OPTIMISTIC"]
    eps_growth_pct: Decimal = Field(gt=-100, le=1000)
    terminal_pe: Decimal = Field(gt=0, le=1000)
    years: int = Field(ge=1, le=20)
    required_return_pct: Decimal = Field(gt=-100, le=1000)
    source: str = Field(default="User selected scenario", min_length=1, max_length=500)


class TestedPERangeRequest(BaseModel):
    scope: FinancialScope = FinancialScope.CONSOLIDATED
    eps_growth_pct: Decimal | None = None
    terminal_pe_low: Decimal | None = Field(default=None, gt=0)
    terminal_pe_high: Decimal | None = Field(default=None, gt=0)
    years: int | None = Field(default=None, ge=1, le=20)
    required_return_pct: Decimal | None = Field(default=None, gt=-100)
    assumption_source: str | None = Field(default=None, max_length=200)
    assumption_date: date | None = None
    near_range_pct: Decimal = Field(default=Decimal(10), ge=0, le=50)
    extreme_gap_pct: Decimal = Field(default=Decimal(35), gt=0, le=100)
    scenarios: list[InvestorScenarioInput] | None = Field(
        default=None, min_length=3, max_length=3
    )

    @model_validator(mode="after")
    def validate_scenarios(self):
        if self.extreme_gap_pct <= self.near_range_pct:
            raise ValueError("Extreme gap must exceed near-range tolerance")
        if self.scenarios and {s.name for s in self.scenarios} != {
            "CONSERVATIVE",
            "BASE",
            "OPTIMISTIC",
        }:
            raise ValueError("Provide one conservative, base and optimistic scenario")
        if self.scenarios:
            multiples = {
                item.name: implied_current_pe(
                    item.eps_growth_pct,
                    item.terminal_pe,
                    item.years,
                    item.required_return_pct,
                )
                for item in self.scenarios
            }
            if not (
                multiples["CONSERVATIVE"] <= multiples["BASE"] <= multiples["OPTIMISTIC"]
            ):
                raise ValueError("Scenario P/E outcomes must rise from conservative to base to optimistic")
        return self


@router.post("/api/companies/{symbol}/valuation/tested-pe")
def tested_pe(
    symbol: str, payload: TestedPERangeRequest, db: Session = Depends(get_db)
) -> dict:
    company = _company(db, symbol)
    if payload.scenarios is not None:
        scenarios = [
            {
                **item.model_dump(mode="json"),
                "source_type": "USER_ASSUMPTION",
                "date": datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
                "evidence_ids": [],
            }
            for item in payload.scenarios
        ]
        result = scenario_assessment(
            db,
            company.id,
            payload.scope,
            scenarios,
            payload.near_range_pct,
            payload.extreme_gap_pct,
        )
        stored = db.scalar(
            select(ValuationAssumptions).where(
                ValuationAssumptions.company_id == company.id,
                ValuationAssumptions.scope == payload.scope.value,
            )
        )
        if stored is None:
            stored = ValuationAssumptions(
                company_id=company.id, scope=payload.scope.value
            )
            db.add(stored)
        stored.scenarios = scenarios
        stored.near_range_pct = payload.near_range_pct
        stored.extreme_gap_pct = payload.extreme_gap_pct
        db.commit()
        return result
    market = latest_snapshot(db, company.id)
    valuation = recalculate(db, market, payload.scope) if market else None
    try:
        result = tested_pe_range(
            current_eps=valuation.ttm_eps if valuation else None,
            current_price=market.close_price if market else None,
            growth_pct=payload.eps_growth_pct,
            terminal_pe_low=payload.terminal_pe_low,
            terminal_pe_high=payload.terminal_pe_high,
            years=payload.years,
            required_return_pct=payload.required_return_pct,
            assumption_source=payload.assumption_source,
            assumption_date=(
                payload.assumption_date.isoformat() if payload.assumption_date else None
            ),
            near_range_pct=payload.near_range_pct,
            extreme_gap_pct=payload.extreme_gap_pct,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result["market_date"] = market.as_of_date.isoformat() if market else None
    age = (
        (datetime.now(ZoneInfo("Asia/Kolkata")).date() - market.as_of_date).days
        if market
        else None
    )
    result["price_age_days"] = age
    if age is not None and (age < 0 or age > 7) and result["status"] == "CALCULATED":
        result["scenario_verdict"] = result["verdict"]
        result["verdict"] = "INSUFFICIENT_EVIDENCE"
        result["missing"].append(
            "Fresh dated market price for a current valuation verdict"
        )
    result["scope"] = payload.scope.value
    result["input_evidence"] = (
        valuation.metric_details.get("pe_ttm", {}).get("inputs", [])
        if valuation
        else []
    )
    result["current_pe"] = (
        str(valuation.pe_ttm.quantize(Decimal("0.01")))
        if valuation and valuation.pe_ttm is not None
        else None
    )
    result["ttm_eps"] = (
        str(valuation.ttm_eps) if valuation and valuation.ttm_eps is not None else None
    )
    result["current_price"] = str(market.close_price) if market else None
    result["price_source"] = (
        {
            "provider": market.provider,
            "url": market.source_url,
            "origin": market.data_origin,
        }
        if market
        else None
    )
    result["observed_eps_growth"] = (
        latest_quarter_eps_growth(db, company.id, payload.scope, market.as_of_date)
        if market
        else None
    )
    if market:
        history = historical_pe_summary(db, company.id, market, payload.scope)
        result["historical_pe"] = history
        result["lenses"]["historical_valuation"] = (
            "CALCULATED"
            if history["one_year"] or history["three_year"]
            else "UNAVAILABLE"
        )
    return result


@router.get("/api/companies/{symbol}/valuation/tested-pe")
def automatic_pe_assessment(
    symbol: str,
    scope: FinancialScope = FinancialScope.CONSOLIDATED,
    db: Session = Depends(get_db),
) -> dict:
    """Use saved inputs or fully disclosed illustrative sensitivity assumptions."""
    return scenario_assessment(db, _company(db, symbol).id, scope)


def _company(db: Session, symbol: str) -> Company:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _read(record: ValuationSnapshot, market: MarketSnapshot) -> ValuationRead:
    return ValuationRead.model_validate(
        {
            **record.__dict__,
            "market_date": market.as_of_date,
            "market": MarketSnapshotRead.model_validate(market),
        }
    )


@router.get("/api/companies/{symbol}/valuation", response_model=ValuationRead | None)
@router.get(
    "/api/companies/{symbol}/valuation/latest", response_model=ValuationRead | None
)
def latest_valuation(
    symbol: str,
    scope: FinancialScope = FinancialScope.CONSOLIDATED,
    db: Session = Depends(get_db),
) -> ValuationRead | None:
    company = _company(db, symbol)
    market = latest_snapshot(db, company.id)
    return _read(recalculate(db, market, scope), market) if market else None


@router.get(
    "/api/companies/{symbol}/valuation/history", response_model=list[ValuationRead]
)
def valuation_history(
    symbol: str,
    scope: FinancialScope = FinancialScope.CONSOLIDATED,
    limit: int = Query(default=100, ge=1, le=365),
    db: Session = Depends(get_db),
) -> list[ValuationRead]:
    company = _company(db, symbol)
    return [
        _read(recalculate(db, market, scope), market)
        for market in market_history(db, company.id, limit=limit)
    ]


@router.post(
    "/api/companies/{symbol}/valuation/recalculate", response_model=ValuationRead
)
def recalculate_valuation(
    symbol: str,
    market_snapshot_id: int | None = None,
    scope: FinancialScope = FinancialScope.CONSOLIDATED,
    db: Session = Depends(get_db),
) -> ValuationRead:
    company = _company(db, symbol)
    market = (
        db.get(MarketSnapshot, market_snapshot_id)
        if market_snapshot_id is not None
        else latest_snapshot(db, company.id)
    )
    if market is None or market.company_id != company.id:
        raise HTTPException(status_code=404, detail="Market snapshot not found")
    return _read(recalculate(db, market, scope), market)


@router.post(
    "/api/companies/{symbol}/valuation/scenario", response_model=list[ScenarioResult]
)
def valuation_scenario(
    symbol: str, payload: ScenarioRequest, db: Session = Depends(get_db)
) -> list[ScenarioResult]:
    company = _company(db, symbol)
    market = latest_snapshot(db, company.id)
    if market is None:
        raise HTTPException(status_code=422, detail="Market price is unavailable")
    valuation = recalculate(db, market, FinancialScope(payload.scope))
    if valuation.ttm_eps is None or valuation.ttm_eps <= 0:
        raise HTTPException(
            status_code=422, detail="Reliable positive TTM EPS is unavailable"
        )
    return [
        ScenarioResult(
            name=item.name,
            eps_growth_pct=item.eps_growth_pct,
            terminal_pe=item.terminal_pe,
            years=item.years,
            current_eps=valuation.ttm_eps,
            current_price=market.close_price,
            **scenario(
                valuation.ttm_eps,
                market.close_price,
                item.eps_growth_pct,
                item.terminal_pe,
                item.years,
            )
        )
        for item in payload.assumptions
    ]


@router.post("/api/companies/{symbol}/valuation/reverse", response_model=ReverseResult)
def valuation_reverse(
    symbol: str, payload: ReverseRequest, db: Session = Depends(get_db)
) -> ReverseResult:
    company = _company(db, symbol)
    market = latest_snapshot(db, company.id)
    if market is None:
        raise HTTPException(status_code=422, detail="Market price is unavailable")
    valuation = recalculate(db, market, FinancialScope(payload.scope))
    if valuation.ttm_eps is None or valuation.ttm_eps <= 0:
        raise HTTPException(
            status_code=422, detail="Reliable positive TTM EPS is unavailable"
        )
    scenario_result = reverse_growth(
        valuation.ttm_eps,
        market.close_price,
        payload.terminal_pe,
        payload.years,
        payload.target_annualized_return_pct,
    )
    observed = latest_quarter_eps_growth(
        db, company.id, FinancialScope(payload.scope), market.as_of_date
    )
    gap = required_growth_gap(
        Decimal(scenario_result["required_eps_cagr_pct"]),
        latest_eps_growth=observed["latest_eps_growth_pct"] if observed else None,
    )
    return ReverseResult(
        current_eps=valuation.ttm_eps,
        current_price=market.close_price,
        terminal_pe=payload.terminal_pe,
        years=payload.years,
        target_annualized_return_pct=payload.target_annualized_return_pct,
        observed_eps_growth=observed,
        growth_gap=gap["comparisons"]["latest_eps_growth"],
        **scenario_result
    )


@router.get("/api/companies/{symbol}/valuation/change")
def valuation_change_api(
    symbol: str,
    old_market_snapshot_id: int,
    new_market_snapshot_id: int,
    scope: FinancialScope = FinancialScope.CONSOLIDATED,
    db: Session = Depends(get_db),
) -> dict:
    company = _company(db, symbol)
    old_market = db.get(MarketSnapshot, old_market_snapshot_id)
    new_market = db.get(MarketSnapshot, new_market_snapshot_id)
    if (
        not old_market
        or not new_market
        or old_market.company_id != company.id
        or new_market.company_id != company.id
    ):
        raise HTTPException(status_code=404, detail="Market snapshot not found")
    old = recalculate(db, old_market, scope)
    new = recalculate(db, new_market, scope)
    try:
        return {
            "status": "CALCULATED",
            **valuation_change(old, new, old_market, new_market),
        }
    except ValueError:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": (
                "Historical valuation needs positive comparable diluted TTM EPS and P/E on both dates"
                if old_market.as_of_date < new_market.as_of_date
                else "The first market date must precede the second market date"
            ),
            "old_date": old_market.as_of_date.isoformat(),
            "new_date": new_market.as_of_date.isoformat(),
            "missing_inputs": [
                label
                for label, value in (
                    ("old TTM EPS", old.ttm_eps),
                    ("new TTM EPS", new.ttm_eps),
                    ("old P/E", old.pe_ttm),
                    ("new P/E", new.pe_ttm),
                )
                if value is None or value <= 0
            ],
        }


@router.get("/api/companies/{symbol}/valuation/reference")
def valuation_reference(
    symbol: str,
    scope: FinancialScope = FinancialScope.CONSOLIDATED,
    db: Session = Depends(get_db),
) -> list[dict]:
    company = _company(db, symbol)
    market = latest_snapshot(db, company.id)
    if market is None:
        return []
    valuation = recalculate(db, market, scope)
    from decimal import Decimal

    return comparisons(db, valuation, Decimal(str(settings.market_lens_tolerance_pct)))
