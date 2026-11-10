"""Share-basis adjustment from NSE corporate actions, and event price reactions."""

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.main import app
from app.market.nse_mcp import NSEMCPClient, set_nse_mcp
from app.market.valuation import calculate_valuation, recalculate, valuation_change
from app.models.event import CompanyEvent, DataOrigin, EventType, ProcessingStatus, PublicationPrecision
from app.models.financial import FinancialScope
from app.models.market_events import CorporateAction, CorporateActionSync, EventPriceReaction
from app.services.corporate_actions import ShareAction, ShareBasis, share_basis, sync_company
from app.services.price_reactions import Bar, compute_reaction, refresh_company
from tests.test_market_valuation import add_quarter, company, market_db, quote  # noqa: F401
from tests.test_nse_mcp import FakeNSE

IST = ZoneInfo("Asia/Kolkata")
BONUS_EX = date(2026, 8, 20)


def confirm_basis(db, item, actions=((BONUS_EX, "0.5", "Bonus 1:1"),), synced=datetime(2026, 9, 29, tzinfo=timezone.utc)):
    for ex_date, factor, purpose in actions:
        db.add(CorporateAction(company_id=item.id, ex_date=ex_date, action_type="BONUS", purpose=purpose,
                               adjustment_factor=Decimal(factor), source="NSE_MCP", source_url="https://mcp.nseindia.in"))
    db.add(CorporateActionSync(company_id=item.id, from_date=date(2021, 10, 1), to_date=date(2027, 3, 1),
                               synced_at=synced, complete=True))
    db.commit()


def pre_bonus_quarters(db, item, last_published=date(2026, 7, 1), last_eps="1"):
    ends = (date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30))
    published = (date(2025, 10, 1), date(2026, 1, 1), date(2026, 4, 1), last_published)
    return [add_quarter(db, item, end, pub, eps=eps) for end, pub, eps in
            zip(ends, published, ("1", "1", "1", last_eps))]


# -- share basis --------------------------------------------------------------

def test_share_basis_restates_per_share_values_both_directions():
    basis = ShareBasis(date(2021, 1, 1), date(2027, 1, 1), [
        ShareAction(date(2022, 7, 28), Decimal("0.1"), "Split 10:1"),
        ShareAction(date(2024, 10, 28), Decimal("0.5"), "Bonus 1:1"),
    ])
    assert basis.factor_between(date(2022, 1, 1), date(2025, 1, 1)) == Decimal("0.05")
    assert basis.factor_between(date(2025, 1, 1), date(2022, 1, 1)) == Decimal("20")
    assert basis.factor_between(date(2023, 1, 1), date(2024, 10, 27)) == 1
    assert basis.factor_between(date(2023, 1, 1), date(2024, 10, 28)) == Decimal("0.5")
    assert [a.label for a in basis.between(date(2022, 1, 1), date(2023, 1, 1))] == ["Split 10:1"]
    assert not basis.covers(date(2020, 12, 31), date(2025, 1, 1))


@pytest.fixture
def fake_nse(monkeypatch):
    monkeypatch.setattr(settings, "nse_mcp_enabled", True)
    server = FakeNSE()
    set_nse_mcp(NSEMCPClient(transport=httpx.MockTransport(server.handler)))
    yield server
    set_nse_mcp(None)


def test_sync_stores_actions_and_refuses_unquantified_splits(market_db, fake_nse):
    item = company(market_db)
    fake_nse.on("bhavcopy", "get_corporate_actions", {"actions": [
        {"exDate": "2024-10-28", "actionType": "BONUS", "purpose": "Bonus 1:1", "adjustmentFactor": 0.5},
        {"exDate": "2025-08-14", "actionType": "DIVIDEND", "purpose": "Dividend - Rs 5.5 Per Share", "adjustmentFactor": 1.0},
    ]})
    sync = asyncio.run(sync_company(market_db, item))
    assert sync.complete and sync.last_error is None
    basis = share_basis(market_db, item.id)
    assert [(a.ex_date, a.factor) for a in basis.actions] == [(date(2024, 10, 28), Decimal("0.5"))]
    # A second call within 20 hours is served from the stored sync.
    asyncio.run(sync_company(market_db, item))
    assert len(fake_nse.calls) == 1

    fake_nse.on("bhavcopy", "get_corporate_actions", {"actions": [
        {"exDate": "2026-01-05", "actionType": "SPLIT", "purpose": "Face value split", "adjustmentFactor": None}]})
    sync = asyncio.run(sync_company(market_db, item, force=True))
    assert not sync.complete and sync.last_error == "SPLIT_OR_BONUS_WITHOUT_FACTOR"
    assert share_basis(market_db, item.id) is None

    fake_nse.on("bhavcopy", "get_corporate_actions", {"actions": [], "warning": "partial list"})
    assert not asyncio.run(sync_company(market_db, item, force=True)).complete


# -- valuation -------------------------------------------------------------------

def test_pe_after_bonus_uses_market_date_share_basis(market_db):
    item = company(market_db)
    pre_bonus_quarters(market_db, item)
    market = quote(market_db, item, close_price="50")  # 28 Sep 2026, after the 1:1 bonus

    # Without confirmed corporate actions the stale pre-bonus EPS halves the P/E.
    _, unconfirmed = calculate_valuation(market_db, market, FinancialScope.CONSOLIDATED)
    assert Decimal(unconfirmed["pe_ttm"]["value"]) == Decimal("12.5")

    confirm_basis(market_db, item)
    _, details = calculate_valuation(market_db, market, FinancialScope.CONSOLIDATED)
    assert Decimal(details["ttm_eps"]["value"]) == 2
    assert Decimal(details["pe_ttm"]["value"]) == 25
    assert "restated to the 2026-09-28 share basis" in details["ttm_eps"]["formula"]
    assert "Bonus 1:1 (ex 2026-08-20, factor 0.5)" in details["ttm_eps"]["formula"]


def test_mixed_filing_bases_are_reconciled_only_with_confirmed_actions(market_db):
    item = company(market_db)
    # The June quarter is filed after the bonus, already on the new basis.
    pre_bonus_quarters(market_db, item, last_published=date(2026, 8, 25), last_eps="0.5")
    market = quote(market_db, item, close_price="50")
    _, mixed = calculate_valuation(market_db, market, FinancialScope.CONSOLIDATED)
    assert mixed["ttm_eps"]["status"] == "NEEDS_REVIEW"  # implied share counts disagree

    confirm_basis(market_db, item)
    _, details = calculate_valuation(market_db, market, FinancialScope.CONSOLIDATED)
    assert Decimal(details["ttm_eps"]["value"]) == 2 and Decimal(details["pe_ttm"]["value"]) == 25


def test_split_event_without_sync_keeps_needs_review(market_db):
    item = company(market_db)
    pre_bonus_quarters(market_db, item)
    market_db.add(CompanyEvent(
        company_id=item.id, event_type=EventType.BONUS, title="Bonus issue 1:1",
        published_at=datetime(2026, 7, 10, tzinfo=timezone.utc), source="TEST",
        source_url="https://example.com/bonus", data_origin=DataOrigin.MANUAL,
        publication_precision=PublicationPrecision.DATE, processing_status=ProcessingStatus.PROCESSED,
        fingerprint="bonus-event"))
    market_db.commit()
    market = quote(market_db, item, close_price="50")
    _, details = calculate_valuation(market_db, market, FinancialScope.CONSOLIDATED)
    assert details["ttm_eps"]["status"] == "NEEDS_REVIEW"
    confirm_basis(market_db, item)
    _, details = calculate_valuation(market_db, market, FinancialScope.CONSOLIDATED)
    assert Decimal(details["pe_ttm"]["value"]) == 25


def test_valuation_change_and_price_history_cross_the_bonus(market_db):
    item = company(market_db)
    pre_bonus_quarters(market_db, item)
    confirm_basis(market_db, item)
    before = quote(market_db, item, day=date(2026, 8, 10), close_price="100")
    after = quote(market_db, item, close_price="50")
    old = recalculate(market_db, before, FinancialScope.CONSOLIDATED)
    new = recalculate(market_db, after, FinancialScope.CONSOLIDATED)
    change = valuation_change(old, new, before, after, share_basis(market_db, item.id))
    assert Decimal(change["price_change_pct"]) == 0 and Decimal(change["eps_change_pct"]) == 0
    assert change["share_basis_adjustment"]["raw_old_price"].startswith("100")

    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            rows = client.get(f"/api/companies/{item.symbol}/market/history").json()
            by_date = {row["as_of_date"]: row for row in rows}
            assert Decimal(by_date["2026-08-10"]["adjusted_close"]) == 50
            assert Decimal(by_date["2026-09-28"]["adjusted_close"]) == 50
            actions = client.get(f"/api/companies/{item.symbol}/corporate-actions").json()
            assert actions["sync"]["complete"] and actions["actions"][0]["affects_share_basis"]
    finally:
        app.dependency_overrides.pop(get_db, None)


# -- price reactions -----------------------------------------------------------

def weekdays(start: date, end: date):
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


EVENT_DAY = date(2026, 8, 3)  # Monday


def stock_close(day: date) -> Decimal:
    if day < EVENT_DAY:
        return Decimal("100")
    return Decimal("110") if day < BONUS_EX else Decimal("55")


def event_at(item, when: datetime, precision=PublicationPrecision.TIMESTAMP, kind=EventType.FINANCIAL_RESULTS):
    return CompanyEvent(
        id=None, company_id=item.id, event_type=kind, title="Q1 results",
        published_at=when, source="NSE", source_url="https://example.com/r",
        data_origin=DataOrigin.LIVE, publication_precision=precision,
        processing_status=ProcessingStatus.PROCESSED, fingerprint=f"evt-{when.isoformat()}-{kind.value}",
    )


def bars_between(start, end):
    return {d: Bar(stock_close(d), 5000 if d == EVENT_DAY else 1000) for d in weekdays(start, end)}


def basis():
    return ShareBasis(date(2021, 1, 1), date(2027, 1, 1), [ShareAction(BONUS_EX, Decimal("0.5"), "Bonus 1:1")])


def test_reaction_windows_timing_bonus_and_excess_return():
    bars = bars_between(date(2026, 6, 1), date(2026, 10, 30))
    index = {d: Decimal("1000") if d < EVENT_DAY else Decimal("1010") for d in bars}
    during = event_at(type("C", (), {"id": 1})(), datetime(2026, 8, 3, 10, 0, tzinfo=IST))
    result = compute_reaction(during, bars, index, basis(), date(2026, 11, 10))
    assert result["status"] == "COMPLETE" and result["timing"] == "DURING_OR_BEFORE_SESSION"
    assert result["base_date"] == date(2026, 7, 31) and result["reaction_date"] == EVENT_DAY
    windows = result["windows"]
    assert windows["1"]["return_pct"] == "10.00" and windows["1"]["excess_return_pct"] == "9.00"
    # The 20-session window crosses the bonus ex-date: 55 / (100 × 0.5) − 1 = +10%.
    assert windows["20"]["return_pct"] == "10.00"
    assert result["adjustments"][0]["ex_date"] == "2026-08-20"
    assert result["volume_ratio"] == Decimal("5.00")

    after_close = event_at(type("C", (), {"id": 1})(), datetime(2026, 7, 31, 16, 30, tzinfo=IST))
    assert compute_reaction(after_close, bars, index, basis(), date(2026, 11, 10))["base_date"] == date(2026, 7, 31)
    date_only = event_at(type("C", (), {"id": 1})(), datetime(2026, 8, 3, 0, 0, tzinfo=IST), PublicationPrecision.DATE)
    result = compute_reaction(date_only, bars, index, basis(), date(2026, 11, 10))
    assert result["timing"] == "DATE_ONLY" and result["base_date"] == date(2026, 7, 31)

    recent = compute_reaction(during, bars_between(date(2026, 6, 1), date(2026, 8, 6)), index, basis(), date(2026, 8, 7))
    assert recent["status"] == "PENDING" and set(recent["windows"]) == {"1"}
    unconfirmed = compute_reaction(during, bars, index, None, date(2026, 11, 10))
    assert unconfirmed["status"] == "UNAVAILABLE" and unconfirmed["reason"] == "SHARE_BASIS_UNCONFIRMED"
    no_index = compute_reaction(during, bars, {}, basis(), date(2026, 11, 10))
    assert no_index["windows"]["5"]["excess_return_pct"] is None
    assert no_index["reason"] == "NIFTY_HISTORY_UNAVAILABLE_FOR_DATES"


def test_refresh_measures_events_and_serves_summary(market_db, fake_nse):
    item = company(market_db, symbol="ESDS")
    market_db.add(event_at(item, datetime(2026, 8, 3, 10, 0, tzinfo=IST)))
    market_db.add(event_at(item, datetime(2026, 8, 3, 11, 0, tzinfo=IST), kind=EventType.SHAREHOLDING))
    market_db.commit()

    def stock_history(args):
        end = date.fromisoformat(args["endDate"]) if args["endDate"] != "today" else date.today()
        start = end - timedelta(days=91)
        rows = [{"symbol": "ESDS", "date": d.isoformat(), "close": float(stock_close(d)),
                 "volume": 5000 if d == EVENT_DAY else 1000}
                for d in weekdays(start + timedelta(days=1), min(end, date.today()))]
        return {"data": rows, "next_end_date": start.isoformat()}

    def index_history(args):
        end = date.fromisoformat(args["endDate"])
        start = end - timedelta(days=365)
        rows = [{"date": d.isoformat(), "close": 1000.0 if d < EVENT_DAY else 1010.0}
                for d in weekdays(max(start + timedelta(days=1), date(2024, 11, 11)), min(end, date.today()))]
        return {"data": rows, "next_end_date": start.isoformat()}

    fake_nse.on("bhavcopy", "get_stock_history", stock_history)
    fake_nse.on("bhavcopy", "get_index_history", index_history)
    fake_nse.on("bhavcopy", "get_corporate_actions", {"actions": [
        {"exDate": BONUS_EX.isoformat(), "actionType": "BONUS", "purpose": "Bonus 1:1", "adjustmentFactor": 0.5}]})

    result = asyncio.run(refresh_company(market_db, item))
    assert result["stored"] == 1  # SHAREHOLDING is not a material event type
    row = market_db.query(EventPriceReaction).one()
    assert row.status == "COMPLETE" and row.windows["20"]["excess_return_pct"] == "9.00"

    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            body = client.get("/api/companies/ESDS/price-reactions").json()
            assert body["summary_by_event_type"]["FINANCIAL_RESULTS"]["windows"]["5"] == {
                "observations": 1, "median_excess_return_pct": "9.00", "outperformed": 1}
            single = client.get(f"/api/events/{row.event_id}/price-reaction").json()
            assert single["windows"]["1"]["return_pct"] == "10.00"
            assert single["event"]["event_type"] == "FINANCIAL_RESULTS"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_reaction_becomes_quarterly_analyst_evidence(market_db):
    from app.ai.analyst.context_builder import _register_market_reaction
    from app.ai.analyst.validator import _check_numbers, InvalidAnalysis

    item = company(market_db)
    period = pre_bonus_quarters(market_db, item)[-1]
    event = market_db.get(CompanyEvent, period.source_event_id)
    market_db.add(EventPriceReaction(
        event_id=event.id, company_id=item.id, status="COMPLETE", base_date=date(2026, 6, 30),
        base_close=Decimal("100"), benchmark="Nifty 50", adjustments=[], attempts=1,
        windows={"5": {"sessions": 5, "date": "2026-07-07", "return_pct": "-6.40",
                       "index_return_pct": "0.50", "excess_return_pct": "-6.90"}}))
    market_db.commit()
    registry: dict = {}
    key = _register_market_reaction(market_db, registry, event)
    assert registry[key]["kind"] == "MARKET_REACTION"
    _check_numbers("The stock fell 6.4% in 5 sessions, 6.9 points behind the index.", registry, 2026, [key], "x")
    with pytest.raises(InvalidAnalysis):
        _check_numbers("The stock fell 12.3% in 5 sessions.", registry, 2026, [key], "x")
