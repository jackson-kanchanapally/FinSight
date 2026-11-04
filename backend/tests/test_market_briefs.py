"""Market-session, forecast-memory, source and Telegram contracts; no live sends."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, time
from decimal import Decimal
from email.utils import format_datetime
import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings, settings
from app.database import Base
from app.models.market_brief import MarketBrief, MarketBriefState
from app.models.telegram import TelegramDelivery
from app.services import market_briefs as briefs, telegram_alerts
from app.services.gold_sources import IST, utc
from app.services.market_brief_analysis import assess, mark_quotes, validate_narrative
from app.services.market_brief_sources import (MarketBriefProvider, parse_calendar, parse_feed,
                                             parse_nse, parse_sensex)
from app.services.telegram_formatter import units

MORNING = datetime(2026, 11, 2, 8, 45, tzinfo=IST)
CALENDAR = {"year": 2026, "holidays": ["2026-11-10"], "source_url": "https://www.nseindia.com/"}


def evidence(current, *, change="1.00", news=True):
    current = current.astimezone(IST)
    morning = current.timetz().replace(tzinfo=None) < time(9, 15)
    if morning:
        stamp = current - timedelta(days=1)
        while stamp.weekday() >= 5:
            stamp -= timedelta(days=1)
        stamp = stamp.replace(hour=15, minute=30)
    else:
        stamp = current.replace(hour=15, minute=30) if current.hour >= 16 else current - timedelta(minutes=1)
    indices = {}
    for name, base in (("NIFTY 50", Decimal("100")), ("SENSEX", Decimal("200")),
                       ("NIFTY BANK", Decimal("150")), ("NIFTY IT", Decimal("80")),
                       ("NIFTY METAL", Decimal("90")), ("INDIA VIX", Decimal("15"))):
        last = base if morning else base * (1 + Decimal(change) / 100)
        indices[name] = {"id": "Q_" + name.replace(" ", "_"), "name": name,
                         "last": str(last), "previous_close": str(base if not morning else base / Decimal("1.01")),
                         "open": str(base), "high": str(base * Decimal("1.05")), "low": str(base * Decimal("0.95")),
                         "change_pct": change, "as_of": stamp.isoformat(),
                         "source": "Test source", "source_url": "https://www.nseindia.com/"}
    rows = [{"id": "N_INDIA", "title": "Indian equities gain as banks recover", "summary": "A test report attributes the gain to bank buying.",
             "source": "ET Markets", "region": "INDIA", "url": "https://economictimes.indiatimes.com/test-india",
             "published_at": (current - timedelta(hours=1)).isoformat()},
            {"id": "N_GLOBAL", "title": "US stocks rise as bond yields fall", "summary": "A test report of lower yields.",
             "source": "BBC Business", "region": "GLOBAL", "url": "https://www.bbc.com/test-world",
             "published_at": (current - timedelta(hours=2)).isoformat()}] if news else []
    return {"collected_at": utc(current).isoformat(), "indices": indices, "breadth": None,
            "news": rows, "news_sources_ok": ["ET Markets", "BBC Business"] if news else [], "errors": []}


class Provider:
    def __init__(self, *, news=True, calendar_error=False):
        self.calls, self.news, self.calendar_error = 0, news, calendar_error

    async def calendar(self, current):
        if self.calendar_error:
            raise ValueError("offline")
        return deepcopy(CALENDAR)

    async def fetch(self, current, since):
        self.calls += 1
        return evidence(current, news=self.news)


class AI:
    name, model, timeout = "test", "market-test", 5

    def __init__(self):
        self.contexts = []

    def generate(self, system, user, schema):
        context = json.loads(user.split("CONTEXT:\n", 1)[1])
        self.contexts.append(context)
        claim = lambda text: {"text": text, "evidence_ids": ["N_INDIA", "N_GLOBAL"]}
        morning_ids = [key for key in ("H_MORNING", "H_MORNING_UPDATE") if key in context["evidence"]]
        return json.dumps({"view": "UP", "confidence": "MEDIUM",
            "summary": claim("Domestic buying and lower global yields offer tentative support."),
            "drivers": [{**claim("Lower bond yields could support equity demand if they persist."), "basis": "POSSIBLE"}],
            "review_of_morning": {"text": "The original yield hypothesis remains possible; the observed direction does not confirm causation.",
                                  "evidence_ids": [*morning_ids, "N_GLOBAL"]} if morning_ids else None,
            "next_session": claim("Equities could retain support if global yields stay softer."),
            "longer_term": claim("The outlook may improve if domestic earnings and policy remain supportive."),
            "invalidation": claim("Watch for renewed yield pressure or weaker domestic buying.")})


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'market.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(settings, "market_briefs_enabled", True)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:SECRET"))
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    yield factory
    engine.dispose()


def run(store, current=MORNING, **kwargs):
    return asyncio.run(briefs.run_due(store, current=current, **kwargs))


def test_four_reviews_freeze_morning_grade_and_feed_next_morning(store):
    ai, provider = AI(), Provider()
    assert run(store, provider=provider, ai_provider=ai)["queued"] == 1
    assert run(store, current=MORNING + timedelta(minutes=5), provider=provider, ai_provider=ai)["queued"] == 0
    with store() as db:
        morning = db.scalar(select(MarketBrief))
        morning_id, original = morning.id, deepcopy(morning.analysis)
        job = db.scalar(select(TelegramDelivery))
        job.status, job.sent_at = "SENT", utc(MORNING + timedelta(minutes=1))
        db.commit()
    assert run(store, MORNING.replace(hour=11, minute=0), provider=provider, ai_provider=ai)["queued"] == 1
    assert run(store, MORNING.replace(hour=11, minute=5), provider=provider, ai_provider=ai)["queued"] == 0
    assert run(store, MORNING.replace(hour=12, minute=30), provider=provider, ai_provider=ai)["queued"] == 1
    assert run(store, MORNING.replace(hour=16, minute=0), provider=provider, ai_provider=ai)["queued"] == 1
    with store() as db:
        rows = list(db.scalars(select(MarketBrief).order_by(MarketBrief.id)))
        assert [row.slot for row in rows] == ["morning", "midmorning", "noon", "close"]
        assert rows[0].analysis == original
        assert all(row.morning_id == morning_id for row in rows[1:])
        assert all(row.assessment["status"] == "SUPPORTED" for row in rows[1:])
        assert all(row.assessment["provisional"] is True for row in rows[1:3])
        assert rows[3].assessment["provisional"] is False
        assert all(q["status"] == "CURRENT_SESSION" for q in rows[1].evidence["indices"].values())
        assert "UPDATED MORNING BRIEF" in briefs.format_message(rows[1])
        assert "Morning reasoning check" in briefs.format_message(rows[1])
        assert rows[2].evidence["morning_update"]["report_id"] == rows[1].id
        assert ai.contexts[2]["evidence"]["H_MORNING_UPDATE"]["analysis"] == rows[1].analysis
        assert "Updated morning brief review" in briefs.format_message(rows[2])
        assert "H_MORNING_UPDATE" in rows[2].analysis["review_of_morning"]["evidence_ids"]
        assert "H_MORNING_UPDATE" not in ai.contexts[3]["evidence"]
        close_id = rows[3].id
        jobs = list(db.scalars(select(TelegramDelivery)))
        assert len(jobs) == 4 and len({job.idempotency_key for job in jobs}) == 4
    assert run(store, MORNING + timedelta(days=1), provider=provider, ai_provider=ai)["queued"] == 1
    with store() as db:
        latest = db.scalar(select(MarketBrief).order_by(MarketBrief.id.desc()))
        assert latest.previous_close_id == close_id
        assert ai.contexts[-1]["evidence"]["H_PREVIOUS_CLOSE"]["analysis"]["next_session"] == original["next_session"]


def test_no_retroactive_morning_and_no_false_grade_after_installation(store):
    assert run(store, MORNING.replace(hour=9, minute=15), provider=Provider(), ai_provider=AI())["queued"] == 0
    assert run(store, MORNING.replace(hour=12, minute=30), provider=Provider(), ai_provider=AI())["queued"] == 1
    with store() as db:
        report = db.scalar(select(MarketBrief))
        assert report.morning_id is None
        assert report.assessment["status"] == "UNSCORED"


@pytest.mark.parametrize("day", [MORNING.replace(day=7), MORNING.replace(day=10)])
def test_weekends_and_exchange_holidays_send_nothing(store, day):
    provider = Provider()
    assert run(store, day, provider=provider, ai_provider=AI())["queued"] == 0
    assert provider.calls == 0


def test_calendar_failure_fails_closed_without_a_valid_cache(store):
    assert run(store, provider=Provider(calendar_error=True), ai_provider=AI())["status"] == "CALENDAR_UNAVAILABLE"
    with store() as db:
        assert db.scalar(select(MarketBrief)) is None
        assert db.get(MarketBriefState, 1).last_error


def test_recent_cached_calendar_survives_source_failure_with_visible_gap(store):
    with store() as db:
        db.add(MarketBriefState(id=1, calendar=deepcopy(CALENDAR), calendar_checked_at=utc(MORNING) - timedelta(days=2)))
        db.commit()
    assert run(store, provider=Provider(calendar_error=True), ai_provider=AI())["queued"] == 1
    with store() as db:
        assert {e["source"] for e in db.scalar(select(MarketBrief)).evidence["errors"]} == {"NSE calendar"}


def test_preparation_that_finishes_after_cutoff_does_not_save_forecast(store, monkeypatch):
    clocks = iter([utc(MORNING), utc(MORNING.replace(hour=9, minute=11))])
    monkeypatch.setattr(briefs, "now", lambda: next(clocks))
    result = asyncio.run(briefs.run_due(store, provider=Provider(), ai_provider=AI()))
    assert result["status"] == "EXPIRED"
    with store() as db:
        assert db.scalar(select(MarketBrief)) is None


def test_missing_news_produces_transparent_unscored_alert(store):
    ai = AI()
    assert run(store, provider=Provider(news=False), ai_provider=ai)["queued"] == 1
    with store() as db:
        row = db.scalar(select(MarketBrief))
        assert row.analysis["view"] == "UNCLEAR"
        assert "unavailable" in db.scalar(select(TelegramDelivery)).message
    assert not ai.contexts


def test_worker_sends_market_brief_once_and_expires_late_messages(store, monkeypatch):
    run(store, provider=Provider(), ai_provider=AI())
    sent = []
    async def send(chat, message, **kwargs):
        sent.append(message)
        return 777
    monkeypatch.setattr(telegram_alerts, "send_message", send)
    monkeypatch.setattr(telegram_alerts, "now", lambda: utc(MORNING + timedelta(minutes=1)))
    assert asyncio.run(telegram_alerts.deliver_one(store))
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    assert len(sent) == 1 and "MORNING MARKET OUTLOOK" in sent[0]
    run(store, MORNING.replace(hour=12, minute=30), provider=Provider(), ai_provider=AI())
    monkeypatch.setattr(telegram_alerts, "now", lambda: utc(MORNING.replace(hour=13, minute=1)))
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    with store() as db:
        assert db.scalar(select(TelegramDelivery).order_by(TelegramDelivery.id.desc())).status == "SUPERSEDED"


def test_pausing_market_briefs_blocks_pending_message(store, monkeypatch):
    run(store, provider=Provider(), ai_provider=AI())
    monkeypatch.setattr(settings, "market_briefs_enabled", False)
    monkeypatch.setattr(telegram_alerts, "now", lambda: utc(MORNING + timedelta(minutes=1)))
    assert not asyncio.run(telegram_alerts.deliver_one(store))


@pytest.mark.parametrize("moves,status", [(("1.0", "0.7"), "SUPPORTED"),
    (("-1.0", "-0.7"), "NOT_SUPPORTED"), (("1.0", "-0.7"), "MIXED")])
def test_grading_uses_frozen_baselines_and_agreement_of_both_indices(moves, status):
    data = mark_quotes(evidence(MORNING.replace(hour=12, minute=30)), MORNING.replace(hour=12, minute=30), "noon", CALENDAR)
    data["indices"]["NIFTY 50"]["last"] = str(100 * (1 + Decimal(moves[0]) / 100))
    data["indices"]["SENSEX"]["last"] = str(200 * (1 + Decimal(moves[1]) / 100))
    morning = SimpleNamespace(analysis={"view": "UP", "status": "VERIFIED_AI"}, assessment={"threshold_pct": 0.3,
        "baseline_levels": {"NIFTY 50": {"value": "100"}, "SENSEX": {"value": "200"}}})
    assert assess(morning, data, delivery_confirmed=True)["status"] == status
    data["indices"]["SENSEX"]["status"] = "STALE"
    assert assess(morning, data, delivery_confirmed=True)["status"] == "UNSCORED"
    assert assess(morning, data, delivery_confirmed=False)["status"] == "UNSCORED"


def test_stale_previous_day_and_early_close_cannot_be_graded():
    current = MORNING.replace(hour=16, minute=0)
    data = evidence(current)
    data["indices"]["SENSEX"]["as_of"] = current.replace(hour=15, minute=29).isoformat()
    mark_quotes(data, current, "close", CALENDAR)
    assert data["indices"]["SENSEX"]["status"] == "STALE"


def test_news_rejects_future_items_unsafe_links_and_xml_entities():
    def item(title, day, url="https://www.bbc.com/news/test"):
        return f"<item><title>{title}</title><link>{url}</link><pubDate>{format_datetime(day)}</pubDate></item>"
    xml = "<rss><channel>" + item("Global stocks rise", MORNING - timedelta(hours=1))
    xml += item("Stocks fall", MORNING + timedelta(minutes=1))
    xml += item("Oil prices rise", MORNING, "https://evil.test/a") + "</channel></rss>"
    rows = parse_feed(xml, "BBC Business", "GLOBAL", "www.bbc.com", MORNING, MORNING - timedelta(days=1))
    assert len(rows) == 1
    with pytest.raises(Exception, match="DECLARATION_REJECTED"):
        parse_feed('<!DOCTYPE rss><rss/>', "BBC", "GLOBAL", "www.bbc.com", MORNING, MORNING)


def test_index_parsers_verify_source_identity_dates_and_percentage():
    current = MORNING.replace(hour=12, minute=30)
    row = {"index": "NIFTY 50", "last": 101, "previousClose": 100, "open": 100, "high": 102, "low": 99, "percentChange": 1}
    quotes, _ = parse_nse({"timestamp": "02-Nov-2026 12:29", "data": [row]}, current)
    assert quotes["NIFTY 50"]["change_pct"] == "1.00"
    with pytest.raises(Exception):
        parse_nse({"timestamp": "02-Nov-2026 13:00", "data": [row]}, current)
    with pytest.raises(Exception):
        parse_nse({"timestamp": "02-Nov-2026 12:29", "data": [{**row, "percentChange": 8}]}, current)
    html = '<title>SENSEX live</title><span id="sp_val">202</span><span id="sp_previousclose">200</span><span id="sp_open">200</span><span id="sp_high">203</span><span id="sp_low">199</span><p id="sp_asOnDate">As on 02 Nov, 2026 | 12:29 IST</p>'
    assert parse_sensex(html, current)["last"] == "202"
    with pytest.raises(Exception, match="IDENTITY_CHANGED"):
        parse_sensex(html.replace("SENSEX", "Something else"), current)


def test_nse_calendar_rejects_wrong_segment_or_year():
    with pytest.raises(Exception, match="CALENDAR_UNAVAILABLE"):
        parse_calendar({"FO": []}, MORNING)
    with pytest.raises(Exception, match="YEAR_MISMATCH"):
        parse_calendar({"CM": [{"tradingDate": f"{day:02}-Jan-2025"} for day in range(1, 10)]}, MORNING)


def test_provider_does_not_fetch_quotes_when_robots_denies_access():
    requests = []
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /")
    async def work():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await MarketBriefProvider(client).fetch(MORNING, MORNING - timedelta(days=1))
    result = asyncio.run(work())
    assert not result["indices"] and not result["news"]
    assert all(url.endswith("/robots.txt") for url in requests)


def test_model_claims_reject_invented_numbers_unknown_evidence_and_certainty():
    ai = AI()
    context = {"evidence": {"N_INDIA": {"title": "Indian stocks rise"}, "N_GLOBAL": {"title": "Yields fall"}}}
    raw = ai.generate("", "CONTEXT:\n" + json.dumps(context), {})
    assert validate_narrative(raw, context["evidence"])["view"] == "UP"
    for field, value in (("text", "Markets will rise"), ("text", "A gain of 99 percent"), ("evidence_ids", ["N_INVENTED"])):
        body = json.loads(raw)
        body["summary"][field] = value
        with pytest.raises(ValueError):
            validate_narrative(json.dumps(body), context["evidence"])


@pytest.mark.parametrize("slot", ["morning", "midmorning", "noon"])
def test_message_handles_html_and_full_claim_limits_within_one_telegram_message(store, slot):
    run(store, provider=Provider(), ai_provider=AI())
    if slot in {"midmorning", "noon"}:
        run(store, MORNING.replace(hour=11, minute=0), provider=Provider(), ai_provider=AI())
    if slot == "noon":
        run(store, MORNING.replace(hour=12, minute=30), provider=Provider(), ai_provider=AI())
    with store() as db:
        report = db.scalar(select(MarketBrief).where(MarketBrief.slot == slot))
        a = deepcopy(report.analysis)
        a["drivers"] *= 3
        for claim in [a["summary"], *a["drivers"], a["next_session"], a["longer_term"], a["invalidation"]]:
            claim["text"] = "<script> & " * 38
        if a.get("review_of_morning"):
            a["review_of_morning"]["text"] = "<script> & " * 38
        report.analysis = a
        message = briefs.format_message(report)
        assert "<script>" not in message and "&lt;script&gt;" in message
        assert units(message) <= 3800
        report.slot = "close"
        report.assessment = {"status": "UNSCORED", "reason": "No original morning forecast is available. " * 8}
        a["review_of_morning"] = {"text": "<script> & " * 38, "evidence_ids": []}
        message = briefs.format_message(report)
        assert units(message) <= 3800 and "Further ahead:" in message


def test_borderline_return_is_not_rounded_into_forecast_success():
    current = MORNING.replace(hour=12, minute=30)
    data = mark_quotes(evidence(current, change="0.2999"), current, "noon", CALENDAR)
    morning = SimpleNamespace(analysis={"view": "UP", "status": "VERIFIED_AI"}, assessment={"threshold_pct": 0.3,
        "baseline_levels": {"NIFTY 50": {"value": "100"}, "SENSEX": {"value": "200"}}})
    result = assess(morning, data, delivery_confirmed=True)
    assert result["observed"] == "RANGE" and result["status"] == "NOT_SUPPORTED"


def test_preview_cannot_create_a_saved_morning_forecast(client, monkeypatch):
    from app.api import market_briefs as api
    from app.services import market_brief_analysis
    monkeypatch.setattr(api, "MarketBriefProvider", Provider)
    monkeypatch.setattr(briefs, "now", lambda: MORNING)
    monkeypatch.setattr(market_brief_analysis, "configured_provider", AI)
    response = client.post('/api/market-briefs/preview?slot=morning')
    assert response.status_code == 200
    assert response.json()["saved"] is False and response.json()["queued"] is False
    assert "PREVIEW" in response.json()["message"]
    assert client.get('/api/market-briefs').json()["reports"] == []


def test_market_brief_migration_roundtrip_does_not_touch_existing_tables():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).parents[1] / 'alembic/versions/0028_market_briefs.py'
    spec = importlib.util.spec_from_file_location('market_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE existing_data (id INTEGER PRIMARY KEY)')
        operations = Operations(MigrationContext.configure(connection))
        with Operations.context(operations.migration_context):
            migration.upgrade()
            assert set(inspect(connection).get_table_names()) == {'existing_data', 'market_brief_state', 'market_briefs'}
            migration.downgrade()
        assert inspect(connection).get_table_names() == ['existing_data']
    engine.dispose()


def test_market_schedule_validation():
    assert Settings(_env_file=None).market_brief_times_ist == "08:45,11:00,12:30,16:00"
    assert Settings(_env_file=None, market_brief_times_ist="08:30,12:00,16:30").market_brief_times_ist == "08:30,11:00,12:00,16:30"
    with pytest.raises(ValueError):
        Settings(_env_file=None, market_brief_times_ist="09:30,12:30,16:00")
    with pytest.raises(ValueError):
        Settings(_env_file=None, market_brief_times_ist="08:45,11:00,11:15,16:00")
    assert briefs.active_slot(MORNING.replace(hour=9, minute=10)) is None
    assert briefs.active_slot(MORNING.replace(hour=10, minute=59)) is None
    assert briefs.active_slot(MORNING.replace(hour=11, minute=0))["slot"] == "midmorning"
    assert briefs.active_slot(MORNING.replace(hour=11, minute=29))["slot"] == "midmorning"
    assert briefs.active_slot(MORNING.replace(hour=11, minute=30)) is None


def test_midmorning_worker_sends_once_and_does_not_replay_expired_update(store, monkeypatch):
    current = MORNING.replace(hour=11, minute=0)
    run(store, current, provider=Provider(), ai_provider=AI())
    sent = []

    async def send(chat, message, **kwargs):
        sent.append(message)
        return 778

    monkeypatch.setattr(telegram_alerts, "send_message", send)
    monkeypatch.setattr(telegram_alerts, "now", lambda: utc(current + timedelta(minutes=1)))
    assert asyncio.run(telegram_alerts.deliver_one(store))
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    assert len(sent) == 1 and "UPDATED MORNING BRIEF" in sent[0]
    run(store, current + timedelta(days=1), provider=Provider(), ai_provider=AI())
    monkeypatch.setattr(telegram_alerts, "now", lambda: utc(current + timedelta(days=1, minutes=30)))
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    assert len(sent) == 1
    with store() as db:
        assert db.scalar(select(TelegramDelivery).order_by(TelegramDelivery.id.desc())).status == "SUPERSEDED"


def test_noon_retains_actual_updated_morning_news_when_live_headlines_change(store):
    class ChangingNews(Provider):
        async def fetch(self, current, since):
            data = await super().fetch(current, since)
            if current.astimezone(IST).hour == 11:
                data["news"][0]["title"] = "Morning policy announcement supports domestic sentiment"
            return data

    ai, provider = AI(), ChangingNews()
    run(store, MORNING.replace(hour=11, minute=0), provider=provider, ai_provider=ai)
    run(store, MORNING.replace(hour=12, minute=30), provider=provider, ai_provider=ai)
    context = ai.contexts[-1]["evidence"]
    assert context["H_MORNING_UPDATE"]["news"][0]["title"] == "Morning policy announcement supports domestic sentiment"
    assert context["N_INDIA"]["title"] == "Indian equities gain as banks recover"
    with store() as db:
        noon = db.scalar(select(MarketBrief).where(MarketBrief.slot == "noon"))
        assert noon.morning_id is None
        assert noon.assessment["status"] == "UNSCORED"
        assert noon.analysis["status"] == "VERIFIED_AI"
        assert noon.analysis["review_of_morning"]["evidence_ids"] == ["H_MORNING_UPDATE", "N_GLOBAL"]
        assert "11:00 IST" in briefs.format_message(noon)
        assert briefs.dashboard(db)["reports"][0]["morning_update_id"] == context["H_MORNING_UPDATE"]["report_id"]


@pytest.mark.parametrize("unavailable", ["missing", "previous_day", "future"])
def test_noon_uses_only_an_earlier_updated_brief_from_same_session(store, unavailable):
    ai = AI()
    run(store, provider=Provider(), ai_provider=ai)
    if unavailable != "missing":
        run(store, MORNING.replace(hour=11, minute=0), provider=Provider(), ai_provider=ai)
        with store() as db:
            update = db.scalar(select(MarketBrief).where(MarketBrief.slot == "midmorning"))
            if unavailable == "previous_day":
                update.session_date -= timedelta(days=1)
                update.created_at -= timedelta(days=1)
            else:
                update.created_at = utc(MORNING.replace(hour=13, minute=0))
            db.commit()
    run(store, MORNING.replace(hour=12, minute=30), provider=Provider(), ai_provider=ai)
    context = ai.contexts[-1]["evidence"]
    assert "H_MORNING_UPDATE" not in context and "H_MORNING" in context
    with store() as db:
        noon = db.scalar(select(MarketBrief).where(MarketBrief.slot == "noon"))
        assert "Updated morning brief unavailable" in briefs.format_message(noon)


def test_noon_narrative_cannot_ignore_saved_updated_morning_brief():
    registry = {"N_INDIA": {"title": "Indian stocks rise"}, "N_GLOBAL": {"title": "Yields fall"},
                "H_MORNING_UPDATE": {"analysis": {"view": "UP"}, "news": []}}
    raw = AI().generate("", "CONTEXT:\n" + json.dumps({"evidence": registry}), {})
    assert validate_narrative(raw, registry)["review_of_morning"]
    for review in (None, {"text": "Morning drivers remain possible.", "evidence_ids": ["N_GLOBAL"]},
                   {"text": "Morning drivers remain possible.", "evidence_ids": ["H_MORNING_UPDATE"]}):
        body = json.loads(raw)
        body["review_of_morning"] = review
        with pytest.raises(ValueError, match="MORNING_REVIEW_REQUIRES"):
            validate_narrative(json.dumps(body), registry)
