"""Idempotent local storage of market observations."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.config import settings
from app.market.providers import (AlphaVantageProvider, NSEMarketDataProvider, ManualMarketDataProvider,
                                  MarketDataProvider, MarketProviderError, PriceObservation)
from app.models.company import Company
from app.models.market import MarketSnapshot
from app.schemas.market import MarketSnapshotInput

INDIA = ZoneInfo("Asia/Kolkata")


def configured_provider() -> MarketDataProvider:
    if settings.market_data_provider == "nse_mcp":
        return NSEMarketDataProvider(settings.market_data_timeout_seconds)
    if settings.market_data_provider == "alpha_vantage" and settings.alpha_vantage_api_key:
        return AlphaVantageProvider(settings.alpha_vantage_api_key,
                                    settings.market_data_timeout_seconds)
    raise MarketProviderError("PROVIDER_NOT_CONFIGURED")


def latest_snapshot(db: Session, company_id: int, as_of: date | None = None) -> MarketSnapshot | None:
    query = select(MarketSnapshot).where(MarketSnapshot.company_id == company_id)
    if as_of is not None:
        query = query.where(MarketSnapshot.as_of_date <= as_of)
    priority = case((MarketSnapshot.provider == "NSE_MCP", 3),
                    (MarketSnapshot.provider == "ALPHA_VANTAGE", 2), else_=1)
    return db.scalar(query.order_by(MarketSnapshot.as_of_date.desc(), priority.desc(),
                                    MarketSnapshot.observed_at.desc(), MarketSnapshot.id.desc()))


def market_history(db: Session, company_id: int, start: date | None = None,
                   end: date | None = None, limit: int = 365) -> list[MarketSnapshot]:
    query = select(MarketSnapshot).where(MarketSnapshot.company_id == company_id)
    if start:
        query = query.where(MarketSnapshot.as_of_date >= start)
    if end:
        query = query.where(MarketSnapshot.as_of_date <= end)
    return list(db.scalars(query.order_by(MarketSnapshot.as_of_date.desc(),
                                          MarketSnapshot.observed_at.desc(),
                                          MarketSnapshot.id.desc()).limit(limit)))


def save_manual_snapshot(db: Session, company: Company,
                         payload: MarketSnapshotInput) -> MarketSnapshot:
    record = db.scalar(select(MarketSnapshot).where(
        MarketSnapshot.company_id == company.id, MarketSnapshot.as_of_date == payload.as_of_date,
        MarketSnapshot.provider == "MANUAL", MarketSnapshot.data_origin == "MANUAL"))
    if record is None:
        record = MarketSnapshot(company_id=company.id, as_of_date=payload.as_of_date,
                                provider="MANUAL", data_origin="MANUAL")
        db.add(record)
    observed = ManualMarketDataProvider.observation(payload.as_of_date, payload.close_price,
                                                    payload.source_url)
    for key, value in payload.model_dump().items():
        setattr(record, key, value)
    record.observed_at = observed.observed_at
    record.fetched_at = observed.fetched_at
    record.currency = "INR"
    record.provider_symbol = None
    db.commit()
    db.refresh(record)
    return record


def store_observations(db: Session, company: Company,
                       observations: list[PriceObservation]) -> list[MarketSnapshot]:
    records = []
    for item in observations:
        if item.currency != "INR" or item.close_price <= Decimal("0"):
            raise MarketProviderError("UNSUPPORTED_CURRENCY_OR_PRICE")
        record = db.scalar(select(MarketSnapshot).where(
            MarketSnapshot.company_id == company.id, MarketSnapshot.as_of_date == item.market_date,
            MarketSnapshot.provider == item.provider,
            MarketSnapshot.data_origin == item.data_origin))
        if record is None:
            record = MarketSnapshot(company_id=company.id, as_of_date=item.market_date,
                provider=item.provider, data_origin=item.data_origin,
                close_price=item.close_price, source_url=item.source_url,
                observed_at=item.observed_at, fetched_at=item.fetched_at,
                provider_symbol=item.provider_symbol, currency=item.currency)
            record.open_price = item.open_price
            record.high_price = item.high_price
            record.low_price = item.low_price
            record.volume = item.volume
            record.note = item.note
            db.add(record)
        else:
            # Keep the first stored EOD close stable; later provider changes need review.
            record.fetched_at = item.fetched_at
        records.append(record)
    db.commit()
    for record in records:
        db.refresh(record)
    return records


def refresh_market(db: Session, company: Company, provider: MarketDataProvider | None = None,
                   start: date | None = None, end: date | None = None,
                   force: bool = False) -> list[MarketSnapshot]:
    provider = provider or configured_provider()
    today = datetime.now(INDIA).date()
    end = end or today
    start = start or end - timedelta(days=14)
    if start > end or end > today:
        raise MarketProviderError("INVALID_DATE_RANGE")
    stored = list(db.scalars(select(MarketSnapshot).where(
        MarketSnapshot.company_id == company.id, MarketSnapshot.provider == provider.name,
        MarketSnapshot.as_of_date >= start, MarketSnapshot.as_of_date <= end)
        .order_by(MarketSnapshot.as_of_date)))
    latest = stored[-1] if stored else None
    fetched_at = (latest.fetched_at.replace(tzinfo=timezone.utc) if latest and
                  latest.fetched_at.tzinfo is None else latest.fetched_at if latest else None)
    if not force and latest and fetched_at.astimezone(INDIA).date() == today:
        # A fetched window is good for the day. Do not fetch it again on each page load.
        if start >= stored[0].as_of_date and end <= latest.as_of_date + timedelta(days=7):
            return stored
    ranges = [(start, end)]
    if stored and not force:
        ranges = []
        if start < stored[0].as_of_date:
            ranges.append((start, stored[0].as_of_date - timedelta(days=1)))
        if end > latest.as_of_date and (fetched_at is None or
                                         fetched_at.astimezone(INDIA).date() != today):
            ranges.append((latest.as_of_date + timedelta(days=1), end))
    quotes = [item for first, last in ranges
              for item in provider.get_price_history(company, first, last)]
    if not quotes and not stored:
        raise MarketProviderError("NO_PRICE_HISTORY")
    if quotes and end == today and max([item.market_date for item in quotes] +
                                       [item.as_of_date for item in stored]) < today - timedelta(days=7):
        raise MarketProviderError("STALE_PROVIDER_RESPONSE")
    if quotes:
        store_observations(db, company, quotes)
    return list(db.scalars(select(MarketSnapshot).where(MarketSnapshot.company_id == company.id,
        MarketSnapshot.provider == provider.name, MarketSnapshot.as_of_date >= start,
        MarketSnapshot.as_of_date <= end).order_by(MarketSnapshot.as_of_date)))
