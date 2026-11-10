"""Telegram holdings section, reaction follow-up replies and big-move alerts; no live sends."""

import asyncio
import json
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.market.nse_mcp import NSEMCPClient, set_nse_mcp
from app.models.company import Company
from app.models.event import CompanyEvent, DataOrigin, EventType, ProcessingStatus, PublicationPrecision
from app.models.market_brief import MarketBrief
from app.models.market_events import EventPriceReaction
from app.models.telegram import TelegramDelivery
from app.models.transaction import Transaction, TransactionType
from app.services import holdings_alerts, market_briefs as briefs, telegram_alerts
from app.services.gold_sources import IST
from app.services.telegram_client import send_message
from app.services.telegram_formatter import units
from tests.test_market_briefs import AI, MORNING, Provider, run
from tests.test_nse_mcp import FakeNSE

TODAY = datetime.now(IST).date()


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'alerts.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(settings, "market_briefs_enabled", True)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:SECRET"))
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    monkeypatch.setattr(settings, "nse_mcp_enabled", True)
    yield factory
    engine.dispose()


@pytest.fixture
def nse():
    server = FakeNSE()
    set_nse_mcp(NSEMCPClient(transport=httpx.MockTransport(server.handler)))
    yield server
    set_nse_mcp(None)


def hold(db, symbol, quantity="10", price="1000"):
    company = Company(symbol=symbol, name=f"{symbol} Ltd", exchange="NSE")
    db.add(company)
    db.flush()
    db.add(Transaction(company_id=company.id, transaction_type=TransactionType.BUY, quantity=Decimal(quantity),
                       price=Decimal(price), transaction_date=date(2026, 1, 5), charges=Decimal(0)))
    db.commit()
    return company


def live(symbol, ltp, prev, day, *, high=2000.0, low=500.0, volume=1000):
    return {"stock": {"symbol": symbol, "series": "EQ", "openPrice": prev, "highPrice": max(ltp, prev),
                      "lowPrice": min(ltp, prev), "preClosePrice": prev, "lastTradedPrice": ltp,
                      "perChange": round((ltp / prev - 1) * 100, 2), "volume": volume,
                      "fiftyTwoWeekHigh": high, "fiftyTwoWeekLow": low,
                      "latestTimestamp": f"{day.isoformat()} 14:05:00"}}


# -- 1. closing brief ------------------------------------------------------------

def test_closing_brief_adds_holdings_section_after_analysis(store, nse):
    day = MORNING.date()
    with store() as db:
        esds = hold(db, "ESDS")
        hold(db, "TCS", quantity="5", price="2000")
        db.add(CompanyEvent(company_id=esds.id, event_type=EventType.ORDER_WIN, title="ESDS wins data-centre order",
                            published_at=datetime(2026, 11, 2, 11, 0, tzinfo=IST), source="NSE",
                            source_url="https://example.com/o", data_origin=DataOrigin.LIVE,
                            publication_precision=PublicationPrecision.TIMESTAMP,
                            processing_status=ProcessingStatus.PROCESSED, fingerprint="order"))
        db.commit()
    quotes = {"ESDS": live("ESDS", 1100.0, 1000.0, day, high=1102.0, volume=9000),
              "TCS": live("TCS", 1980.0, 2000.0, day, volume=1000)}
    nse.on("live", "cm_get_stock_quote", lambda args: quotes[args["symbol"]])
    nse.on("bhavcopy", "get_volume_analysis", {"avg_volume": 3000})
    nse.on("bhavcopy", "get_stock_vs_index", None)

    ai = AI()
    run(store, provider=Provider(), ai_provider=ai)
    run(store, MORNING.replace(hour=16, minute=0), provider=Provider(), ai_provider=ai)
    with store() as db:
        close = db.scalar(select(MarketBrief).where(MarketBrief.slot == "close"))
        holdings = close.evidence["holdings"]
        # The market analysis never saw personal positions.
        assert all("holdings" not in json.dumps(context) for context in ai.contexts)
        assert holdings["value"] == "20900.00" and holdings["day_change_value"] == "900.00"
        message = db.scalar(select(TelegramDelivery).where(
            TelegramDelivery.notification["slot"].as_string() == "close")).message
    assert "<b>Your holdings · NSE close</b>" in message
    assert "Value ₹20,900 · today +₹900.00 (+4.50%)" in message
    assert "Best ESDS +10.00% · Worst TCS −1.00%" in message
    assert "ESDS at its 52-week high (₹1,102)" in message
    assert "ESDS volume 3.0× its 30-session average · filing today: ESDS wins data-centre order" in message
    assert "TCS volume" not in message


def test_holdings_never_push_the_brief_over_the_telegram_limit(store, nse):
    nse.on("live", "cm_get_stock_quote", None)
    run(store, provider=Provider(), ai_provider=AI())
    run(store, MORNING.replace(hour=16, minute=0), provider=Provider(), ai_provider=AI())
    with store() as db:
        report = db.scalar(select(MarketBrief).where(MarketBrief.slot == "close"))
        a = deepcopy(report.analysis)
        for claim in [a["summary"], *a["drivers"], a["next_session"], a["longer_term"], a["invalidation"]]:
            claim["text"] = "<script> & " * 38
        report.analysis = a
        report.evidence = {**report.evidence, "holdings": {
            "as_of": "2026-11-02", "positions": 9, "priced": 9, "value": "123456.00", "day_change_value": "-1000.00",
            "day_change_pct": "-0.80", "best": {"symbol": "AAA", "change_pct": "1.00"},
            "worst": {"symbol": "BBB", "change_pct": "-2.00"},
            "flags": [{"symbol": f"S{i}", "type": "VOLUME_SPIKE", "ratio": "4.0", "filing": "x" * 90} for i in range(6)]}}
        message = briefs.format_message(report)
    assert units(message) <= 3800 and "Further ahead:" in message


# -- 2. reaction follow-up -------------------------------------------------------------

def results_with_alert(db, company, *, sent=True):
    event = CompanyEvent(company_id=company.id, event_type=EventType.FINANCIAL_RESULTS, title="Q2 FY27 results",
                         published_at=datetime.now(timezone.utc) - timedelta(days=8), source="NSE",
                         source_url="https://example.com/r", data_origin=DataOrigin.LIVE,
                         publication_precision=PublicationPrecision.TIMESTAMP,
                         processing_status=ProcessingStatus.PROCESSED, fingerprint=f"res-{company.id}")
    db.add(event)
    db.flush()
    db.add(TelegramDelivery(event_id=event.id, chat_id="456", status="SENT" if sent else "FAILED",
                            next_attempt_at=datetime.now(timezone.utc), message="results", message_ids=[77] if sent else []))
    db.commit()
    return event


def reaction(db, event, window_date):
    row = EventPriceReaction(event_id=event.id, company_id=event.company_id, status="PENDING",
                             base_date=window_date - timedelta(days=7), base_close=Decimal("2000"), benchmark="Nifty 50",
                             volume_ratio=Decimal("2.6"), adjustments=[], attempts=1,
                             windows={"5": {"sessions": 5, "date": window_date.isoformat(), "close": "2200",
                                            "return_pct": "10.00", "index_return_pct": "1.00", "excess_return_pct": "9.00"}})
    db.add(row)
    db.flush()
    return row


def test_followup_replies_once_under_the_original_alert(store, monkeypatch):
    monkeypatch.setattr(holdings_alerts, "_daytime", lambda current: current)
    with store() as db:
        tcs = hold(db, "TCS")
        event = results_with_alert(db, tcs)
        row = reaction(db, event, TODAY - timedelta(days=1))
        assert holdings_alerts.queue_reaction_followup(db, event, row)
        assert not holdings_alerts.queue_reaction_followup(db, event, row)  # one per filing
        db.commit()
    sent = []

    async def fake_send(chat_id, message, **kwargs):
        sent.append((chat_id, message, kwargs))
        return 88

    monkeypatch.setattr(telegram_alerts, "send_message", fake_send)
    assert asyncio.run(telegram_alerts.deliver_one(store))
    chat, message, kwargs = sent[0]
    assert kwargs == {"parse_mode": "HTML", "reply_to_message_id": 77}
    assert "<b>ALMONDS · TCS since results</b>" in message
    assert "TCS +10.00% · Nifty 50 +1.00% → <b>+9.00 pts</b> vs the market" in message
    assert "What the market did, not why." in message


def test_followup_skips_backfill_unheld_companies_and_unsent_alerts(store, monkeypatch):
    monkeypatch.setattr(holdings_alerts, "_daytime", lambda current: current)
    with store() as db:
        held = hold(db, "TCS")
        old = reaction(db, results_with_alert(db, held), TODAY - timedelta(days=30))
        assert not holdings_alerts.queue_reaction_followup(db, db.get(CompanyEvent, old.event_id), old)

        watched = Company(symbol="INFY", name="Infosys", exchange="NSE")
        db.add(watched)
        db.flush()
        event = results_with_alert(db, watched)
        assert not holdings_alerts.queue_reaction_followup(db, event, reaction(db, event, TODAY))

        other = hold(db, "WIPRO")
        unsent = results_with_alert(db, other, sent=False)
        assert not holdings_alerts.queue_reaction_followup(db, unsent, reaction(db, unsent, TODAY))

        monkeypatch.setattr(settings, "telegram_reaction_followups", False)
        fresh = results_with_alert(db, hold(db, "HCL"))
        assert not holdings_alerts.queue_reaction_followup(db, fresh, reaction(db, fresh, TODAY))
        assert db.scalar(select(TelegramDelivery).where(TelegramDelivery.version == holdings_alerts.VERSION)) is None


def test_overnight_followups_wait_for_morning():
    night = datetime(2026, 11, 10, 23, 30, tzinfo=IST)
    assert holdings_alerts._daytime(night).astimezone(IST) == datetime(2026, 11, 11, 8, 30, tzinfo=IST)
    early = datetime(2026, 11, 10, 3, 0, tzinfo=IST)
    assert holdings_alerts._daytime(early).astimezone(IST) == datetime(2026, 11, 10, 8, 30, tzinfo=IST)
    noon = datetime(2026, 11, 10, 12, 0, tzinfo=IST)
    assert holdings_alerts._daytime(noon) == noon


# -- 3. big moves -------------------------------------------------------------------------

def test_big_move_alert_is_off_by_default_then_once_per_direction_per_day(store, nse, monkeypatch):
    assert asyncio.run(holdings_alerts.run_big_moves(store)) == {"status": "OFF"}
    monkeypatch.setattr(settings, "telegram_big_move_alerts", True)
    monkeypatch.setattr(holdings_alerts, "market_session", lambda: {"state": "OPEN"})
    with store() as db:
        hold(db, "ESDS")
        hold(db, "TCS")
    stamp = TODAY.strftime("%d-%b-%Y") + " 14:05:00"
    nse.on("live", "cm_get_index_quote", {"nseTimestamp": stamp, "index": {"indexName": "NIFTY 50", "percChange": 0.3}})
    quotes = {"ESDS": live("ESDS", 915.0, 1000.0, TODAY, volume=9000), "TCS": live("TCS", 1010.0, 1000.0, TODAY)}
    nse.on("live", "cm_get_stock_quote", lambda args: quotes[args["symbol"]])
    nse.on("bhavcopy", "get_volume_analysis", {"avg_volume": 3000})

    assert asyncio.run(holdings_alerts.run_big_moves(store))["queued"] == 1
    get_nse = holdings_alerts.get_nse_mcp()
    get_nse.clear_cache()
    assert asyncio.run(holdings_alerts.run_big_moves(store))["queued"] == 0  # already alerted today
    with store() as db:
        job = db.scalar(select(TelegramDelivery))
    assert job.notification["kind"] == "HOLDING_MOVE" and job.notification["direction"] == "DOWN"
    assert "<b>ALMONDS · ESDS −8.50% today</b>" in job.message
    assert "Nifty 50 +0.30% → <b>−8.80 pts</b> vs the market" in job.message
    assert "volume 3.0× its 30-session average" in job.message
    assert "No company filing on record today." in job.message

    # A stale index (holiday or delayed feed) never triggers alerts.
    nse.on("live", "cm_get_index_quote", {"nseTimestamp": "01-Jan-2020 15:30:00", "index": {"percChange": 0.3}})
    get_nse.clear_cache()
    assert asyncio.run(holdings_alerts.run_big_moves(store))["status"] == "STALE_SESSION"


def test_expired_or_disabled_holding_alerts_are_superseded(store, monkeypatch):
    with store() as db:
        db.add(TelegramDelivery(chat_id="456", status="PENDING", next_attempt_at=datetime.now(timezone.utc),
                                version=holdings_alerts.VERSION, message="m", message_parts=["m"], message_ids=[],
                                notification={"kind": "HOLDING_MOVE", "expires_at":
                                              (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}))
        db.commit()

    async def never(*args, **kwargs):
        raise AssertionError("must not send")

    monkeypatch.setattr(telegram_alerts, "send_message", never)
    monkeypatch.setattr(settings, "telegram_big_move_alerts", True)
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    with store() as db:
        job = db.scalar(select(TelegramDelivery))
        assert job.status == "SUPERSEDED" and job.last_error == "Alert delivery window expired"


def test_send_message_threads_replies(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:SECRET"))
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    receipt = asyncio.run(send_message("456", "hi", transport=httpx.MockTransport(handler), reply_to_message_id=77))
    assert receipt == 5
    assert seen[0]["reply_parameters"] == {"message_id": 77, "allow_sending_without_reply": True}
    asyncio.run(send_message("456", "hi", transport=httpx.MockTransport(handler)))
    assert "reply_parameters" not in seen[1]
