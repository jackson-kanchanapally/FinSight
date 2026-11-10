"""Telegram content about the positions you hold, built on NSE MCP data.

Three deliberately quiet additions to the existing alert stream:

1. **Closing-brief section** (``TELEGRAM_HOLDINGS_IN_CLOSE_BRIEF``): portfolio
   value and day change, best/worst holding, holdings at a 52-week extreme, and
   volume spikes, with whether the company filed anything that day. It adds no
   messages; it is part of the 4 PM brief.
2. **Reaction follow-up** (``TELEGRAM_REACTION_FOLLOWUPS``): a reply under the
   original results alert once N sessions have passed, for companies you hold.
   At most one per results filing; never sent for historical backfill.
3. **Big-move alert** (``TELEGRAM_BIG_MOVE_ALERTS``, off by default): when a
   holding moves at least ``TELEGRAM_BIG_MOVE_PCT`` points against the Nifty 50
   during the session. At most once per holding, direction and day.

Every message states what the market did, never why.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.market.nse_mcp import NSEMCPError, get_nse_mcp
from app.models.company import Company
from app.models.event import CompanyEvent, EventType
from app.models.market_events import EventPriceReaction
from app.models.telegram import TelegramDelivery
from app.services.nse_insights import market_session, nse_date, quotes_for
from app.services.portfolio import calculate_position, get_positions
from app.services.result_sources import digest
from app.services.telegram_client import configured

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
VERSION = "holdings-v1"
NEAR_EXTREME = Decimal("0.005")  # within 0.5% of the 52-week high/low
CENT = Decimal("0.01")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _rupees(value: Decimal) -> str:
    return f"₹{value:,.0f}" if abs(value) >= 1000 else f"₹{value:,.2f}"


def _signed_rupees(value: Decimal) -> str:
    return ("+" if value > 0 else "−" if value < 0 else "") + _rupees(abs(value))


def _signed(value: Decimal | str | float, suffix: str = "%") -> str:
    number = Decimal(str(value))
    return f"{'+' if number > 0 else '−' if number < 0 else ''}{abs(number):.2f}{suffix}"


def _held_nse_positions(db: Session):
    return [
        p for p in get_positions(db)
        if p.quantity > 0 and p.company.exchange.strip().upper() == "NSE"
    ]


def _filings_today(db: Session, company_ids: list[int], today: date) -> dict[int, list[CompanyEvent]]:
    start = datetime.combine(today, time(0), IST)
    rows = db.scalars(
        select(CompanyEvent)
        .where(CompanyEvent.company_id.in_(company_ids), CompanyEvent.published_at >= start)
        .order_by(CompanyEvent.published_at)
    )
    result: dict[int, list[CompanyEvent]] = {}
    for row in rows:
        result.setdefault(row.company_id, []).append(row)
    return result


async def _average_volumes(symbols: list[str]) -> dict[str, Decimal]:
    client = get_nse_mcp()

    async def one(symbol):
        try:
            data = await client.call("bhavcopy", "get_volume_analysis", {"symbol": symbol, "days": 30})
        except NSEMCPError:
            return symbol, None
        value = data.get("avg_volume") if isinstance(data, dict) else None
        return symbol, Decimal(str(value)) if isinstance(value, (int, float)) and value > 0 else None

    return {s: v for s, v in await asyncio.gather(*(one(s) for s in symbols)) if v}


# -- 1. closing brief section -------------------------------------------------------

async def holdings_block(db: Session, current: datetime) -> dict | None:
    """Frozen into the closing brief's evidence; ``None`` when nothing is held."""
    positions = _held_nse_positions(db)
    if not positions:
        return None
    today = _utc(current).astimezone(IST).date()
    symbols = [p.company.symbol.upper() for p in positions]
    quotes, errors = await quotes_for(symbols)
    # Only today's quotes describe today's session.
    quotes = {s: q for s, q in quotes.items() if nse_date(q["as_of"]) == today}
    volumes = await _average_volumes([s for s in symbols if s in quotes])
    filings = _filings_today(db, [p.company.id for p in positions], today)

    rows, flags = [], []
    value = previous = Decimal(0)
    for position in positions:
        symbol = position.company.symbol.upper()
        quote = quotes.get(symbol)
        if not quote:
            continue
        price = quote["price"]
        prev = quote["previous_close"] or price
        value += position.quantity * price
        previous += position.quantity * prev
        change = quote["change_pct"] if quote["change_pct"] is not None else (price / prev - 1) * 100
        rows.append({"symbol": symbol, "change_pct": str(Decimal(change).quantize(CENT))})
        high, low = quote["week52_high"], quote["week52_low"]
        if high and price >= high * (1 - NEAR_EXTREME):
            flags.append({"symbol": symbol, "type": "52W_HIGH", "level": str(high)})
        elif low and price <= low * (1 + NEAR_EXTREME):
            flags.append({"symbol": symbol, "type": "52W_LOW", "level": str(low)})
        average = volumes.get(symbol)
        volume = quote.get("volume")
        if average and isinstance(volume, (int, float)) and volume > 0:
            ratio = Decimal(volume) / average
            if ratio >= Decimal(str(settings.telegram_volume_spike_ratio)):
                filed = filings.get(position.company.id, [])
                flags.append({
                    "symbol": symbol, "type": "VOLUME_SPIKE", "ratio": str(ratio.quantize(Decimal("0.1"))),
                    "filing": filed[-1].title[:90] if filed else None,
                })
    if not rows:
        return {"as_of": today.isoformat(), "positions": len(positions), "priced": 0,
                "unpriced": symbols, "errors": [e["source"] for e in errors]}
    rows.sort(key=lambda r: Decimal(r["change_pct"]))
    day = value - previous
    return {
        "as_of": today.isoformat(),
        "positions": len(positions),
        "priced": len(rows),
        "value": str(value.quantize(CENT)),
        "day_change_value": str(day.quantize(CENT)),
        "day_change_pct": str((day / previous * 100).quantize(CENT)) if previous else None,
        "best": rows[-1] if len(rows) > 1 else None,
        "worst": rows[0] if len(rows) > 1 else None,
        "flags": flags[:6],
        "unpriced": [s for s in symbols if s not in quotes],
        "errors": [e["source"] for e in errors][:5],
    }


def format_holdings(block: dict | None, *, with_flags: bool = True) -> list[str]:
    if not block:
        return []
    lines = ["", "<b>Your holdings · NSE close</b>"]
    if not block.get("priced"):
        lines.append("Prices for today's session were unavailable from NSE.")
        return lines
    pct = f" ({_signed(block['day_change_pct'])})" if block.get("day_change_pct") else ""
    lines.append(
        f"Value {_rupees(Decimal(block['value']))} · today {_signed_rupees(Decimal(block['day_change_value']))}{pct}"
        + (f" · {block['priced']}/{block['positions']} priced" if block["priced"] < block["positions"] else "")
    )
    if block.get("best") and block.get("worst"):
        lines.append(
            f"Best {escape(block['best']['symbol'])} {_signed(block['best']['change_pct'])} · "
            f"Worst {escape(block['worst']['symbol'])} {_signed(block['worst']['change_pct'])}"
        )
    if with_flags:
        for flag in block.get("flags", []):
            symbol = escape(flag["symbol"])
            if flag["type"] == "52W_HIGH":
                lines.append(f"• {symbol} at its 52-week high ({_rupees(Decimal(flag['level']))})")
            elif flag["type"] == "52W_LOW":
                lines.append(f"• {symbol} at its 52-week low ({_rupees(Decimal(flag['level']))})")
            elif flag["type"] == "VOLUME_SPIKE":
                note = (f"filing today: {escape(flag['filing'])}" if flag.get("filing")
                        else "no company filing on record today")
                lines.append(f"• {symbol} volume {flag['ratio']}× its 30-session average · {note}")
    return lines


# -- 2. reaction follow-up -----------------------------------------------------------

def _daytime(current: datetime) -> datetime:
    """Hold follow-ups queued overnight until 08:30 IST."""
    local = current.astimezone(IST)
    if time(8, 0) <= local.time() <= time(21, 0):
        return current
    day = local.date() + (timedelta(days=1) if local.time() > time(21, 0) else timedelta(0))
    return datetime.combine(day, time(8, 30), IST).astimezone(timezone.utc)


def queue_reaction_followup(db: Session, event: CompanyEvent, reaction: EventPriceReaction) -> bool:
    if not (settings.telegram_enabled and settings.telegram_reaction_followups and configured()):
        return False
    if event.event_type != EventType.FINANCIAL_RESULTS:
        return False
    sessions = str(settings.telegram_reaction_followup_sessions)
    window = (reaction.windows or {}).get(sessions)
    if not window:
        return False
    current = _now()
    today = current.astimezone(IST).date()
    if date.fromisoformat(window["date"]) < today - timedelta(days=3):
        return False  # Historical backfill never reaches the chat.
    company = db.get(Company, event.company_id)
    if company is None or calculate_position(db, company).quantity <= 0:
        return False
    original = db.scalar(
        select(TelegramDelivery)
        .where(TelegramDelivery.event_id == event.id, TelegramDelivery.status == "SENT")
        .order_by(TelegramDelivery.id)
    )
    if original is None or not original.message_ids:
        return False  # Only ever a reply to an alert you received.
    key = digest(["reaction-followup", event.id, sessions, original.chat_id])
    if db.scalar(select(TelegramDelivery.id).where(TelegramDelivery.idempotency_key == key)):
        return False
    message = format_followup(company, event, reaction, window)
    send_at = _daytime(current)
    db.add(TelegramDelivery(
        event_id=None, version=VERSION, idempotency_key=key, chat_id=original.chat_id,
        status="PENDING", attempts=0, next_attempt_at=send_at,
        notification={"kind": "REACTION_FOLLOWUP", "event_id": event.id, "reaction_id": reaction.id,
                      "sessions": int(sessions), "reply_to_message_id": original.message_ids[0],
                      "expires_at": (send_at + timedelta(days=2)).isoformat()},
        message=message, message_parts=[message], message_ids=[], parse_mode="HTML"))
    db.flush()  # Make the idempotency key visible to the next check in this session.
    return True


def format_followup(company: Company, event: CompanyEvent, reaction: EventPriceReaction, window: dict) -> str:
    symbol = escape(company.symbol)
    sessions = window["sessions"]
    lines = [f"<b>ALMONDS · {symbol} since results</b>",
             f"{sessions} session{'s' if sessions != 1 else ''} after “{escape(event.title[:80])}”"
             f" (from the {reaction.base_date.strftime('%d %b')} close to {date.fromisoformat(window['date']).strftime('%d %b')}):"]
    if window.get("excess_return_pct") is not None:
        lines.append(f"{symbol} {_signed(window['return_pct'])} · {escape(reaction.benchmark)} "
                     f"{_signed(window['index_return_pct'])} → <b>{_signed(window['excess_return_pct'], ' pts')}</b> vs the market")
    else:
        lines.append(f"{symbol} <b>{_signed(window['return_pct'])}</b> (no Nifty 50 comparison for these dates)")
    if reaction.volume_ratio is not None:
        lines.append(f"First-session volume {Decimal(reaction.volume_ratio):.1f}× its prior 20-session average.")
    if reaction.adjustments:
        lines.append("Adjusted for " + ", ".join(escape(a["purpose"]) for a in reaction.adjustments) + ".")
    lines.append("<i>What the market did, not why.</i>")
    return "\n".join(lines)


# -- 3. big-move alert ------------------------------------------------------------------

async def run_big_moves(session_factory) -> dict:
    if not (settings.telegram_big_move_alerts and settings.telegram_enabled and configured()
            and settings.nse_mcp_enabled):
        return {"status": "OFF"}
    session = market_session()
    if session["state"] != "OPEN":
        return {"status": "MARKET_CLOSED"}
    today = _now().astimezone(IST).date()
    try:
        index = await get_nse_mcp().call("live", "cm_get_index_quote", {"indexName": "NIFTY 50"}, ttl=60)
    except NSEMCPError as exc:
        return {"status": "INDEX_UNAVAILABLE", "reason": exc.reason}
    nifty = index.get("index") if isinstance(index, dict) else None
    if not isinstance(nifty, dict) or nse_date(index.get("nseTimestamp")) != today:
        return {"status": "STALE_SESSION"}  # Holiday or delayed feed.
    nifty_pct = Decimal(str(nifty.get("percChange")))
    threshold = Decimal(str(settings.telegram_big_move_pct))
    queued = 0
    with session_factory() as db:
        positions = _held_nse_positions(db)
        symbols = [p.company.symbol.upper() for p in positions]
        if not symbols:
            return {"status": "NO_HOLDINGS"}
        quotes, _ = await quotes_for(symbols)
        movers = []
        for position in positions:
            symbol = position.company.symbol.upper()
            quote = quotes.get(symbol)
            if not quote or quote["source"] != "LIVE" or nse_date(quote["as_of"]) != today or quote["change_pct"] is None:
                continue
            excess = quote["change_pct"] - nifty_pct
            if abs(excess) >= threshold:
                movers.append((position, quote, excess))
        if not movers:
            return {"status": "NO_MOVES", "queued": 0}
        volumes = await _average_volumes([p.company.symbol.upper() for p, _, _ in movers])
        filings = _filings_today(db, [p.company.id for p, _, _ in movers], today)
        chat = settings.telegram_chat_id.strip()
        expires = datetime.combine(today, time(15, 45), IST).astimezone(timezone.utc)
        for position, quote, excess in movers:
            direction = "UP" if excess > 0 else "DOWN"
            key = digest(["big-move", position.company.symbol.upper(), today.isoformat(), direction, chat])
            if db.scalar(select(TelegramDelivery.id).where(TelegramDelivery.idempotency_key == key)):
                continue
            volume = quote.get("volume")
            average = volumes.get(position.company.symbol.upper())
            ratio = (Decimal(volume) / average) if average and isinstance(volume, (int, float)) else None
            message = format_big_move(position, quote, nifty_pct, excess, ratio,
                                      filings.get(position.company.id, []), threshold)
            db.add(TelegramDelivery(
                event_id=None, version=VERSION, idempotency_key=key, chat_id=chat, status="PENDING",
                attempts=0, next_attempt_at=_now(),
                notification={"kind": "HOLDING_MOVE", "symbol": position.company.symbol.upper(),
                              "direction": direction, "excess_pct": str(excess.quantize(CENT)),
                              "expires_at": expires.isoformat()},
                message=message, message_parts=[message], message_ids=[], parse_mode="HTML"))
            queued += 1
        db.commit()
    return {"status": "CHECKED", "queued": queued}


def format_big_move(position, quote: dict, nifty_pct: Decimal, excess: Decimal, ratio: Decimal | None,
                    filings: list[CompanyEvent], threshold: Decimal) -> str:
    symbol = escape(position.company.symbol.upper())
    price = quote["price"]
    stamp = str(quote.get("as_of") or "")[11:16]
    lines = [f"<b>ALMONDS · {symbol} {_signed(quote['change_pct'])} today</b>",
             f"Nifty 50 {_signed(nifty_pct)} → <b>{_signed(excess, ' pts')}</b> vs the market",
             f"{_rupees(price)}{f' at {stamp} IST' if stamp else ''}"
             + (f" · volume {ratio:.1f}× its 30-session average" if ratio else "")]
    if quote["previous_close"]:
        day = position.quantity * (price - quote["previous_close"])
        lines.append(f"Your {position.quantity.normalize():f} shares: {_rupees(position.quantity * price)} "
                     f"({_signed_rupees(day)} today)")
    lines.append(f"Filing today: {escape(filings[-1].title[:100])}" if filings
                 else "No company filing on record today.")
    lines.append(f"<i>What the market did, not why. Alert threshold ±{threshold:.0f} pts vs the Nifty 50.</i>")
    return "\n".join(lines)


# -- delivery-time checks ----------------------------------------------------------------

def validate_delivery(db: Session, job: TelegramDelivery, current: datetime) -> str | None:
    note = job.notification or {}
    kind = note.get("kind")
    if kind == "REACTION_FOLLOWUP" and not settings.telegram_reaction_followups:
        return "Reaction follow-ups are turned off"
    if kind == "HOLDING_MOVE" and not settings.telegram_big_move_alerts:
        return "Big-move alerts are turned off"
    expires = note.get("expires_at")
    if expires and _utc(current) >= _utc(datetime.fromisoformat(expires)):
        return "Alert delivery window expired"
    return None


def alert_settings() -> dict:
    return {
        "holdings_in_close_brief": settings.telegram_holdings_in_close_brief and settings.market_briefs_enabled,
        "reaction_followups": settings.telegram_reaction_followups,
        "reaction_followup_sessions": settings.telegram_reaction_followup_sessions,
        "big_move_alerts": settings.telegram_big_move_alerts,
        "big_move_pct": settings.telegram_big_move_pct,
        "nse_mcp_enabled": settings.nse_mcp_enabled,
    }
