"""Gold source, schedule, deduplication and Telegram contracts; no live sends."""
import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings, settings
from app.database import Base
from app.models.gold import GoldHeadline, GoldSnapshot, GoldState
from app.models.telegram import TelegramDelivery
from app.schemas.gold import DollarRupeeQuote, GlobalGoldQuote, GoldNewsData, IndiaGoldQuote
from app.services import gold, telegram_alerts
from app.services.gold_sources import GoldFetch, GoldProvider, IST, headline_impact, major_event_key, parse_global, parse_india, parse_news
from app.services.gold_reasoning import build_analysis, explain_headline

NOW = datetime(2026, 11, 2, 11, 0, tzinfo=IST)


def quotes(current=NOW, price="4000", fx_rate="90"):
    return GoldFetch(global_quote=GlobalGoldQuote(usd_per_oz=price, as_of=current, fetched_at=current),
        usd_inr_quote=DollarRupeeQuote(usd_inr_rate=fx_rate,
            quoted_gold_inr_per_oz=Decimal(price) * Decimal(fx_rate), as_of=current, fetched_at=current),
        india_quote=IndiaGoldQuote(city="India", inr_22k_per_g="11200", inr_24k_per_g="12200",
            source_url="https://groww.in/gold-rates",
            rate_date=current.date(), fetched_at=current, change_22k_pct="-0.4", change_24k_pct="-0.4"),
        hyderabad_quote=IndiaGoldQuote(inr_22k_per_g="11000", inr_24k_per_g="12000",
            rate_date=current.date(), fetched_at=current, change_22k_pct="-0.5", change_24k_pct="-0.5"),
        news_sources_ok=["FXStreet", "Federal Reserve"])


def headline(key="news-1", published=NOW, title="Gold rises as the dollar weakens", **changes):
    data = dict(identity=key, title=title, summary="A fictional gold headline used for tests.",
        source="FXStreet", url="https://www.fxstreet.com/news/test-gold-20261102",
        published_at=published, impact="POSITIVE", impact_reason="The headline reports a rise.", important=True)
    data.update(changes)
    return GoldNewsData(**data)


class Provider:
    def __init__(self, *, price="4000", fx_rate="90", headlines=(), error=False):
        self.price, self.fx_rate, self.headlines, self.error, self.calls = price, fx_rate, list(headlines), error, 0

    async def fetch(self, current, cached_india=None, cached_hyderabad=None):
        self.calls += 1
        if self.error:
            raise RuntimeError("private remote response")
        result = quotes(current, self.price, self.fx_rate)
        result.headlines = self.headlines
        return result


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'gold.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(settings, "gold_monitor_enabled", True)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:SECRET"))
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    monkeypatch.setattr(telegram_alerts, "now", lambda: gold.utc(NOW))
    yield factory
    engine.dispose()


def test_source_prices_have_verified_units_city_and_dates():
    global_quote = parse_global({"symbol": "XAU", "currency": "USD", "price": 4000,
                                "updatedAt": NOW.isoformat()}, NOW)
    assert global_quote.usd_per_oz == 4000 and global_quote.unit == "USD/troy oz"
    fx_quote = parse_global({"symbol": "XAU", "currency": "INR", "price": 360000, "exchangeRate": 90,
                              "updatedAt": NOW.isoformat()}, NOW, "INR")
    assert fx_quote.usd_inr_rate == 90 and fx_quote.unit == "INR/USD"
    with pytest.raises(Exception, match="USD_INR_RATE_OUT_OF_RANGE"):
        parse_global({"symbol": "XAU", "currency": "INR", "price": 360000, "exchangeRate": 2,
                      "updatedAt": NOW.isoformat()}, NOW, "INR")
    item = {"date": "2026-11-02", "priceLocation": "Hyderabad",
            "price": {"TWENTY_FOUR": 12000, "TWENTY_TWO": 11000},
            "percentageChange": {"TWENTY_FOUR": -0.5, "TWENTY_TWO": -0.6}}
    def html(value):
        return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {
            "goldRateData": {"physicalGoldRate": {"hyderabad": value}}}}}) + '</script>'
    quote = parse_india(html(item), NOW)
    assert quote.inr_24k_per_g == 12000 and quote.inr_22k_per_g == 11000
    assert quote.rate_date.isoformat() == "2026-11-02"
    assert "excludes GST" in quote.basis
    with pytest.raises(Exception, match="LOCATION_UNVERIFIED"):
        parse_india(html({**item, "priceLocation": "Delhi"}), NOW)
    with pytest.raises(Exception, match="FUTURE_DATE"):
        parse_india(html({**item, "date": "2026-11-03"}), NOW)
    with pytest.raises(Exception, match="SYMBOL_OR_CURRENCY"):
        parse_global({"symbol": "XAG", "currency": "USD"}, NOW)
    with pytest.raises(Exception, match="SYMBOL_OR_CURRENCY"):
        parse_global({"symbol": "XAU", "currency": "USD", "price": 4000,
                      "updatedAt": NOW.isoformat()}, NOW, "INR")
    with pytest.raises(Exception, match="INVALID_TIMESTAMP"):
        parse_global({"symbol": "XAU", "currency": "USD", "price": 4000,
                      "updatedAt": (NOW + timedelta(hours=1)).isoformat()}, NOW)


def feed_item(title, url="https://www.fxstreet.com/news/gold-test", published=NOW):
    from email.utils import format_datetime
    return f"<item><title>{title}</title><link>{url}</link><pubDate>{format_datetime(published)}</pubDate><description>Public feed summary.</description></item>"


def test_news_filters_dates_links_duplicates_and_conditional_predictions():
    items = feed_item("Gold tumbles as Fed hawks press on") + feed_item("EUR/USD rises on dollar weakness")
    items += feed_item("Gold could surge next year") + feed_item("Gold crashes", "https://evil.example/news")
    items += feed_item("Gold rises", published=NOW + timedelta(hours=1))
    rows = parse_news("<rss><channel>" + items + "</channel></rss>", "FXStreet", NOW)
    assert len(rows) == 2
    assert rows[0].impact == "NEGATIVE" and rows[0].important
    assert rows[1].impact == "UNCLEAR"  # A forecast is not a measured move.
    assert headline_impact("Gold struggles as the US Dollar climbs", "FXStreet")[0] == "UNCLEAR"
    assert headline_impact("Gold falls as the US Dollar climbs", "FXStreet")[0] == "NEGATIVE"
    a = parse_news("<rss><channel>" + feed_item("Gold rises above $4,000") + "</channel></rss>", "FXStreet", NOW)[0]
    b = parse_news("<rss><channel>" + feed_item("Gold rises above $4,010") + "</channel></rss>", "FXStreet", NOW)[0]
    assert a.identity == b.identity
    with pytest.raises(Exception, match="DECLARATION_REJECTED"):
        parse_news('<!DOCTYPE rss [<!ENTITY x "bad">]><rss><channel/></rss>', "FXStreet", NOW)


def test_provider_obeys_robots_without_bypassing_denied_html():
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "api.gold-api.com":
            if request.url.path.endswith("/INR"):
                return httpx.Response(200, json={"symbol": "XAU", "currency": "INR", "price": 360000,
                                                  "exchangeRate": 90, "updatedAt": NOW.isoformat()})
            return httpx.Response(200, json={"symbol": "XAU", "currency": "USD", "price": 4000, "updatedAt": NOW.isoformat()})
        return httpx.Response(200, text="User-agent: *\nDisallow: /")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GoldProvider(client).fetch(NOW)
    result = asyncio.run(run())
    assert result.global_quote is not None and result.usd_inr_quote is not None and result.india_quote is None
    assert not any("gold-rate-today" in url for url in seen)
    assert any(row["reason"] == "ROBOTS_ACCESS_DISALLOWED" for row in result.errors)


def test_two_daily_alerts_are_ist_idempotent_and_expire(store):
    with store() as db:
        provider = Provider()
        for hour, minute in [(10, 55), (11, 0), (11, 5), (13, 0), (17, 0), (17, 15), (21, 0)]:
            asyncio.run(gold.refresh(db, provider=provider, current=NOW.replace(hour=hour, minute=minute), notify=True))
        jobs = list(db.scalars(select(TelegramDelivery).order_by(TelegramDelivery.id)))
        assert [job.notification["brief_kind"] for job in jobs] == ["morning", "evening"]
        assert all(job.event_id is None and job.parse_mode == "HTML" for job in jobs)
        assert gold.validate_delivery(db, jobs[0], NOW + timedelta(minutes=31)) == "Gold brief expired"
        assert "Hyderabad: 22K" in jobs[0].message and "India: 22K" in jobs[0].message
        assert "$4,000.00/troy oz" in jobs[0].message
        assert "USD/INR: <b>$1 = ₹90.0000</b>" in jobs[0].message
        assert "0.00% vs previous rate · rupee little changed" in jobs[0].message
        assert "+0.00% vs previous rate" not in jobs[0].message
        assert "INR spot:" not in jobs[0].message
        assert "₹360,000.00/troy oz" not in jobs[0].message
        assert "GST/making" not in jobs[0].message
    assert gold.active_slot(NOW.replace(hour=11, minute=30)) is None
    assert gold.active_slot(NOW.replace(hour=14, minute=0)) is None
    assert gold.active_slot(NOW.replace(hour=22, minute=30)) is None


def test_usd_inr_change_explains_rupee_weakness_for_indian_gold(store):
    provider = Provider()
    with store() as db:
        asyncio.run(gold.refresh(db, provider=provider, current=NOW - timedelta(minutes=15)))
        provider.price = "4044"
        provider.fx_rate = "91.35"
        asyncio.run(gold.refresh(db, provider=provider, current=NOW, notify=True))
        message = db.scalar(select(TelegramDelivery)).message
        assert "$4,044.00/troy oz</b> · +1.10% vs previous USD quote" in message
        assert "USD/INR: <b>$1 = ₹91.3500</b> · +1.50% vs previous rate · rupee weaker" in message
        assert "can raise the INR cost of imported gold" in message
        analysis = gold.dashboard(db, current=NOW)["analysis"]
        assert "rupee weakened against the dollar" in analysis["local_context"]["india"]
        assert "rupee weakened against the dollar" in analysis["local_context"]["hyderabad"]


def test_usd_inr_fall_explains_rupee_strength_without_changing_global_bias(store):
    provider = Provider()
    with store() as db:
        asyncio.run(gold.refresh(db, provider=provider, current=NOW - timedelta(minutes=15)))
        provider.fx_rate = "89.55"
        asyncio.run(gold.refresh(db, provider=provider, current=NOW, notify=True))
        message = db.scalar(select(TelegramDelivery)).message
        assert "-0.50% vs previous rate · rupee stronger" in message
        assert "can ease the INR cost of imported gold" in message
        analysis = gold.dashboard(db, current=NOW)["analysis"]
        assert analysis["bias"] == "UNCLEAR"
        assert "rupee strengthened against the dollar" in analysis["local_context"]["india"]


def test_alert_omits_fx_when_its_timestamp_is_too_far_from_usd(store):
    class MismatchedProvider(Provider):
        async def fetch(self, current, cached_india=None, cached_hyderabad=None):
            result = await super().fetch(current, cached_india, cached_hyderabad)
            result.usd_inr_quote.as_of = current - timedelta(minutes=10)
            return result

    with store() as db:
        asyncio.run(gold.refresh(db, provider=MismatchedProvider(), current=NOW, notify=True))
        message = db.scalar(select(TelegramDelivery)).message
        assert "$4,000.00/troy oz" in message
        assert "USD/INR:" not in message


def test_alert_omits_inconsistent_fx_rate_and_legacy_inr_gold_price(store):
    class InconsistentProvider(Provider):
        async def fetch(self, current, cached_india=None, cached_hyderabad=None):
            result = await super().fetch(current, cached_india, cached_hyderabad)
            result.usd_inr_quote.quoted_gold_inr_per_oz = Decimal("500000")
            return result

    with store() as db:
        old = quotes().global_quote.model_dump(mode="json")
        old["inr_per_oz"] = "360000"
        db.add(GoldSnapshot(fetched_at=gold.utc(NOW) - timedelta(minutes=15),
                            prices={"global": old, "india": None, "hyderabad": None}, analysis={}, errors=[]))
        db.commit()
        asyncio.run(gold.refresh(db, provider=InconsistentProvider(), current=NOW, notify=True))
        message = db.scalar(select(TelegramDelivery)).message
        assert "USD/INR:" not in message
        assert "₹360,000.00/troy oz" not in message


def test_scheduler_forces_a_fresh_morning_pass_before_periodic_poll(store):
    provider = Provider()
    with store() as db:
        asyncio.run(gold.refresh(db, current=NOW - timedelta(minutes=5), provider=provider))
    asyncio.run(gold.run_due(store, current=NOW, provider=provider))
    asyncio.run(gold.run_due(store, current=NOW + timedelta(minutes=1), provider=provider))
    assert provider.calls == 2
    with store() as db:
        assert len(list(db.scalars(select(TelegramDelivery)))) == 1


def test_important_news_is_collected_without_separate_alerts(store):
    start = NOW.replace(hour=10, minute=0)
    provider = Provider(headlines=[headline("old", start - timedelta(minutes=1))])
    with store() as db:
        assert asyncio.run(gold.refresh(db, current=start, provider=provider, notify=True))["queued"] == 0
        provider.headlines += [headline("new", start + timedelta(minutes=5))] * 2
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=15), provider=provider, notify=True))["queued"] == 0
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=30), provider=provider, notify=True))["queued"] == 0
        assert len(list(db.scalars(select(GoldHeadline)))) == 2
        assert len(list(db.scalars(select(TelegramDelivery)))) == 0


def test_price_move_uses_last_alert_anchor_and_cooldown(store):
    start = NOW.replace(hour=9, minute=0)
    provider = Provider()
    with store() as db:
        asyncio.run(gold.refresh(db, current=start, provider=provider, notify=True))
        provider.price = "4044"
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=15), provider=provider, notify=True))["queued"] == 1
        provider.price = "4090"
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=30), provider=provider, notify=True))["queued"] == 0
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=80), provider=provider, notify=True))["queued"] == 1
        jobs = list(db.scalars(select(TelegramDelivery)))
        assert all(job.notification["brief_kind"] == "move" for job in jobs)
        assert "+1.10%" in jobs[0].message


def test_only_large_moves_trigger_extra_alerts_in_either_direction(store):
    start = NOW.replace(hour=8, minute=0)
    provider = Provider()
    with store() as db:
        asyncio.run(gold.refresh(db, current=start, provider=provider, notify=True))
        provider.price = "4039.6"
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=15), provider=provider, notify=True))["queued"] == 0
        provider.price = "3959.6"
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=30), provider=provider, notify=True))["queued"] == 1
        job = db.scalar(select(TelegramDelivery))
        assert job.notification["brief_kind"] == "move" and "-1.01%" in job.message


@pytest.mark.parametrize("kind", ["news", "major_news", "noon", "morning"])
def test_legacy_news_and_schedule_messages_are_superseded(store, monkeypatch, kind):
    with store() as db:
        asyncio.run(gold.refresh(db, current=NOW, provider=Provider()))
        snapshot = gold.latest_snapshot(db)
        assert gold.queue_message(db, snapshot, NOW, kind, "legacy", NOW + timedelta(minutes=30))
        db.flush()
        job = db.scalar(select(TelegramDelivery))
        note = dict(job.notification)
        note.pop("scheduled_times", None)
        job.notification = note
        db.commit()
    monkeypatch.setattr(telegram_alerts, "now", lambda: gold.utc(NOW + timedelta(minutes=1)))
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    with store() as db:
        assert db.scalar(select(TelegramDelivery)).status == "SUPERSEDED"


@pytest.mark.parametrize("title,source,family", [
    ("Federal Reserve issues FOMC statement", "Federal Reserve", "FED_POLICY_DECISION"),
    ("Gold gains after Fed cuts interest rates", "FXStreet", "FED_POLICY_DECISION"),
    ("Gold falls after US CPI rises above expectations", "FXStreet", "US_INFLATION_SURPRISE"),
    ("Gold falls after US CPI rises above forecasts", "FXStreet", "US_INFLATION_SURPRISE"),
    ("Gold falls after US core PCE rises above expectations", "FXStreet", "US_INFLATION_SURPRISE"),
    ("Gold gains after US Nonfarm Payrolls miss forecasts", "FXStreet", "US_JOBS_SURPRISE"),
    ("Gold rises after US launches airstrikes on Iran", "FXStreet", "CONFLICT_ESCALATION"),
    ("Gold falls as Israel ceasefire agreed", "FXStreet", "CEASEFIRE_AGREEMENT"),
    ("Gold rises as Israel ceasefire collapses", "FXStreet", "CEASEFIRE_BREAKDOWN"),
    ("US announces ceasefire between Israel and Iran", "BBC World", "CEASEFIRE_AGREEMENT"),
    ("Israel ceasefire collapsed", "BBC World", "CEASEFIRE_BREAKDOWN"),
    ("India cuts gold import duty", "FXStreet", "INDIA_GOLD_POLICY"),
    ("Gold rallies as China central bank resumes gold buying", "FXStreet", "CENTRAL_BANK_GOLD_POLICY"),
])
def test_major_event_filter_recognizes_reported_developments(title, source, family):
    assert major_event_key(title, source).split(":", 1)[0] == family


@pytest.mark.parametrize("title,source", [
    ("Gold rises as the dollar weakens", "FXStreet"),
    ("Gold surges to a record high", "FXStreet"),
    ("Gold awaits Fed interest rate decision", "FXStreet"),
    ("Gold could surge if Fed cuts interest rates", "FXStreet"),
    ("Gold forecast: Fed cuts interest rates", "FXStreet"),
    ("Gold slips ahead of US CPI", "FXStreet"),
    ("Gold steadies as US CPI expectations rise", "FXStreet"),
    ("Gold steadies as Fed has not cut interest rates", "FXStreet"),
    ("Gold rises on rumours Fed will cut interest rates", "FXStreet"),
    ("Gold falls as no ceasefire agreed in Iran", "FXStreet"),
    ("Gold rises as ceasefire hopes fade", "FXStreet"),
    ("Gold rises on Israel ceasefire collapse fears", "FXStreet"),
    ("Implementation Note issued November 2", "Federal Reserve"),
    ("Minutes of the Federal Open Market Committee", "Federal Reserve"),
])
def test_major_event_filter_excludes_routine_and_unconfirmed_news(title, source):
    assert major_event_key(title, source) is None


def test_bbc_world_only_collects_qualifying_major_reports():
    xml = "<rss><channel>" + feed_item("US launches airstrikes on Iran", "https://www.bbc.com/news/major-event")
    xml += feed_item("A routine world news update", "https://www.bbc.com/news/routine") + "</channel></rss>"
    rows = parse_news(xml, "BBC World", NOW)
    assert len(rows) == 1 and rows[0].important is True
    assert major_event_key(rows[0].title, rows[0].source) == "CONFLICT_ESCALATION:iran"


def test_major_event_alerts_without_price_move_and_deduplicates_cross_source(store):
    start = NOW.replace(hour=8, minute=0)
    provider = Provider()
    with store() as db:
        asyncio.run(gold.refresh(db, current=start, provider=provider, notify=True))
        provider.headlines = [headline("fx-fed", start + timedelta(minutes=5), title="Gold gains after Fed cuts interest rates")]
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=15), provider=provider, notify=True))["queued"] == 1
        provider.headlines.append(headline("official-fed", start + timedelta(minutes=20),
            title="Federal Reserve issues FOMC statement", source="Federal Reserve"))
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=30), provider=provider, notify=True))["queued"] == 0
        jobs = list(db.scalars(select(TelegramDelivery)))
        assert len(jobs) == 1 and jobs[0].notification["brief_kind"] == "major_news"
        assert jobs[0].notification["major_event_keys"] == ["FED_POLICY_DECISION:us"]
        assert "MAJOR GOLD EVENT" in jobs[0].message


def test_major_event_and_price_move_are_combined_in_one_alert(store):
    start = NOW.replace(hour=8, minute=0)
    provider = Provider()
    with store() as db:
        asyncio.run(gold.refresh(db, current=start, provider=provider, notify=True))
        provider.price = "4044"
        provider.headlines = [headline("fed", start + timedelta(minutes=5), title="Gold gains after Fed cuts interest rates")]
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=15), provider=provider, notify=True))["queued"] == 1
        jobs = list(db.scalars(select(TelegramDelivery)))
        assert len(jobs) == 1 and jobs[0].notification["brief_kind"] == "move"
        assert jobs[0].notification["major_event_keys"]
        assert "Fed cuts interest rates" in jobs[0].message and "+1.10%" in jobs[0].message
        assert db.scalar(select(GoldHeadline)).alerted_at is not None


def test_scheduled_brief_absorbs_major_event_without_duplicate(store):
    with store() as db:
        provider = Provider()
        asyncio.run(gold.refresh(db, current=NOW - timedelta(hours=1), provider=provider, notify=True))
        provider.headlines = [headline("fed", NOW - timedelta(minutes=5), title="Gold gains after Fed cuts interest rates")]
        assert asyncio.run(gold.refresh(db, current=NOW, provider=provider, notify=True))["queued"] == 1
        assert asyncio.run(gold.refresh(db, current=NOW + timedelta(minutes=15), provider=provider, notify=True))["queued"] == 0
        job = db.scalar(select(TelegramDelivery))
        assert job.notification["brief_kind"] == "morning" and job.notification["major_event_keys"]


def test_major_event_enablement_does_not_replay_earlier_news(store, monkeypatch):
    start = NOW.replace(hour=8, minute=0)
    monkeypatch.setattr(settings, "gold_major_event_alerts_since", start)
    provider = Provider()
    with store() as db:
        asyncio.run(gold.refresh(db, current=start - timedelta(hours=1), provider=provider, notify=True))
        provider.headlines = [headline("old-fed", start - timedelta(minutes=1), title="Gold gains after Fed cuts interest rates")]
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=15), provider=provider, notify=True))["queued"] == 0
        provider.headlines.append(headline("new-fed", start + timedelta(minutes=20), title="Federal Reserve issues FOMC statement", source="Federal Reserve"))
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=30), provider=provider, notify=True))["queued"] == 1
        job = db.scalar(select(TelegramDelivery))
        assert "Federal Reserve issues FOMC statement" in job.message
        assert "Gold gains after Fed cuts interest rates" not in job.message


def test_distinct_major_events_are_batched_without_marking_unshown_items(store):
    start = NOW.replace(hour=8, minute=0)
    provider = Provider()
    with store() as db:
        asyncio.run(gold.refresh(db, current=start, provider=provider, notify=True))
        provider.headlines = [headline("fed", start + timedelta(minutes=1), title="Gold gains after Fed cuts interest rates"),
            headline("cpi", start + timedelta(minutes=2), title="Gold falls after US CPI rises above expectations"),
            headline("war", start + timedelta(minutes=3), title="Gold rises after US launches airstrikes on Iran")]
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=15), provider=provider, notify=True))["queued"] == 1
        assert sum(row.alerted_at is not None for row in db.scalars(select(GoldHeadline))) == 2
        assert asyncio.run(gold.refresh(db, current=start + timedelta(minutes=30), provider=provider, notify=True))["queued"] == 1
        assert all(row.alerted_at is not None for row in db.scalars(select(GoldHeadline)))
    assert not gold.major_keys_overlap("CONFLICT_ESCALATION:iran", "CONFLICT_ESCALATION:russia,ukraine")
    assert not gold.major_keys_overlap("CEASEFIRE_AGREEMENT:israel", "CEASEFIRE_BREAKDOWN:israel")


def test_pausing_major_events_supersedes_a_pending_major_alert(store, monkeypatch):
    start = NOW.replace(hour=8, minute=0)
    provider = Provider()
    with store() as db:
        asyncio.run(gold.refresh(db, current=start, provider=provider, notify=True))
        provider.headlines = [headline("fed", start + timedelta(minutes=5), title="Gold gains after Fed cuts interest rates")]
        asyncio.run(gold.refresh(db, current=start + timedelta(minutes=15), provider=provider, notify=True))
    monkeypatch.setattr(settings, "gold_major_event_alerts_enabled", False)
    monkeypatch.setattr(telegram_alerts, "now", lambda: gold.utc(start + timedelta(minutes=16)))
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    with store() as db:
        assert db.scalar(select(TelegramDelivery)).status == "SUPERSEDED"


def test_source_failure_retains_last_quote_and_exposes_staleness(store):
    provider = Provider()
    start = NOW - timedelta(hours=2)
    with store() as db:
        asyncio.run(gold.refresh(db, current=start, provider=provider))
        provider.error = True
        result = asyncio.run(gold.refresh(db, current=NOW, provider=provider, notify=True))
        assert result["status"] == "FAILED"
        view = gold.dashboard(db, current=NOW)
        assert view["prices"]["global"]["usd_per_oz"] == "4000"
        assert view["prices"]["global"]["freshness"] == "STALE"
        assert view["status"]["errors"] == [{"source": "Gold monitor", "reason": "REFRESH_FAILED"}]
        assert len(list(db.scalars(select(TelegramDelivery)))) == 0
        assert len(list(db.scalars(select(GoldSnapshot)))) == 1


def test_telegram_worker_sends_gold_without_company_event(store, monkeypatch):
    sent = []
    async def send(chat, message, **options):
        sent.append((chat, message, options))
        return 123
    monkeypatch.setattr(telegram_alerts, "send_message", send)
    with store() as db:
        asyncio.run(gold.refresh(db, current=NOW, provider=Provider(), notify=True))
    assert asyncio.run(telegram_alerts.deliver_one(store))
    with store() as db:
        job = db.scalar(select(TelegramDelivery))
        assert job.status == "SENT" and job.message_ids == [123]
    assert sent[0][0] == "456" and sent[0][2] == {"parse_mode": "HTML"}
    assert not asyncio.run(telegram_alerts.deliver_one(store))


def test_paused_monitor_supersedes_pending_brief(store, monkeypatch):
    with store() as db:
        asyncio.run(gold.refresh(db, current=NOW, provider=Provider(), notify=True))
    monkeypatch.setattr(settings, "gold_monitor_enabled", False)
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    with store() as db:
        assert db.scalar(select(TelegramDelivery)).status == "SUPERSEDED"


def test_gold_api_is_independent_of_portfolio(client, monkeypatch):
    monkeypatch.setattr(gold, "GoldProvider", lambda: Provider())
    response = client.post("/api/gold/refresh")
    assert response.status_code == 200 and response.json()["queued"] == 0
    view = client.get("/api/gold").json()
    assert view["prices"]["india"]["city"] == "Hyderabad"
    latest = client.get("/api/gold?version=2").json()
    assert latest["api_version"] == 2
    assert latest["prices"]["india"]["city"] == "India"
    assert latest["prices"]["hyderabad"]["city"] == "Hyderabad"
    assert latest["prices"]["india"]["inr_24k_per_g"] != latest["prices"]["hyderabad"]["inr_24k_per_g"]
    assert [slot["time"] for slot in view["status"]["schedule"]] == ["11:00", "17:00"]
    assert client.get("/api/companies").json() == []


def test_schedule_configuration_rejects_ambiguous_or_duplicate_times():
    with pytest.raises(ValueError):
        Settings(gold_alert_times_ist="11:00,11:00", _env_file=None)
    with pytest.raises(ValueError):
        Settings(gold_alert_times_ist="25:00,17:00", _env_file=None)
    with pytest.raises(ValueError):
        Settings(gold_alert_times_ist="10:45,13:00,21:00", _env_file=None)


def test_daily_brief_does_not_duplicate_its_material_headline(store):
    with store() as db:
        asyncio.run(gold.refresh(db, current=NOW - timedelta(hours=1), provider=Provider()))
        provider = Provider(headlines=[headline(published=NOW - timedelta(minutes=5))])
        result = asyncio.run(gold.refresh(db, current=NOW, provider=provider, notify=True))
        assert result["queued"] == 1
        job = db.scalar(select(TelegramDelivery))
        assert job.notification["brief_kind"] == "morning"
        assert "Gold rises as the dollar weakens" in job.message
        assert len(job.message) < 2500


def test_material_news_remains_on_dashboard_without_extra_telegram_messages(store):
    start = NOW.replace(hour=8, minute=0)
    with store() as db:
        asyncio.run(gold.refresh(db, current=start, provider=Provider()))
        provider = Provider(headlines=[headline(str(i), start + timedelta(minutes=i + 1), title=f"Demo gold policy update {i}") for i in range(3)])
        asyncio.run(gold.refresh(db, current=start + timedelta(minutes=15), provider=provider, notify=True))
        rows = list(db.scalars(select(GoldHeadline)))
        assert all(row.alerted_at is None for row in rows)
        asyncio.run(gold.refresh(db, current=start + timedelta(minutes=30), provider=provider, notify=True))
        assert all(row.alerted_at is None for row in rows)
        assert len(list(db.scalars(select(TelegramDelivery)))) == 0
        assert len(gold.dashboard(db, current=start + timedelta(minutes=30))["headlines"]) == 3


def test_migration_creates_and_removes_only_gold_tables(tmp_path):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0027_gold_alerts.py"
    spec = importlib.util.spec_from_file_location("gold_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert set(inspect(connection).get_table_names()) == {"gold_monitor_state", "gold_snapshots", "gold_headlines"}
            migration.downgrade()
            assert inspect(connection).get_table_names() == []
    engine.dispose()


def test_india_and_hyderabad_keep_independent_prices_and_dates():
    hyd = {"date": "2026-11-02", "priceLocation": "Hyderabad", "price": {"TWENTY_FOUR": 12000, "TWENTY_TWO": 11000}}
    national = {"date": "2026-10-31", "priceLocation": "India", "price": {"TWENTY_FOUR": 12200, "TWENTY_TWO": 11200}}
    html = '<script id="__NEXT_DATA__">' + json.dumps({"props": {"pageProps": {"goldRateData": {
        "physicalGoldRate": {"india": national, "hyderabad": hyd}}}}}) + '</script>'
    india_quote, hyd_quote = parse_india(html, NOW, "india"), parse_india(html, NOW, "hyderabad")
    view = gold.prices_view({"india": india_quote.model_dump(mode="json"), "hyderabad": hyd_quote.model_dump(mode="json")}, NOW)
    assert view["india"]["city"] == "India" and view["india"]["freshness"] == "STALE"
    assert view["hyderabad"]["city"] == "Hyderabad" and view["hyderabad"]["freshness"] == "CURRENT"
    assert view["india"]["inr_24k_per_g"] == "12200"


def test_one_invalid_location_does_not_discard_the_other_location():
    locations = {
        "india": {"date": "2026-11-02", "priceLocation": "Delhi", "price": {"TWENTY_FOUR": 12200, "TWENTY_TWO": 11200}},
        "hyderabad": {"date": "2026-11-02", "priceLocation": "Hyderabad", "price": {"TWENTY_FOUR": 12000, "TWENTY_TWO": 11000}},
    }
    html = '<script id="__NEXT_DATA__">' + json.dumps({"props": {"pageProps": {"goldRateData": {"physicalGoldRate": locations}}}}) + '</script>'
    pages = []
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /")
        if request.url.host == "api.gold-api.com":
            if request.url.path.endswith("/INR"):
                return httpx.Response(200, json={"symbol": "XAU", "currency": "INR", "price": 360000,
                                                  "exchangeRate": 90, "updatedAt": NOW.isoformat()})
            return httpx.Response(200, json={"symbol": "XAU", "currency": "USD", "price": 4000, "updatedAt": NOW.isoformat()})
        if request.url.host == "groww.in":
            pages.append(str(request.url))
            return httpx.Response(200, text=html)
        return httpx.Response(200, text="<rss><channel/></rss>")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GoldProvider(client).fetch(NOW)
    result = asyncio.run(run())
    assert result.india_quote is None and result.hyderabad_quote.inr_24k_per_g == 12000
    assert len(pages) == 1
    assert result.errors == [{"source": "India gold", "reason": "INDIA_GOLD_LOCATION_UNVERIFIED"}]


def test_old_hyderabad_snapshot_is_never_relabelled_as_national(store):
    data = quotes()
    old_prices = {"global": data.global_quote.model_dump(mode="json"), "india": data.hyderabad_quote.model_dump(mode="json")}
    with store() as db:
        db.add(GoldSnapshot(fetched_at=gold.utc(NOW), prices=old_prices, analysis={}, errors=[]))
        db.commit()
        view = gold.dashboard(db, current=NOW)
        assert view["prices"]["india"] is None
        assert view["prices"]["hyderabad"]["city"] == "Hyderabad"
        assert view["history"][0]["prices"]["india"] is None
        assert view["history"][0]["prices"]["hyderabad"]["inr_24k_per_g"] == "12000"


def test_reasoning_explains_mechanism_and_a_conditional_next_step():
    from types import SimpleNamespace
    row = SimpleNamespace(id=1, **headline(title="Gold falls as Fed hawks press on").model_dump())
    prices = gold.prices_view({"global": quotes().global_quote.model_dump(mode="json"),
        "india": quotes().india_quote.model_dump(mode="json"), "hyderabad": quotes().hyderabad_quote.model_dump(mode="json")}, NOW)
    result = build_analysis(prices, [row])
    assert result["bias"] == "NEGATIVE"
    assert "Gold pays no interest" in result["drivers"][0]["reason"]
    assert "if" in result["what_next"].lower() and "could" in result["what_next"].lower()
    assert "measured their separate effects" in result["local_context"]["india"]


def test_reasoning_does_not_treat_inflation_or_hypotheses_as_a_sure_direction():
    from types import SimpleNamespace
    inflation = SimpleNamespace(id=1, **headline(title="Gold holds steady as inflation data arrives").model_dump())
    result = build_analysis({"global": None, "india": None, "hyderabad": None}, [inflation])
    assert result["bias"] == "UNCLEAR"
    assert "also hurt gold" in result["drivers"][0]["reason"]
    hypothetical = SimpleNamespace(id=2, **headline(title="Gold could fall if Fed hikes interest rates", summary="").model_dump())
    reading = explain_headline(hypothetical)
    assert reading["impact"] == "UNCLEAR" and reading["rules"] == []


def test_conflicting_driver_and_observed_move_are_explained_as_mixed():
    from types import SimpleNamespace
    row = SimpleNamespace(id=1, **headline(title="Gold falls despite a weaker US dollar").model_dump())
    assert explain_headline(row)["impact"] == "MIXED"
    result = build_analysis({"global": None, "india": None, "hyderabad": None}, [row])
    assert result["bias"] == "MIXED" and "conflicting" in result["summary"]


def test_failed_ceasefire_and_negated_rate_cut_are_not_read_backwards():
    from types import SimpleNamespace
    failed = SimpleNamespace(id=1, **headline(title="Gold rises as ceasefire collapses", summary="").model_dump())
    assert explain_headline(failed)["impact"] == "POSITIVE"
    assert all(rule.key != "haven_easing" for rule in explain_headline(failed)["rules"])
    negated = SimpleNamespace(id=2, **headline(title="Gold steady: there are no hopes for rate cuts", summary="").model_dump())
    assert all(rule.key != "rates_down" for rule in explain_headline(negated)["rules"])
