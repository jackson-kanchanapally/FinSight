import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from sqlalchemy import select

from app.api import (
    analysis,
    companies,
    events,
    financials,
    market,
    memory,
    portfolio,
    transactions,
    valuation,
    thesis,
)
from app.config import settings
from app.database import SessionLocal
from app.ingestion.monitor import check_for_events
from app.market.providers import MarketProviderError
from app.market.service import refresh_market
from app.market.valuation import recalculate
from app.models.company import Company
from app.models.financial import FinancialScope

logger = logging.getLogger(__name__)


async def scheduled_check() -> None:
    try:
        with SessionLocal() as db:
            await check_for_events(db)
    except Exception:
        logger.exception("event_monitor.scheduled_check_failed")


async def scheduled_market_refresh() -> None:
    """One EOD request per eligible company; preserve stored data on failure."""
    with SessionLocal() as db:
        query = select(Company).order_by(Company.id).limit(20)
        if settings.market_data_provider == "alpha_vantage":
            query = query.where(Company.bse_code.is_not(None))
        else:
            query = query.where(Company.exchange == "NSE")
        companies = list(db.scalars(query))
        for company in companies:
            try:
                for market in refresh_market(db, company):
                    recalculate(db, market, FinancialScope.CONSOLIDATED)
            except MarketProviderError as exc:
                logger.warning(
                    "market_data.refresh_failed company=%s reason=%s",
                    company.symbol,
                    exc.reason,
                )
            except Exception:
                logger.exception(
                    "market_data.unexpected_refresh_failure company=%s", company.symbol
                )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler = None
    if settings.event_monitor_enabled or settings.market_data_enabled:
        scheduler = AsyncIOScheduler(timezone="UTC")
        if settings.event_monitor_enabled:
            scheduler.add_job(
                scheduled_check,
                "interval",
                minutes=settings.event_monitor_interval_minutes,
                id="event_monitor",
                max_instances=1,
                coalesce=True,
            )
        if settings.market_data_enabled and settings.market_data_provider != "manual":
            scheduler.add_job(
                scheduled_market_refresh,
                "cron",
                hour=settings.market_data_refresh_hour,
                minute=settings.market_data_refresh_minute,
                day_of_week="mon-fri",
                timezone="Asia/Kolkata",
                id="market_data_eod",
                max_instances=1,
                coalesce=True,
            )
        scheduler.start()
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)


app = FastAPI(
    title="almonds",
    version="0.5.0",
    description="Portfolio, verified reporting, and local AI interpretation",
    lifespan=lifespan,
)
app.include_router(companies.router)
app.include_router(transactions.router)
app.include_router(portfolio.router)
app.include_router(events.router)
app.include_router(financials.router)
app.include_router(analysis.router)
app.include_router(market.router)
app.include_router(valuation.router)
app.include_router(memory.router)
app.include_router(thesis.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
