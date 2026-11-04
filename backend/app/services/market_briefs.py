"""Four durable market briefs per regular NSE trading session."""
import asyncio
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from html import escape

from sqlalchemy import select, text

from app.config import settings
from app.models.market_brief import MarketBrief, MarketBriefState
from app.models.telegram import TelegramDelivery
from app.services.gold_sources import IST, utc
from app.services.market_brief_analysis import analyze, assess, baseline_levels, mark_quotes
from app.services.market_brief_sources import FEEDS, MarketBriefProvider, NSE_PAGE, SENSEX_URL
from app.services.result_sources import digest
from app.services.telegram_client import configured
from app.services.telegram_formatter import _bounded_html, units

SLOTS = ("morning", "midmorning", "noon", "close")
WINDOWS = (25, 30, 30, 60)
VERSION = "market-brief-v1"


def now():
    return datetime.now(timezone.utc)


def schedule():
    return list(zip(SLOTS, (time.fromisoformat(value) for value in settings.market_brief_times_ist.split(",")), WINDOWS))


def active_slot(current):
    local = utc(current).astimezone(IST)
    for slot, clock, minutes in schedule():
        start = datetime.combine(local.date(), clock, IST)
        expires = start + timedelta(minutes=minutes)
        if slot == "morning":
            expires = min(expires, datetime.combine(local.date(), time(9, 10), IST))
        if start <= local < expires:
            return {"slot": slot, "session_date": local.date(), "start": start, "expires": expires}
    return None


def is_session(day, calendar):
    return calendar.get("year") == day.year and day.weekday() < 5 and day.isoformat() not in calendar.get("holidays", [])


def history_context(db, day):
    morning = db.scalar(select(MarketBrief).where(MarketBrief.session_date == day, MarketBrief.slot == "morning"))
    previous = db.scalar(select(MarketBrief).where(MarketBrief.session_date < day,
        MarketBrief.session_date >= day - timedelta(days=20), MarketBrief.slot == "close")
        .order_by(MarketBrief.session_date.desc()).limit(1))
    return morning, previous


def morning_delivered(db, morning):
    if morning is None:
        return False
    key = digest(["market-brief", morning.session_date.isoformat(), "morning", settings.telegram_chat_id.strip()])
    job = db.scalar(select(TelegramDelivery).where(TelegramDelivery.idempotency_key == key,
                                                  TelegramDelivery.status == "SENT"))
    opening = datetime.combine(morning.session_date, time(9, 15), IST)
    return bool(job and (job.notification or {}).get("brief_id") == morning.id and job.sent_at and
                utc(job.sent_at) < utc(opening))


def stamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return utc(value).astimezone(IST).strftime("%d %b %H:%M IST")


def format_message(report, *, preview=False):
    evidence, analysis, assessment = report.evidence, report.analysis, report.assessment
    title = {"morning": "MORNING MARKET OUTLOOK", "midmorning": "UPDATED MORNING BRIEF",
             "noon": "NOON MARKET CHECK", "close": "MARKET CLOSING REVIEW"}[report.slot]
    lines = [f"<b>ALMONDS · {title}{' · PREVIEW' if preview else ''}</b>", stamp(report.created_at)]
    lines += ["", "<b>Benchmarks · vs previous close</b>"]
    for name in ("NIFTY 50", "SENSEX", "NIFTY BANK", "INDIA VIX"):
        quote = evidence["indices"].get(name)
        if quote:
            old = " · stale/unconfirmed" if quote["status"] == "STALE" else ""
            lines.append(f"{name}: <b>{Decimal(quote['last']):,.2f}</b> ({Decimal(quote['change_pct']):+.2f}%) · {stamp(quote['as_of'])}{old}")
        else:
            lines.append(f"{name}: unavailable")
    breadth = evidence.get("breadth")
    if breadth and breadth.get("usable"):
        lines.append(f"Nifty 500 breadth: {breadth['advances']} up / {breadth['declines']} down")
    sectors = sorted((q for name, q in evidence["indices"].items() if name in {
        "NIFTY IT", "NIFTY AUTO", "NIFTY FMCG", "NIFTY PHARMA", "NIFTY METAL", "NIFTY REALTY", "NIFTY ENERGY"}
        and q["status"] != "STALE"), key=lambda q: Decimal(q["change_pct"]))
    if len(sectors) > 1:
        lines.append("Sector spread: " + " / ".join(f"{q['name']} {Decimal(q['change_pct']):+.2f}%" for q in (sectors[-1], sectors[0])))
    threshold = assessment.get("threshold_pct", settings.market_brief_direction_threshold_pct)
    if report.slot == "morning":
        lines += ["", f"<b>Today's expectation: {analysis['view']} · {analysis['confidence'].lower()} confidence</b>",
                  f"Direction rule: both benchmarks beyond ±{Decimal(str(threshold)):.2f}% at close; RANGE stays inside."]
        if report.previous_close_id:
            lines.append("The previous available closing review was reconsidered with the overnight news.")
    else:
        if report.slot == "midmorning":
            lines += ["", f"<b>Updated morning view: {analysis['view']} · {analysis['confidence'].lower()} confidence</b>",
                      "Morning news and outlook for the rest of today; carried forward into the noon update."]
        label = (f"Morning expectation · provisional {report.slot} check" if report.slot in {"midmorning", "noon"}
                 else "Morning expectation · closing check")
        if report.slot == "noon":
            label = "Pre-open expectation · provisional noon check"
        lines += ["", f"<b>{label}: {assessment['status'].replace('_', ' ').lower()}</b>"]
        if assessment.get("expected"):
            lines.append(f"Saved view: {assessment['expected']}; observed: {assessment.get('observed', 'not assessable')}")
        lines.append(_bounded_html(assessment["reason"], 230))
        if analysis.get("review_of_morning"):
            reasoning_label = "Updated morning brief review" if evidence.get("morning_update") else "Morning reasoning check"
            lines.append(f"<b>{reasoning_label}:</b> " + _bounded_html(analysis["review_of_morning"]["text"], 230))
        if report.slot == "noon":
            update = evidence.get("morning_update")
            lines.append(f"Morning news context: updated brief from {stamp(update['created_at'])}." if update else
                         "Updated morning brief unavailable; using available morning context and fresh news.")
    lines += ["", _bounded_html(analysis["summary"]["text"], 350)]
    if analysis["status"] != "VERIFIED_AI":
        lines.append("<i>AI reasoning unavailable or insufficient verified coverage; no directional forecast.</i>")
    for driver in analysis["drivers"][:3]:
        label = "Reported driver" if driver["basis"] == "REPORTED" else "Possible factor"
        lines.append(f"• <b>{label}:</b> " + _bounded_html(driver["text"], 300))
    next_label = "Rest of today / next session" if report.slot in {"midmorning", "noon"} else "Next session"
    lines += ["", f"<b>{next_label}:</b> " + _bounded_html(analysis["next_session"]["text"], 280)]
    if report.slot == "close":
        lines.append(f"<b>Next-session bias: {analysis['view']} · {analysis['confidence'].lower()} confidence</b>")
        lines.append("<b>Further ahead:</b> " + _bounded_html(analysis["longer_term"]["text"], 260))
    lines.append("<b>What could change the view:</b> " + _bounded_html(analysis["invalidation"]["text"], 200))
    lines += ["", f"News coverage: {len(evidence['news_sources_ok'])}/{len(FEEDS)} feeds; {len(evidence['news'])} material headlines reviewed."]
    if evidence["errors"]:
        names = ", ".join(error["source"] for error in evidence["errors"])
        lines.append("Source gaps: " + _bounded_html(names, 150))
    if not analysis["drivers"] and evidence["news"]:
        lines.append("Recent headline: " + _bounded_html(evidence["news"][0]["title"], 150))
    # All cited evidence remains in the report. Fit complete source links, never sliced HTML.
    body = "\n".join(lines)
    sources = [("NSE indices", NSE_PAGE), ("Sensex · Moneycontrol", SENSEX_URL)]
    cited = set()
    for claim in [analysis["summary"], *analysis["drivers"], analysis["next_session"], analysis["longer_term"], analysis["invalidation"]]:
        cited.update(claim["evidence_ids"])
    if analysis.get("review_of_morning"):
        cited.update(analysis["review_of_morning"]["evidence_ids"])
    news_sources = [row for row in evidence["news"] if row["id"] in cited] or evidence["news"][:2]
    sources += [(row["source"] + " " + datetime.fromisoformat(row["published_at"]).strftime("%d %b"), row["url"]) for row in news_sources[:4]]
    links, seen = [], set()
    for label, url in sources:
        if url in seen:
            continue
        seen.add(url)
        link = f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'
        if units(body + "\n\n" + " · ".join(links + [link])) <= 3800:
            links.append(link)
    message = body + ("\n\n" + " · ".join(links) if links else "")
    if units(message) > 3800:
        raise ValueError("MARKET_BRIEF_TOO_LONG")
    return message


async def collect_review(db, slot, current, calendar, *, provider=None, ai_provider=None):
    provider = provider or MarketBriefProvider()
    morning, previous = history_context(db, current.astimezone(IST).date())
    since = max(utc(current) - timedelta(hours=96), utc(previous.created_at)) if previous else utc(current) - timedelta(hours=72)
    try:
        evidence = await provider.fetch(current, since)
    except Exception:
        evidence = {"collected_at": utc(current).isoformat(), "indices": {}, "breadth": None,
                    "news": [], "news_sources_ok": [], "errors": [{"source": "Market sources", "reason": "FETCH_FAILED"}]}
    mark_quotes(evidence, current, slot, calendar)
    if slot == "noon":
        update = db.scalar(select(MarketBrief).where(
            MarketBrief.session_date == current.astimezone(IST).date(),
            MarketBrief.slot == "midmorning", MarketBrief.created_at < utc(current)))
        if update is not None:
            # Freeze the actual saved morning news and interpretation, even when
            # those headlines have disappeared from the live feeds by noon.
            evidence["morning_update"] = {
                "report_id": update.id, "date": update.session_date.isoformat(),
                "created_at": utc(update.created_at).isoformat(), "analysis": update.analysis,
                "news": update.evidence["news"], "source_gaps": update.evidence.get("errors", [])}
    assessment = ({"status": "FROZEN_PREOPEN", "threshold_pct": settings.market_brief_direction_threshold_pct,
                   "baseline_levels": baseline_levels(evidence)} if slot == "morning" else
                  assess(morning, evidence, delivery_confirmed=morning_delivered(db, morning)))
    analysis = await asyncio.to_thread(analyze, evidence, morning, previous, ai_provider, assessment)
    return evidence, analysis, assessment, morning, previous


async def run_due(session_factory, *, current=None, provider=None, ai_provider=None):
    if not settings.market_briefs_enabled or not settings.telegram_enabled or not configured():
        return {"status": "PAUSED", "queued": 0}
    supplied_time = current is not None
    current = utc(current or now())
    due = active_slot(current)
    if not due or due["session_date"].weekday() >= 5:
        return {"status": "NOT_DUE", "queued": 0}
    with session_factory() as db:
        if db.get_bind().dialect.name == "postgresql" and not db.scalar(text("SELECT pg_try_advisory_xact_lock(840028)")):
            return {"status": "BUSY", "queued": 0}
        state = db.get(MarketBriefState, 1)
        if state is None:
            state = MarketBriefState(id=1, calendar={})
            db.add(state)
        existing = db.scalar(select(MarketBrief).where(MarketBrief.session_date == due["session_date"], MarketBrief.slot == due["slot"]))
        if existing:
            return {"status": "ALREADY_PREPARED", "queued": 0}
        if state.last_attempt_at and current - utc(state.last_attempt_at) < timedelta(minutes=4):
            return {"status": "COOLDOWN", "queued": 0}
        state.last_attempt_at = current
        provider = provider or MarketBriefProvider()
        if (not state.calendar_checked_at or
                utc(state.calendar_checked_at).astimezone(IST).date() != due["session_date"] or
                state.calendar.get("year") != due["session_date"].year):
            try:
                state.calendar = await provider.calendar(current)
                state.calendar_checked_at = current
                state.last_error = None
            except Exception:
                state.last_error = "NSE trading calendar could not refresh"
        if not state.calendar_checked_at or current - utc(state.calendar_checked_at) > timedelta(days=7) or state.calendar.get("year") != due["session_date"].year:
            db.commit()
            return {"status": "CALENDAR_UNAVAILABLE", "queued": 0}
        if not is_session(due["session_date"], state.calendar):
            db.commit()
            return {"status": "MARKET_HOLIDAY", "queued": 0}
        evidence, analysis, assessment, morning, previous = await collect_review(db, due["slot"], current, state.calendar,
            provider=provider, ai_provider=ai_provider)
        completed = current if supplied_time else now()
        if utc(completed) >= utc(due["expires"]):
            state.last_error = "Market review preparation finished after its delivery window"
            db.commit()
            return {"status": "EXPIRED", "queued": 0}
        if state.last_error:
            evidence["errors"].append({"source": "NSE calendar", "reason": "USING_CACHED_CALENDAR"})
        report = MarketBrief(session_date=due["session_date"], slot=due["slot"], created_at=completed,
            expires_at=utc(due["expires"]), morning_id=morning.id if morning else None,
            previous_close_id=previous.id if previous else None,
            evidence=evidence, analysis=analysis, assessment=assessment)
        db.add(report)
        db.flush()
        message = format_message(report)
        chat = settings.telegram_chat_id.strip()
        db.add(TelegramDelivery(event_id=None, version=VERSION,
            idempotency_key=digest(["market-brief", due["session_date"].isoformat(), due["slot"], chat]),
            chat_id=chat, status="PENDING", attempts=0, next_attempt_at=completed,
            notification={"kind": "MARKET_BRIEF", "brief_id": report.id, "slot": report.slot,
                          "expires_at": report.expires_at.isoformat()},
            message=message, message_parts=[message], message_ids=[], parse_mode="HTML"))
        db.commit()
        return {"status": "QUEUED", "queued": 1, "brief_id": report.id}


def validate_delivery(db, job, current):
    if not settings.market_briefs_enabled:
        return "Market briefs are paused"
    report = db.get(MarketBrief, (job.notification or {}).get("brief_id"))
    if report is None:
        return "Market review is unavailable"
    if utc(current) >= utc(report.expires_at):
        return "Market review delivery window expired"
    if report.session_date != utc(current).astimezone(IST).date():
        return "Market review belongs to another session"
    for quote in report.evidence["indices"].values():
        if quote["status"] == "CURRENT_SESSION" and utc(current) - utc(datetime.fromisoformat(quote["as_of"])) > timedelta(minutes=20):
            return "Intraday market observation became stale before delivery"
    return None


def dashboard(db):
    state = db.get(MarketBriefState, 1)
    reports = list(db.scalars(select(MarketBrief).order_by(MarketBrief.id.desc()).limit(30)))
    scored = [r for r in reports if r.slot == "close" and r.assessment.get("status") in {"SUPPORTED", "NOT_SUPPORTED", "MIXED"}]
    deliveries = list(db.scalars(select(TelegramDelivery).where(TelegramDelivery.version == VERSION)
        .order_by(TelegramDelivery.id.desc()).limit(12)))
    return {"enabled": settings.market_briefs_enabled, "telegram_connected": settings.telegram_enabled and configured(),
            "schedule_ist": settings.market_brief_times_ist.split(","),
            "calendar_checked_at": state.calendar_checked_at if state else None,
            "last_error": state.last_error if state else None,
            "accuracy": {"evaluated_sessions": len(scored), "supported": sum(r.assessment["status"] == "SUPPORTED" for r in scored),
                         "note": "Small, uncalibrated direction sample; intraday observations are excluded."},
            "reports": [{"id": r.id, "session_date": r.session_date, "slot": r.slot, "created_at": r.created_at,
                         "analysis": r.analysis, "assessment": r.assessment, "morning_id": r.morning_id,
                         "morning_update_id": (r.evidence.get("morning_update") or {}).get("report_id"),
                         "previous_close_id": r.previous_close_id, "source_gaps": r.evidence.get("errors", [])} for r in reports],
            "deliveries": [{"id": j.id, "status": j.status, "sent_at": j.sent_at, "slot": (j.notification or {}).get("slot")} for j in deliveries]}
