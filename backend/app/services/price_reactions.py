"""How the share price moved after a company event, measured against the Nifty 50.

For each material event Almonds finds the **base session**: the last NSE close
that happened before the event was public. A filing time-stamped before 15:30 IST
on a trading day is first reflected in that day's close; a later one in the next
session's. A date-only filing is measured from the previous session, so its first
window may include a move from before the filing.

Returns are measured 1, 5 and 20 sessions after the base, close to close. Splits
and bonuses inside a window are removed with NSE adjustment factors. The excess
return is the stock's return minus the Nifty 50's over the same dates. NSE's
index history currently begins in October 2024, so older events have no excess return.

This is an observation of what the market did, not an explanation of why.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from statistics import median
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.market.nse_mcp import NSEMCPError, get_nse_mcp
from app.models.company import Company
from app.models.event import CompanyEvent, DataOrigin, EventType, PublicationPrecision
from app.models.market_events import CorporateActionSync, EventPriceReaction
from app.services.corporate_actions import ShareBasis, share_basis, sync_company

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
WINDOWS = (1, 5, 20)
BENCHMARK = "Nifty 50"
CLOSE = time(15, 30)
TRANSIENT = {"TIMEOUT", "UNAVAILABLE", "RATE_LIMITED", "PROTOCOL_ERROR", "MALFORMED_RESPONSE", "DISABLED"}
HISTORY_DAYS = 5 * 365
PENDING_GRACE_DAYS = 45
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Bar:
    close: Decimal
    volume: int | None


def material_types() -> list[EventType]:
    names = {part.strip().upper() for part in settings.price_reaction_event_types.split(",") if part.strip()}
    return [member for name, member in EventType.__members__.items() if name in names]


def _dec(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _pct(ratio: Decimal) -> str:
    return str(((ratio - 1) * 100).quantize(CENT))


def _local(value: datetime) -> datetime:
    return (value if value.tzinfo else value.replace(tzinfo=IST)).astimezone(IST)


# -- data -----------------------------------------------------------------------

async def fetch_stock_bars(symbol: str, start: date, end: date) -> dict[date, Bar]:
    """Daily Bhavcopy closes and volumes, paging backwards three months at a time."""
    client = get_nse_mcp()
    bars: dict[date, Bar] = {}
    cursor = end
    for _ in range(24):
        data = await client.call(
            "bhavcopy", "get_stock_history", {"symbol": symbol, "months": 3, "endDate": cursor.isoformat()}
        )
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict) or str(row.get("symbol", symbol)).upper() != symbol:
                continue
            try:
                day = date.fromisoformat(str(row.get("date")))
            except ValueError:
                continue
            close = _dec(row.get("close"))
            if close is None or close <= 0:
                continue
            raw_volume = row.get("volume")
            volume = int(raw_volume) if isinstance(raw_volume, (int, float)) and raw_volume >= 0 else None
            bars[day] = Bar(close, volume)
        try:
            following = date.fromisoformat(str(data.get("next_end_date")))
        except ValueError:
            break
        if following < start or following >= cursor:
            break
        cursor = following
    return bars


async def fetch_index_closes(start: date, end: date) -> dict[date, Decimal]:
    """Nifty 50 closes; NSE's index history currently starts in October 2024."""
    client = get_nse_mcp()
    closes: dict[date, Decimal] = {}
    cursor = end
    for _ in range(8):
        try:
            data = await client.call(
                "bhavcopy",
                "get_index_history",
                {"indexName": BENCHMARK, "months": 12, "endDate": cursor.isoformat()},
            )
        except NSEMCPError as exc:
            if exc.reason in TRANSIENT:
                raise
            break  # Before the index history begins NSE answers with an error.
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                day = date.fromisoformat(str(row.get("date")))
            except ValueError:
                continue
            close = _dec(row.get("close"))
            if close is not None and close > 0:
                closes[day] = close
        try:
            following = date.fromisoformat(str(data.get("next_end_date")))
        except ValueError:
            break
        if following < start or following >= cursor or min(closes, default=cursor) > following + timedelta(days=7):
            break  # No older data was returned for the previous chunk.
        cursor = following
    return closes


# -- calculation -------------------------------------------------------------

def base_session(event: CompanyEvent, sessions: list[date]) -> tuple[date | None, str]:
    published = _local(event.published_at)
    if event.publication_precision == PublicationPrecision.DATE:
        candidates = [s for s in sessions if s < published.date()]
        timing = "DATE_ONLY"
    else:
        after_close = published.time() >= CLOSE
        candidates = [s for s in sessions if s < published.date() or (s == published.date() and after_close)]
        timing = (
            "DURING_OR_BEFORE_SESSION"
            if published.date() in sessions and not after_close
            else "AFTER_CLOSE_OR_HOLIDAY"
        )
    return (candidates[-1] if candidates else None), timing


def compute_reaction(
    event: CompanyEvent,
    bars: dict[date, Bar],
    index: dict[date, Decimal],
    basis: ShareBasis | None,
    today: date,
) -> dict:
    """Pure calculation; returns the column values for :class:`EventPriceReaction`."""
    sessions = sorted(bars)
    published = _local(event.published_at).date()
    result: dict = {"windows": {}, "adjustments": [], "base_date": None, "base_close": None,
                    "reaction_date": None, "volume_ratio": None, "timing": None, "reason": None}
    if published < today - timedelta(days=HISTORY_DAYS):
        return {**result, "status": "UNAVAILABLE", "reason": "OUTSIDE_NSE_HISTORY"}
    base, timing = base_session(event, sessions)
    result["timing"] = timing
    if base is None or (published - base).days > 10:
        return {**result, "status": "UNAVAILABLE", "reason": "NO_PRICE_JUST_BEFORE_EVENT"}
    position = sessions.index(base)
    after = sessions[position + 1 :]
    last_needed = after[min(len(after), max(WINDOWS)) - 1] if after else base
    if basis is None or not basis.covers(base, last_needed):
        return {**result, "status": "UNAVAILABLE", "reason": "SHARE_BASIS_UNCONFIRMED"}
    base_bar = bars[base]
    result.update(base_date=base, base_close=base_bar.close, reaction_date=after[0] if after else None)
    for n in WINDOWS:
        if len(after) < n:
            continue
        day = after[n - 1]
        factor = basis.factor_between(base, day)
        stock = bars[day].close / (base_bar.close * factor)
        window = {
            "sessions": n,
            "date": day.isoformat(),
            "close": str(bars[day].close),
            "return_pct": _pct(stock),
            "index_return_pct": None,
            "excess_return_pct": None,
        }
        if base in index and day in index:
            benchmark = index[day] / index[base]
            window["index_return_pct"] = _pct(benchmark)
            window["excess_return_pct"] = str(((stock - benchmark) * 100).quantize(CENT))
        result["windows"][str(n)] = window
    adjustments = basis.between(base, last_needed)
    result["adjustments"] = [
        {"ex_date": a.ex_date.isoformat(), "factor": str(a.factor.normalize()), "purpose": a.label}
        for a in adjustments
    ]
    prior = [bars[s].volume for s in sessions[max(0, position - 19) : position + 1] if bars[s].volume]
    if after and bars[after[0]].volume is not None and len(prior) >= 10:
        average = Decimal(sum(prior)) / len(prior)
        if average > 0:
            result["volume_ratio"] = (Decimal(bars[after[0]].volume) / average).quantize(Decimal("0.01"))
    if str(max(WINDOWS)) in result["windows"]:
        status = "COMPLETE"
    elif (today - base).days > PENDING_GRACE_DAYS:
        status, result["reason"] = "UNAVAILABLE", "TOO_FEW_SESSIONS_AFTER_EVENT"
    else:
        status = "PENDING"
    if result["windows"] and not any(w["excess_return_pct"] for w in result["windows"].values()):
        result["reason"] = result["reason"] or "NIFTY_HISTORY_UNAVAILABLE_FOR_DATES"
    return {**result, "status": status}


def _store(db: Session, event: CompanyEvent, values: dict) -> EventPriceReaction:
    row = db.scalar(select(EventPriceReaction).where(EventPriceReaction.event_id == event.id))
    if row is None:
        row = EventPriceReaction(event_id=event.id, company_id=event.company_id, attempts=0, benchmark=BENCHMARK)
        db.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    row.attempts = (row.attempts or 0) + 1
    row.computed_at = datetime.now(timezone.utc)
    return row


# -- orchestration -----------------------------------------------------------------

def _eligible(db: Session, company: Company | None, due_only: bool, limit: int) -> list[CompanyEvent]:
    now = datetime.now(timezone.utc)
    query = (
        select(CompanyEvent)
        .join(Company, Company.id == CompanyEvent.company_id)
        .outerjoin(EventPriceReaction, EventPriceReaction.event_id == CompanyEvent.id)
        .where(
            Company.exchange == "NSE",
            CompanyEvent.event_type.in_(material_types()),
            CompanyEvent.data_origin != DataOrigin.FIXTURE,
            CompanyEvent.published_at <= now,
        )
    )
    if company is not None:
        query = query.where(CompanyEvent.company_id == company.id)
    if due_only:
        query = query.where(
            or_(
                EventPriceReaction.id.is_(None),
                (EventPriceReaction.status == "PENDING")
                & (EventPriceReaction.computed_at < now - timedelta(hours=6)),
                (EventPriceReaction.status == "UNAVAILABLE")
                & EventPriceReaction.reason.in_(TRANSIENT | {"SHARE_BASIS_UNCONFIRMED"})
                & (EventPriceReaction.computed_at < now - timedelta(hours=20)),
            )
        )
    return list(db.scalars(query.order_by(CompanyEvent.published_at.desc()).limit(limit)))


async def _measure(db: Session, events: list[CompanyEvent], *, force_sync: bool = False) -> dict:
    today = datetime.now(IST).date()
    by_company: dict[int, list[CompanyEvent]] = defaultdict(list)
    for event in events:
        by_company[event.company_id].append(event)
    measurable = [e for e in events if _local(e.published_at).date() >= today - timedelta(days=HISTORY_DAYS)]
    index: dict[date, Decimal] = {}
    if measurable:
        first = min(_local(e.published_at).date() for e in measurable) - timedelta(days=45)
        last = min(today, max(_local(e.published_at).date() for e in measurable) + timedelta(days=60))
        try:
            index = await fetch_index_closes(first, last)
        except NSEMCPError as exc:
            for event in events:
                _store(db, event, {"status": "UNAVAILABLE", "reason": exc.reason})
            db.commit()
            return {"events": len(events), "stored": 0, "error": exc.reason}
    stored = 0
    for company_id, company_events in by_company.items():
        company = db.get(Company, company_id)
        if company is None:
            continue
        await sync_company(db, company, force=force_sync)
        basis = share_basis(db, company.id)
        dates = [_local(e.published_at).date() for e in company_events]
        start = max(min(dates), today - timedelta(days=HISTORY_DAYS)) - timedelta(days=45)
        end = min(today, max(dates) + timedelta(days=60))
        try:
            bars = await fetch_stock_bars(company.symbol.strip().upper(), start, end)
        except NSEMCPError as exc:
            for event in company_events:
                _store(db, event, {"status": "UNAVAILABLE", "reason": exc.reason})
            db.commit()
            continue
        for event in company_events:
            row = _store(db, event, compute_reaction(event, bars, index, basis, today))
            stored += 1
            db.flush()
            from app.services.holdings_alerts import queue_reaction_followup
            queue_reaction_followup(db, event, row)
        db.commit()
    return {"events": len(events), "stored": stored}


async def refresh_company(db: Session, company: Company, limit: int = 300) -> dict:
    events = _eligible(db, company, due_only=False, limit=limit)
    if not events:
        sync = await sync_company(db, company, force=True)
        return {"events": 0, "stored": 0, "corporate_actions_synced": bool(sync.complete)}
    return await _measure(db, events, force_sync=True)


async def run_due(session_factory) -> dict:
    """Scheduled: keep corporate actions current and measure due event reactions."""
    if not settings.nse_mcp_enabled:
        return {"skipped": "NSE_MCP_DISABLED"}
    with session_factory() as db:
        stale_before = datetime.now(timezone.utc) - timedelta(hours=20)
        companies = list(
            db.scalars(
                select(Company)
                .outerjoin(CorporateActionSync, CorporateActionSync.company_id == Company.id)
                .where(
                    Company.exchange == "NSE",
                    or_(
                        CorporateActionSync.company_id.is_(None),
                        CorporateActionSync.synced_at.is_(None),
                        CorporateActionSync.synced_at < stale_before,
                    ),
                )
                .order_by(Company.id)
                .limit(25)
            )
        )
        for company in companies:
            await sync_company(db, company)
        events = _eligible(db, None, due_only=True, limit=settings.price_reaction_batch_size)
        result = await _measure(db, events) if events else {"events": 0, "stored": 0}
        logger.info("price_reactions.run synced=%d events=%d", len(companies), result.get("events", 0))
        return {"corporate_actions_synced": len(companies), **result}


# -- read models -----------------------------------------------------------------

def reaction_read(row: EventPriceReaction, event: CompanyEvent | None = None) -> dict:
    data = {
        "event_id": row.event_id,
        "status": row.status,
        "reason": row.reason,
        "timing": row.timing,
        "base_date": row.base_date.isoformat() if row.base_date else None,
        "base_close": str(row.base_close) if row.base_close is not None else None,
        "reaction_date": row.reaction_date.isoformat() if row.reaction_date else None,
        "benchmark": row.benchmark,
        "windows": row.windows or {},
        "volume_ratio": str(row.volume_ratio) if row.volume_ratio is not None else None,
        "adjustments": row.adjustments or [],
        "computed_at": (row.computed_at if row.computed_at.tzinfo else row.computed_at.replace(tzinfo=timezone.utc)).isoformat(),
        "method": "Close-to-close from the last NSE close before the event was public; splits/bonuses removed "
        "with NSE factors; excess = stock return − Nifty 50 return over the same sessions. Observation, not cause.",
    }
    if event is not None:
        data["event"] = {
            "id": event.id,
            "title": event.title,
            "event_type": event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
            "published_at": _local(event.published_at).isoformat(),
        }
    return data


def company_reactions(db: Session, company: Company) -> dict:
    rows = db.execute(
        select(EventPriceReaction, CompanyEvent)
        .join(CompanyEvent, CompanyEvent.id == EventPriceReaction.event_id)
        .where(EventPriceReaction.company_id == company.id)
        .order_by(CompanyEvent.published_at.desc())
    ).all()
    items = [reaction_read(row, event) for row, event in rows]
    summary: dict[str, dict] = {}
    for item in items:
        if item["status"] != "COMPLETE":
            continue
        group = summary.setdefault(item["event"]["event_type"], {"count": 0, "windows": {}})
        group["count"] += 1
        for key, window in item["windows"].items():
            if window.get("excess_return_pct") is None:
                continue
            group["windows"].setdefault(key, []).append(Decimal(window["excess_return_pct"]))
    for group in summary.values():
        group["windows"] = {
            key: {
                "observations": len(values),
                "median_excess_return_pct": str(Decimal(median(values)).quantize(CENT)),
                "outperformed": sum(1 for v in values if v > 0),
            }
            for key, values in sorted(group["windows"].items(), key=lambda kv: int(kv[0]))
        }
    return {
        "symbol": company.symbol,
        "benchmark": BENCHMARK,
        "reactions": items,
        "summary_by_event_type": summary,
        "note": "Medians use completed reactions with a Nifty 50 comparison (NSE index history currently begins Oct 2024). "
        "Small samples describe the past and do not predict the next reaction.",
    }
