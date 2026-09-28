"""Auditable historical valuations and user-defined scenarios."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.market.service import latest_snapshot, market_history
from app.market.valuation import recalculate, scenario, reverse_growth, valuation_change
from app.market.reference import comparisons
from app.config import settings
from app.models.company import Company
from app.models.financial import FinancialScope
from app.models.market import MarketSnapshot, ValuationSnapshot
from app.schemas.market import MarketSnapshotRead
from app.schemas.valuation import (ScenarioRequest, ScenarioResult, ReverseRequest,
                                   ReverseResult, ValuationRead)

router = APIRouter(tags=["valuation"])


def _company(db: Session, symbol: str) -> Company:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _read(record: ValuationSnapshot, market: MarketSnapshot) -> ValuationRead:
    return ValuationRead.model_validate({**record.__dict__, "market_date": market.as_of_date,
                                         "market": MarketSnapshotRead.model_validate(market)})


@router.get("/api/companies/{symbol}/valuation", response_model=ValuationRead | None)
@router.get("/api/companies/{symbol}/valuation/latest", response_model=ValuationRead | None)
def latest_valuation(symbol: str, scope: FinancialScope = FinancialScope.CONSOLIDATED,
                     db: Session = Depends(get_db)) -> ValuationRead | None:
    company = _company(db, symbol)
    market = latest_snapshot(db, company.id)
    return _read(recalculate(db, market, scope), market) if market else None


@router.get("/api/companies/{symbol}/valuation/history", response_model=list[ValuationRead])
def valuation_history(symbol: str, scope: FinancialScope = FinancialScope.CONSOLIDATED,
                      limit: int = Query(default=100, ge=1, le=365),
                      db: Session = Depends(get_db)) -> list[ValuationRead]:
    company = _company(db, symbol)
    return [_read(recalculate(db, market, scope), market)
            for market in market_history(db, company.id, limit=limit)]


@router.post("/api/companies/{symbol}/valuation/recalculate", response_model=ValuationRead)
def recalculate_valuation(symbol: str, market_snapshot_id: int | None = None,
                          scope: FinancialScope = FinancialScope.CONSOLIDATED,
                          db: Session = Depends(get_db)) -> ValuationRead:
    company = _company(db, symbol)
    market = (db.get(MarketSnapshot, market_snapshot_id) if market_snapshot_id is not None else
              latest_snapshot(db, company.id))
    if market is None or market.company_id != company.id:
        raise HTTPException(status_code=404, detail="Market snapshot not found")
    return _read(recalculate(db, market, scope), market)


@router.post("/api/companies/{symbol}/valuation/scenario", response_model=list[ScenarioResult])
def valuation_scenario(symbol: str, payload: ScenarioRequest,
                       db: Session = Depends(get_db)) -> list[ScenarioResult]:
    company = _company(db, symbol)
    market = latest_snapshot(db, company.id)
    if market is None:
        raise HTTPException(status_code=422, detail="Market price is unavailable")
    valuation = recalculate(db, market, FinancialScope(payload.scope))
    if valuation.ttm_eps is None or valuation.ttm_eps <= 0:
        raise HTTPException(status_code=422, detail="Reliable positive TTM EPS is unavailable")
    return [ScenarioResult(name=item.name, eps_growth_pct=item.eps_growth_pct,
        terminal_pe=item.terminal_pe, years=item.years, current_eps=valuation.ttm_eps,
        current_price=market.close_price,
        **scenario(valuation.ttm_eps, market.close_price, item.eps_growth_pct,
                   item.terminal_pe, item.years)) for item in payload.assumptions]


@router.post("/api/companies/{symbol}/valuation/reverse", response_model=ReverseResult)
def valuation_reverse(symbol: str, payload: ReverseRequest,
                      db: Session = Depends(get_db)) -> ReverseResult:
    company = _company(db, symbol)
    market = latest_snapshot(db, company.id)
    if market is None:
        raise HTTPException(status_code=422, detail="Market price is unavailable")
    valuation = recalculate(db, market, FinancialScope(payload.scope))
    if valuation.ttm_eps is None or valuation.ttm_eps <= 0:
        raise HTTPException(status_code=422, detail="Reliable positive TTM EPS is unavailable")
    return ReverseResult(current_eps=valuation.ttm_eps, current_price=market.close_price,
        terminal_pe=payload.terminal_pe, years=payload.years,
        target_annualized_return_pct=payload.target_annualized_return_pct,
        **reverse_growth(valuation.ttm_eps, market.close_price, payload.terminal_pe,
                         payload.years, payload.target_annualized_return_pct))


@router.get("/api/companies/{symbol}/valuation/change")
def valuation_change_api(symbol: str, old_market_snapshot_id: int, new_market_snapshot_id: int,
                         scope: FinancialScope = FinancialScope.CONSOLIDATED,
                         db: Session = Depends(get_db)) -> dict:
    company = _company(db, symbol)
    old_market = db.get(MarketSnapshot, old_market_snapshot_id)
    new_market = db.get(MarketSnapshot, new_market_snapshot_id)
    if not old_market or not new_market or old_market.company_id != company.id or new_market.company_id != company.id:
        raise HTTPException(status_code=404, detail="Market snapshot not found")
    old = recalculate(db, old_market, scope)
    new = recalculate(db, new_market, scope)
    try:
        return {"status": "CALCULATED", **valuation_change(old, new, old_market, new_market)}
    except ValueError:
        return {"status": "INSUFFICIENT_DATA",
                "reason": ("Historical valuation needs positive comparable diluted TTM EPS and P/E on both dates"
                           if old_market.as_of_date < new_market.as_of_date else
                           "The first market date must precede the second market date"),
                "old_date": old_market.as_of_date.isoformat(),
                "new_date": new_market.as_of_date.isoformat(),
                "missing_inputs": [label for label, value in (("old TTM EPS", old.ttm_eps),
                    ("new TTM EPS", new.ttm_eps), ("old P/E", old.pe_ttm),
                    ("new P/E", new.pe_ttm)) if value is None or value <= 0]}


@router.get("/api/companies/{symbol}/valuation/reference")
def valuation_reference(symbol: str, scope: FinancialScope = FinancialScope.CONSOLIDATED,
                        db: Session = Depends(get_db)) -> list[dict]:
    company = _company(db, symbol)
    market = latest_snapshot(db, company.id)
    if market is None:
        return []
    valuation = recalculate(db, market, scope)
    from decimal import Decimal
    return comparisons(db, valuation, Decimal(str(settings.market_lens_tolerance_pct)))
