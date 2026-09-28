from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.market.providers import MarketProviderError
from app.market.service import (latest_snapshot, market_history, refresh_market,
                                save_manual_snapshot)
from app.market.valuation import recalculate
from app.market.reference import import_market_lens_csv
from app.models.company import Company
from app.models.financial import FinancialScope
from app.models.market import MarketSnapshot
from app.schemas.market import MarketSnapshotInput, MarketSnapshotRead

router = APIRouter(tags=["market snapshots"])


class MarketLensImport(BaseModel):
    csv_text: str = Field(min_length=10, max_length=2_000_000)
    as_of_date: date
    source_url: str = Field(pattern=r"^https://marketlens\.nseindia\.com/", max_length=2048)


@router.post("/api/market-lens/import")
def import_reference(payload: MarketLensImport, db: Session = Depends(get_db)) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if payload.as_of_date > datetime.now(ZoneInfo("Asia/Kolkata")).date():
        raise HTTPException(status_code=422, detail="Reference date cannot be in the future")
    try:
        return import_market_lens_csv(db, payload.csv_text, payload.as_of_date, payload.source_url)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _company(db: Session, symbol: str) -> Company:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/api/companies/{symbol}/market-snapshot", response_model=MarketSnapshotRead | None)
def market_snapshot(symbol: str, db: Session = Depends(get_db)) -> MarketSnapshot | None:
    company = _company(db, symbol)
    return db.scalar(select(MarketSnapshot).where(
        MarketSnapshot.company_id == company.id, MarketSnapshot.provider == "MANUAL")
        .order_by(MarketSnapshot.as_of_date.desc(), MarketSnapshot.id.desc()))


@router.put("/api/companies/{symbol}/market-snapshot", response_model=MarketSnapshotRead)
def save_market_snapshot(symbol: str, payload: MarketSnapshotInput,
                         db: Session = Depends(get_db)) -> MarketSnapshot:
    company = _company(db, symbol)
    record = save_manual_snapshot(db, company, payload)
    recalculate(db, record, FinancialScope.CONSOLIDATED)
    return record


@router.get("/api/companies/{symbol}/market")
def market_status(symbol: str, db: Session = Depends(get_db)) -> dict:
    company = _company(db, symbol)
    snapshot = latest_snapshot(db, company.id)
    return {"latest": MarketSnapshotRead.model_validate(snapshot) if snapshot else None,
            "provider": settings.market_data_provider,
            "refresh_available": (settings.market_data_provider == "nse_mcp" and company.exchange.upper() == "NSE")
                or (settings.market_data_provider == "alpha_vantage" and bool(settings.alpha_vantage_api_key)
                    and bool(company.bse_code))}


@router.get("/api/companies/{symbol}/market/history", response_model=list[MarketSnapshotRead])
def price_history(symbol: str, start: date | None = None, end: date | None = None,
                  limit: int = Query(default=365, ge=1, le=1000),
                  db: Session = Depends(get_db)) -> list[MarketSnapshot]:
    company = _company(db, symbol)
    return market_history(db, company.id, start, end, limit)


@router.post("/api/companies/{symbol}/market/refresh")
def refresh_price(symbol: str, start: date | None = None, end: date | None = None,
                  db: Session = Depends(get_db)) -> dict:
    company = _company(db, symbol)
    try:
        records = refresh_market(db, company, start=start, end=end)
    except MarketProviderError as exc:
        status = (429 if exc.reason == "RATE_LIMITED" else 503 if exc.reason in {
            "PROVIDER_NOT_CONFIGURED", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"} else 422)
        raise HTTPException(status_code=status, detail=exc.reason) from exc
    for record in records:
        recalculate(db, record, FinancialScope.CONSOLIDATED)
    latest = latest_snapshot(db, company.id)
    return {"stored": len(records), "latest": MarketSnapshotRead.model_validate(latest)}
