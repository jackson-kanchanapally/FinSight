import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.api import analysis, companies, events, financials, market, portfolio, transactions
from app.config import settings
from app.database import SessionLocal
from app.ingestion.monitor import check_for_events

logger = logging.getLogger(__name__)


async def scheduled_check() -> None:
    try:
        with SessionLocal() as db:
            await check_for_events(db)
    except Exception:
        logger.exception("event_monitor.scheduled_check_failed")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler = None
    if settings.event_monitor_enabled:
        scheduler = AsyncIOScheduler(timezone="UTC")
        scheduler.add_job(scheduled_check, "interval", minutes=settings.event_monitor_interval_minutes,
                          id="event_monitor", max_instances=1, coalesce=True)
        scheduler.start()
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)

app = FastAPI(title="FinSight", version="0.5.0", description="Portfolio, verified reporting, and local AI interpretation", lifespan=lifespan)
app.include_router(companies.router)
app.include_router(transactions.router)
app.include_router(portfolio.router)
app.include_router(events.router)
app.include_router(financials.router)
app.include_router(analysis.router)
app.include_router(market.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
