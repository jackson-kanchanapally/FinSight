"""Two daily gold briefs, major reported events and large price-move alerts."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from html import escape

from sqlalchemy import select, text

from app.config import settings
from app.models.gold import GoldHeadline, GoldSnapshot, GoldState
from app.models.telegram import TelegramDelivery
from app.schemas.gold import IndiaGoldQuote
from app.services.gold_sources import GoldFetch, GoldProvider, IST, major_event_key, utc
from app.services.gold_reasoning import build_analysis, explain_headline
from app.services.result_sources import digest
from app.services.telegram_client import configured

VERSION = "gold-v1"
SLOT_NAMES = ("morning", "evening")
SLOT_WINDOWS = (30, 60)
MAJOR_NEWS_POLICY = "gold-major-events-v1"


def now():
    return datetime.now(timezone.utc)


def clock_times():
    return [time.fromisoformat(value) for value in settings.gold_alert_times_ist.split(",")]


def active_slot(current):
    local = utc(current).astimezone(IST)
    for name, clock, minutes in zip(SLOT_NAMES, clock_times(), SLOT_WINDOWS):
        start = datetime.combine(local.date(), clock, IST)
        end = start + timedelta(minutes=minutes)
        if start <= local < end:
            return {"name": name, "date": local.date().isoformat(), "start": start, "expires": end}
    return None


def next_alert_at(current):
    local = utc(current).astimezone(IST)
    starts = [datetime.combine(local.date() + timedelta(days=day), clock, IST)
              for day in (0, 1) for clock in clock_times()]
    return min(value for value in starts if value > local)


def latest_snapshot(db):
    return db.scalar(select(GoldSnapshot).order_by(GoldSnapshot.id.desc()).limit(1))


def normalize_prices(prices):
    """Read old Hyderabad-only snapshots without relabelling them as India."""
    result = {"global": prices.get("global"), "india": None, "hyderabad": prices.get("hyderabad")}
    quote = prices.get("india")
    if quote and quote.get("city") == "Hyderabad":
        result["hyderabad"] = result["hyderabad"] or quote
    elif quote and quote.get("city") == "India":
        result["india"] = quote
    return result


def prices_view(prices, current):
    prices = normalize_prices(prices)
    result = {"global": None, "india": None, "hyderabad": None}
    if quote := prices.get("global"):
        age = utc(current) - utc(datetime.fromisoformat(quote["as_of"]))
        result["global"] = {**quote, "freshness": "FRESH" if timedelta(minutes=-5) <= age <= timedelta(hours=1) else "STALE"}
        if quote.get("usd_inr_rate") and quote.get("usd_inr_as_of"):
            fx_age = utc(current) - utc(datetime.fromisoformat(quote["usd_inr_as_of"]))
            result["global"]["usd_inr_freshness"] = "FRESH" if timedelta(minutes=-5) <= fx_age <= timedelta(hours=1) else "STALE"
    for region in ("india", "hyderabad"):
        if quote := prices.get(region):
            age = (utc(current).astimezone(IST).date() - date.fromisoformat(quote["rate_date"])).days
            result[region] = {**quote, "freshness": "CURRENT" if age == 0 else "PREVIOUS_DAY" if age == 1 else "STALE"}
    return result


def reportable(prices, current):
    view = prices_view(prices, current)
    return any(quote and quote["freshness"] != "STALE" for quote in view.values())


def headline_view(row):
    reading = explain_headline(row)
    return {"id": row.id, "title": row.title, "summary": row.summary, "source": row.source,
            "url": row.url, "published_at": row.published_at, "impact": reading["impact"],
            "impact_reason": reading["reason"], "important": row.important}


def recent_headlines(db, current, *, hours=24, limit=12):
    return list(db.scalars(select(GoldHeadline).where(
        GoldHeadline.published_at >= utc(current) - timedelta(hours=hours),
        GoldHeadline.published_at <= utc(current))
        .order_by(GoldHeadline.published_at.desc(), GoldHeadline.id.desc()).limit(limit)))


def analyze(prices, headlines, current):
    return build_analysis(prices_view(prices, current), headlines[:6])


def num(value, *, signed=False):
    number = Decimal(str(value))
    return f"{number:+,.2f}" if signed else f"{number:,.2f}"


def stamp(value):
    return utc(datetime.fromisoformat(value) if isinstance(value, str) else value).astimezone(IST).strftime("%d %b, %I:%M %p IST")


def brief(snapshot, current, kind, *, headlines=None, move=None):
    labels = {"morning": "MORNING GOLD BRIEF", "noon": "NOON GOLD UPDATE", "evening": "EVENING GOLD UPDATE",
              "news": "IMPORTANT GOLD NEWS", "major_news": "MAJOR GOLD EVENT", "move": "GOLD PRICE MOVE"}
    prices = prices_view(snapshot.prices, current)
    lines = [f"<b>ALMONDS · {labels[kind]}</b>", stamp(current), "", "<b>Prices</b>"]
    global_quote = prices["global"]
    if global_quote:
        stale = " · old quote" if global_quote["freshness"] == "STALE" else ""
        usd_change = (f" · {num(global_quote['change_pct'], signed=True)}% vs previous USD quote"
                      if global_quote.get("change_pct") is not None else "")
        lines.append(f"Global: <b>${num(global_quote['usd_per_oz'])}/troy oz</b>{usd_change}{stale}")
        if global_quote.get("usd_inr_rate") is not None:
            fx_change = global_quote.get("usd_inr_change_pct")
            direction = ("rupee weaker" if Decimal(fx_change) > 0 else "rupee stronger" if Decimal(fx_change) < 0
                         else "rupee little changed") if fx_change is not None else "no earlier rate"
            comparison = (f" · {num(fx_change, signed=Decimal(fx_change) != 0)}% vs previous rate"
                          if fx_change is not None else "")
            fx_stale = " · old rate" if global_quote.get("usd_inr_freshness") == "STALE" else ""
            lines.append(f"USD/INR: <b>$1 = ₹{Decimal(global_quote['usd_inr_rate']):.4f}</b>{comparison} · {direction}{fx_stale}")
            lines.append(f"Gold source updated {stamp(global_quote['as_of'])} · FX rate observed {stamp(global_quote['usd_inr_as_of'])}")
        else:
            lines.append(f"Source updated {stamp(global_quote['as_of'])}")
    else:
        lines.append("Global: unavailable")
    for region, label in (("india", "India"), ("hyderabad", "Hyderabad")):
        quote = prices[region]
        if quote:
            stale = " · OLD rate" if quote["freshness"] == "STALE" else ""
            change = f" · 24K {num(quote['change_24k_pct'], signed=True)}% vs previous day" if quote.get("change_24k_pct") is not None else ""
            lines += [f"{label}: 22K <b>₹{num(quote['inr_22k_per_g'])}</b> · 24K <b>₹{num(quote['inr_24k_per_g'])}</b> /g",
                      f"Rate date {quote['rate_date']}{stale}{change}"]
        else:
            lines.append(f"{label} 22K / 24K: unavailable")
    if move is not None:
        lines += ["", f"<b>Material move:</b> {num(move['pct'], signed=True)}% since {stamp(move['since'])}"]
    analysis = analyze(snapshot.prices, headlines, current) if headlines else snapshot.analysis
    mood = {"POSITIVE": "Positive", "NEGATIVE": "Negative", "MIXED": "Mixed", "UNCLEAR": "Unclear"}[analysis["bias"]]
    lines += ["", f"<b>News read: {mood}</b>"]
    reasons = analysis.get("drivers", [])
    why = reasons[0]["reason"] if reasons else analysis["summary"]
    if analysis["bias"] == "MIXED":
        why = "The reports contain conflicting moves or drivers. No single factor explains the price move clearly."
    lines.append("<b>Why:</b> " + escape(why))
    if analysis.get("fx_context"):
        lines.append("<b>Indian price read:</b> " + escape(analysis["fx_context"]))
    chosen = headlines if headlines is not None else analysis.get("drivers", [])[:2]
    for item in chosen[:2]:
        title = item.title if isinstance(item, GoldHeadline) else item["title"]
        url = item.url if isinstance(item, GoldHeadline) else item["url"]
        source = item.source if isinstance(item, GoldHeadline) else item["source"]
        if kind == "major_news" and isinstance(item, GoldHeadline):
            source += " · " + stamp(item.published_at)
        short = title if len(title) <= 145 else title[:142].rsplit(" ", 1)[0] + "…"
        lines.append(f'• <a href="{escape(url, quote=True)}">{escape(short)}</a> ({escape(source)})')
    if not chosen:
        lines.append(escape(analysis["summary"]))
    lines += ["", "<b>Next:</b> " + escape(analysis["what_next"])]
    if snapshot.errors:
        lines += ["", "Some sources could not refresh; dates above show the last available quotes."]
    lines += ["", '<a href="https://gold-api.com/">Global source</a> · '
              '<a href="https://groww.in/gold-rates">India &amp; Hyderabad rates</a>']
    return "\n".join(lines)


def queue_message(db, snapshot, current, kind, key, expires, *, headlines=None, move=None, major_keys=None):
    chat = settings.telegram_chat_id.strip()
    identity = digest(["gold", kind, key, chat])
    if db.scalar(select(TelegramDelivery.id).where(TelegramDelivery.idempotency_key == identity)):
        return False
    message = brief(snapshot, current, kind, headlines=headlines, move=move)
    db.add(TelegramDelivery(event_id=None, version=VERSION, idempotency_key=identity,
        notification={"kind": "GOLD", "brief_kind": kind, "snapshot_id": snapshot.id,
                      "scheduled_times": settings.gold_alert_times_ist if kind in SLOT_NAMES else None,
                      "major_event_policy": MAJOR_NEWS_POLICY if major_keys else None,
                      "major_event_keys": major_keys or [], "prepared_at": utc(current).isoformat(),
                      "headline_ids": [row.id for row in (headlines or [])],
                      "expires_at": utc(expires).isoformat()},
        chat_id=chat, status="PENDING", attempts=0, next_attempt_at=utc(current),
        message=message, message_parts=[message], message_ids=[], parse_mode="HTML"))
    return True


def major_keys_overlap(left, right):
    left_family, _, left_entities = left.partition(":")
    right_family, _, right_entities = right.partition(":")
    if left_family != right_family:
        return False
    return not left_entities or not right_entities or bool(set(left_entities.split(",")) & set(right_entities.split(",")))


def pending_major_events(db, state, current):
    if not settings.gold_major_event_alerts_enabled:
        return []
    cutoff = max(utc(state.started_at), utc(current) - timedelta(hours=6))
    if settings.gold_major_event_alerts_since:
        cutoff = max(cutoff, utc(settings.gold_major_event_alerts_since))
    previous_keys = []
    jobs = db.scalars(select(TelegramDelivery).where(TelegramDelivery.version == VERSION,
        TelegramDelivery.created_at >= utc(current) - timedelta(days=1), TelegramDelivery.status != "SUPERSEDED"))
    for job in jobs:
        note = job.notification or {}
        if note.get("major_event_policy") == MAJOR_NEWS_POLICY and note.get("prepared_at"):
            prepared = utc(datetime.fromisoformat(note["prepared_at"]))
            if utc(current) - timedelta(hours=6) <= prepared <= utc(current):
                previous_keys.extend(note.get("major_event_keys", []))
    rows = db.scalars(select(GoldHeadline).where(GoldHeadline.alerted_at.is_(None),
        GoldHeadline.published_at >= cutoff, GoldHeadline.published_at <= utc(current),
        GoldHeadline.first_seen_at >= cutoff).order_by(GoldHeadline.published_at).limit(100))
    selected = []
    for row in rows:
        key = major_event_key(row.title, row.source)
        if key and not any(major_keys_overlap(key, existing) for existing in previous_keys):
            selected.append((row, key))
            previous_keys.append(key)
            if len(selected) == 2:
                break
    return selected


def enqueue_due(db, state, snapshot, current):
    if not settings.gold_monitor_enabled or not settings.telegram_enabled or not configured() or snapshot is None:
        return 0
    queued = 0
    daily_queued = False
    major_events = pending_major_events(db, state, current)
    major_rows = [row for row, key in major_events]
    major_keys = [key for row, key in major_events]
    if (slot := active_slot(current)) and reportable(snapshot.prices, current):
        daily_queued = queue_message(db, snapshot, current, slot["name"], f"{slot['date']}:{slot['name']}", slot["expires"],
                                     headlines=major_rows or None, major_keys=major_keys)
        queued += int(daily_queued)
        if daily_queued:
            if major_rows:
                for row in major_rows:
                    row.alerted_at = utc(current)
            else:
                for item in snapshot.analysis.get("drivers", [])[:2]:
                    row = db.get(GoldHeadline, item["headline_id"])
                    if row and row.important:
                        row.alerted_at = utc(current)
            db.flush()
    quote = prices_view(snapshot.prices, current)["global"]
    if quote and quote["freshness"] == "FRESH":
        value = Decimal(quote["usd_per_oz"])
        if daily_queued or state.move_anchor is None or state.move_anchor_at is None or utc(current) - utc(state.move_anchor_at) > timedelta(hours=24):
            state.move_anchor, state.move_anchor_at = value, utc(current)
        else:
            change = (value / state.move_anchor - 1) * 100
            cooldown = state.last_move_alert_at is None or utc(current) - utc(state.last_move_alert_at) >= timedelta(minutes=settings.gold_move_cooldown_minutes)
            if abs(change) >= Decimal(str(settings.gold_price_move_pct)) and cooldown:
                move = {"pct": change, "since": state.move_anchor_at}
                if queue_message(db, snapshot, current, "move", str(snapshot.id), utc(current) + timedelta(minutes=45),
                                 move=move, headlines=major_rows or None, major_keys=major_keys):
                    queued += 1
                    state.move_anchor, state.move_anchor_at, state.last_move_alert_at = value, utc(current), utc(current)
                    for row in major_rows:
                        row.alerted_at = utc(current)
                    major_events = []
    if major_events and not daily_queued:
        expires = min(utc(current) + timedelta(hours=2), min(utc(row.published_at) for row in major_rows) + timedelta(hours=6))
        if queue_message(db, snapshot, current, "major_news", ":".join(row.identity for row in major_rows), expires,
                         headlines=major_rows, major_keys=major_keys):
            queued += 1
            for row in major_rows:
                row.alerted_at = utc(current)
    return queued


async def refresh(db, *, current=None, provider=None, notify=False):
    current = utc(current or now())
    if db.get_bind().dialect.name == "postgresql" and not db.scalar(text("SELECT pg_try_advisory_xact_lock(840027)")):
        return {"status": "BUSY", "queued": 0, "errors": []}
    state = db.get(GoldState, 1)
    if state is None:
        state = GoldState(id=1, started_at=current, errors=[])
        db.add(state)
    previous = latest_snapshot(db)
    if state.last_attempt_at and current - utc(state.last_attempt_at) < timedelta(minutes=1) and previous and "hyderabad" in previous.prices:
        queued = enqueue_due(db, state, previous, current) if notify else 0
        db.commit()
        return {"status": "CACHED", "queued": queued, "errors": state.errors}
    state.last_attempt_at = current
    previous_prices = normalize_prices(previous.prices) if previous else {"global": None, "india": None, "hyderabad": None}
    cached_india = IndiaGoldQuote.model_validate(previous_prices["india"]) if previous_prices["india"] else None
    cached_hyderabad = IndiaGoldQuote.model_validate(previous_prices["hyderabad"]) if previous_prices["hyderabad"] else None
    try:
        result = await (provider or GoldProvider()).fetch(current, cached_india=cached_india, cached_hyderabad=cached_hyderabad)
    except Exception:
        result = GoldFetch(errors=[{"source": "Gold monitor", "reason": "REFRESH_FAILED"}])
    state.errors = result.errors
    prices = previous_prices
    if result.global_quote:
        quote = result.global_quote.model_dump(mode="json")
        old = prices.get("global")
        if old and old["as_of"] != quote["as_of"]:
            quote["change_pct"] = str(((Decimal(quote["usd_per_oz"]) / Decimal(old["usd_per_oz"]) - 1) * 100).quantize(Decimal("0.01")))
            quote["change_since"] = old["as_of"]
        if result.usd_inr_quote and abs(utc(result.usd_inr_quote.as_of) - utc(result.global_quote.as_of)) <= timedelta(minutes=5):
            fx_quote = result.usd_inr_quote
            implied_rate = fx_quote.quoted_gold_inr_per_oz / result.global_quote.usd_per_oz
            if abs(implied_rate / fx_quote.usd_inr_rate - 1) <= Decimal("0.02"):
                quote["usd_inr_rate"] = str(fx_quote.usd_inr_rate)
                quote["usd_inr_as_of"] = fx_quote.as_of.isoformat()
                if old and old.get("usd_inr_rate") and old.get("usd_inr_as_of") != quote["usd_inr_as_of"]:
                    quote["usd_inr_change_pct"] = str(((fx_quote.usd_inr_rate / Decimal(old["usd_inr_rate"]) - 1) * 100).quantize(Decimal("0.01")))
        prices["global"] = quote
    if result.india_quote:
        prices["india"] = result.india_quote.model_dump(mode="json")
    if result.hyderabad_quote:
        prices["hyderabad"] = result.hyderabad_quote.model_dump(mode="json")
    seen = set()
    for item in result.headlines:
        if item.identity not in seen and db.scalar(select(GoldHeadline.id).where(GoldHeadline.identity == item.identity)) is None:
            values = item.model_dump()
            values["published_at"] = utc(item.published_at)
            db.add(GoldHeadline(**values, first_seen_at=current))
        seen.add(item.identity)
    db.flush()
    if not (result.global_quote or result.india_quote or result.hyderabad_quote or result.news_sources_ok):
        db.commit()
        return {"status": "FAILED", "queued": 0, "errors": result.errors}
    analysis = analyze(prices, recent_headlines(db, current), current)
    snapshot = GoldSnapshot(fetched_at=current, prices=prices, analysis=analysis, errors=result.errors)
    db.add(snapshot)
    db.flush()
    if result.global_quote and result.india_quote and result.hyderabad_quote:
        state.last_success_at = current
    # Establish the move anchor even when Telegram is temporarily paused.
    fresh_global = prices_view(prices, current)["global"]
    if fresh_global and fresh_global["freshness"] == "FRESH" and state.move_anchor is None:
        state.move_anchor, state.move_anchor_at = Decimal(fresh_global["usd_per_oz"]), current
    queued = enqueue_due(db, state, snapshot, current) if notify else 0
    db.commit()
    return {"status": "PARTIAL" if result.errors else "OK", "queued": queued, "errors": result.errors}


async def run_due(session_factory, *, current=None, provider=None):
    if not settings.gold_monitor_enabled:
        return
    current = utc(current or now())
    with session_factory() as db:
        state = db.get(GoldState, 1)
        snapshot = latest_snapshot(db)
        needs_locations = snapshot is not None and "hyderabad" not in snapshot.prices
        due = active_slot(current)
        slot_missing = bool(due) and settings.telegram_enabled and configured() and db.scalar(select(TelegramDelivery.id).where(
            TelegramDelivery.idempotency_key == digest(["gold", due["name"], f"{due['date']}:{due['name']}", settings.telegram_chat_id.strip()]))) is None
        if state and state.last_attempt_at and current - utc(state.last_attempt_at) < timedelta(minutes=settings.gold_poll_minutes) and not slot_missing and not needs_locations:
            return
        await refresh(db, current=current, provider=provider, notify=True)


def validate_delivery(db, job, current):
    if not settings.gold_monitor_enabled:
        return "Gold monitoring is paused"
    note = job.notification
    if utc(datetime.fromisoformat(note["expires_at"])) <= utc(current):
        return "Gold brief expired"
    if note["brief_kind"] == "news":
        return "Separate gold news alerts are disabled"
    if note["brief_kind"] == "major_news":
        if not settings.gold_major_event_alerts_enabled or note.get("major_event_policy") != MAJOR_NEWS_POLICY:
            return "Gold major-event alert policy is inactive"
        rows = [db.get(GoldHeadline, identity) for identity in note.get("headline_ids", [])]
        cutoff = utc(current) - timedelta(hours=6)
        if settings.gold_major_event_alerts_since:
            cutoff = max(cutoff, utc(settings.gold_major_event_alerts_since))
        if not rows or any(row is None or not cutoff <= utc(row.published_at) <= utc(current) or
                           not major_event_key(row.title, row.source) for row in rows):
            return "Gold event no longer qualifies as fresh major news"
    if note["brief_kind"] in {"morning", "noon", "evening"}:
        slot = active_slot(current)
        if (note["brief_kind"] not in SLOT_NAMES or note.get("scheduled_times") != settings.gold_alert_times_ist or
                slot is None or slot["name"] != note["brief_kind"]):
            return "Gold alert belongs to an inactive schedule"
    snapshot = db.get(GoldSnapshot, note["snapshot_id"])
    if snapshot is None:
        return "Gold snapshot is no longer available"
    if note["brief_kind"] in SLOT_NAMES and not reportable(snapshot.prices, current):
        return "Gold quotes became stale before delivery"
    if note["brief_kind"] == "move":
        quote = prices_view(snapshot.prices, current)["global"]
        if not quote or quote["freshness"] != "FRESH":
            return "Gold move quote became stale before delivery"
    return None


def dashboard(db, *, current=None):
    current = utc(current or now())
    state, snapshot = db.get(GoldState, 1), latest_snapshot(db)
    deliveries = list(db.scalars(select(TelegramDelivery).where(TelegramDelivery.version == VERSION)
                                .order_by(TelegramDelivery.id.desc()).limit(12)))
    history = {}
    rows = list(db.scalars(select(GoldSnapshot).where(GoldSnapshot.fetched_at >= current - timedelta(days=7))
                          .order_by(GoldSnapshot.fetched_at.desc()).limit(700)))
    for row in rows:
        day = utc(row.fetched_at).astimezone(IST).date().isoformat()
        if day not in history:
            history[day] = {"recorded_on": day, "fetched_at": row.fetched_at, "prices": prices_view(row.prices, current)}
    return {"prices": prices_view(snapshot.prices, current) if snapshot else {"global": None, "india": None, "hyderabad": None},
            "analysis": analyze(snapshot.prices, recent_headlines(db, current), current) if snapshot else None,
            "headlines": [headline_view(row) for row in recent_headlines(db, current)],
            "history": list(reversed(list(history.values()))),
            "status": {"enabled": settings.gold_monitor_enabled,
                "major_event_alerts_enabled": settings.gold_major_event_alerts_enabled,
                "telegram_connected": settings.telegram_enabled and configured(),
                "last_attempt_at": state.last_attempt_at if state else None,
                "last_success_at": state.last_success_at if state else None,
                "last_refresh_at": snapshot.fetched_at if snapshot else None,
                "errors": state.errors if state else [],
                "schedule": [{"name": name, "time": clock.strftime("%H:%M")} for name, clock in zip(SLOT_NAMES, clock_times())],
                "next_alert_at": next_alert_at(current), "timezone": "Asia/Kolkata",
                "poll_minutes": settings.gold_poll_minutes, "move_threshold_pct": settings.gold_price_move_pct,
                "deliveries": [{"id": row.id, "kind": (row.notification or {}).get("brief_kind"),
                                "status": row.status, "sent_at": row.sent_at, "last_error": row.last_error}
                               for row in deliveries]}}
