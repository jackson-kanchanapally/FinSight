"""IPO discovery/financial/timing contracts using invented issuers and no network."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.models.ipo import IPOCheck, IPOIssue
from app.models.telegram import TelegramDelivery
from app.schemas.ipo import IPOData
from app.services import ipos, telegram_alerts
from app.services.ipo_sources import IST, InvestorGainProvider, parse_report, parse_dashboard, parse_company, short_date
from app.services.telegram_client import TelegramError

NOW = datetime(2026, 11, 2, 8, tzinfo=IST)  # Monday, deliberately fixed.


def issue(source_id="1", **changes):
    data = dict(source_id=source_id, name=f"Demo Issuer {source_id}",
        source_url=f"https://www.investorgain.com/ipo/demo-{source_id}/{source_id}/",
        opens_on=date(2026, 11, 6), closes_on=date(2026, 11, 10), lists_on=date(2026, 11, 13),
        sector="Software", price_low="95", price_high="100", issue_cr="500", fresh_cr="400", ofs_cr="100",
        lot_size=150, gmp="20", gmp_at=NOW, business="An invented software business for tests.",
        objects=["Product development", "Working capital"], annual_eps="5", annual_eps_period=date(2026, 3, 31),
        financials=[{"ended_on": "2026-06-30", "annual": False, "income_cr": "100", "pat_cr": "1"},
            {"ended_on": "2026-03-31", "annual": True, "income_cr": "1000", "pat_cr": "100", "debt_cr": "50", "net_worth_cr": "200"},
            {"ended_on": "2025-03-31", "annual": True, "income_cr": "800", "pat_cr": "80"}])
    data.update(changes)
    return IPOData(**data)


class Provider:
    def __init__(self, issues=None, error=False):
        self.issues, self.error, self.calls = issues or [], error, 0

    async def fetch(self, current):
        self.calls += 1
        if self.error:
            raise RuntimeError("private remote body must never be persisted")
        return self.issues


@pytest.fixture
def store(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'ipos.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(settings, "ipo_monitor_enabled", True)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:SECRET"))
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    monkeypatch.setattr(telegram_alerts, "now", lambda: ipos.utc(NOW))
    yield factory
    engine.dispose()


def test_financial_screen_uses_annual_periods_and_cap_price():
    i = issue()
    a = ipos.screen(i, NOW)
    assert a["status"] == "ATTRACTIVE"
    assert a["metrics"]["pat_cr_growth_pct"] == "25.00"
    assert a["metrics"]["pe"] == "20.00"
    assert ipos.gmp_pct(i) == Decimal("20.00")


@pytest.mark.parametrize("change,expected", [
    ({"gmp": None}, "INSUFFICIENT_DATA"), ({"gmp": "0"}, "WATCH"), ({"gmp": "-10"}, "WATCH"),
    ({"gmp_at": NOW - timedelta(hours=25)}, "WATCH"), ({"gmp_at": NOW + timedelta(hours=1)}, "WATCH"),
    ({"sector": None}, "INSUFFICIENT_DATA"), ({"fresh_cr": None}, "INSUFFICIENT_DATA"),
    ({"annual_eps": "1"}, "WATCH"), ({"annual_eps": "-2"}, "WATCH"),
    ({"annual_eps_period": date(2025, 3, 31)}, "INSUFFICIENT_DATA"),
    ({"ofs_cr": "200"}, "WATCH"), ({"opens_on": None}, "INSUFFICIENT_DATA"),
    ({"detail_error": "Source error"}, "INSUFFICIENT_DATA"),
])
def test_high_gmp_cannot_override_missing_or_weak_evidence(change, expected):
    assert ipos.screen(issue(**change), NOW)["status"] == expected


def test_declining_or_loss_making_annual_profit_rejected():
    i = issue(gmp="80")
    i.financials[1]["pat_cr"] = "-10"
    assert ipos.screen(i, NOW)["status"] == "WATCH"
    i.financials[1]["pat_cr"] = "70"
    assert "Annual pat declined" in ipos.screen(i, NOW)["reasons"]


def test_gmp_sort_numeric_with_zero_negative_and_unknown_last():
    values = [issue("1", gmp=None), issue("2", gmp="0"), issue("3", gmp="-1"),
              issue("4", gmp="9"), issue("5", gmp="100", price_high="200")]
    assert [i.source_id for i in ipos.ranked(values)] == ["5", "4", "2", "3", "1"]


def report_html():
    def row(key, badge, gmp):
        return f'''<tr><td data-label="Name"><a href="/gmp/demo-{key}/{key}/">Demo {key}</a><span class="badge">{badge}</span></td>
          <td data-label="GMP">₹<b>{gmp}</b> (99%)<br><b>0 ↓ / 8 ↑</b></td>
          <td data-label="Open">6-Nov<br>GMP: 10</td><td data-label="Close">10-Nov</td>
          <td data-label="Price (₹)">100</td><td data-label="IPO Size">₹500 Cr</td>
          <td data-label="Lot">150</td><td data-label="Updated-On">2-Nov 07:30</td></tr>'''
    return '<table id="reportTable"><thead><tr><th>Name</th><th>GMP</th></tr></thead><tbody>' + row("1", "IPO", "--") + row("2", "NSE SME", "80") + row("3", "FPO", "50") + row("4", "IPO", "-5") + '</tbody></table>'


def company_html():
    return '''<div class="ipo-hero-sub">Software | IPO | BSE, NSE</div><span class="ipo-acc-chip-val">Software</span>
    <table aria-label="IPO key information"><tr><td>Issue Size</td><td>₹500 Cr</td></tr><tr><td>Fresh Issue</td><td>₹500 Cr</td></tr><tr><td>Price Band</td><td>₹95-100 per share</td></tr></table>
    <div class="d-card"><h2>About Company</h2><p>Invented test business.</p></div>
    <table id="ObjectiveIssue"><tbody><tr><td>1</td><td>Working capital</td><td>500</td></tr></tbody></table>
    <table class="fin-table-new"><thead><tr><th>Period Ended</th><th>30 Jun 2026</th><th>31 Mar 2026</th><th>31 Mar 2025</th></tr></thead>
    <tbody><tr><td>Total Income</td><td>200</td><td>1,000</td><td>800</td></tr><tr><td>Profit After Tax</td><td>10</td><td>100</td><td>80</td></tr></tbody><tfoot><tr><td>Amount in ₹ Crore</td></tr></tfoot></table>
    <table class="kpi-table"><thead><tr><th>Metric</th><th>Pre IPO</th><th>Post IPO</th></tr></thead><tbody><tr><td>EPS (₹)</td><td>5</td><td>999</td></tr></tbody></table>
    <div class="kpi-disclaimer-content">Pre IPO EPS is based on pre-issue shareholding as on date of RHP and the latest FY earnings as of 2026-03-31.</div>'''


def test_real_source_shape_mainboard_only_and_missing_gmp():
    data = parse_report(report_html(), NOW.date())
    assert set(data) == {"1", "4"}
    assert data["1"].gmp is None
    assert data["4"].gmp == -5
    assert data["1"].opens_on == date(2026, 11, 6)
    assert data["1"].gmp_at == datetime(2026, 11, 2, 7, 30, tzinfo=IST)


def test_detail_parser_units_composition_and_annual_eps_basis():
    i = parse_company(company_html(), issue())
    assert i.fresh_cr == 500 and i.ofs_cr == 0
    assert i.price_high == 100
    assert i.annual_eps == 5 and i.annual_eps_period == date(2026, 3, 31)
    assert i.financials[0]["annual"] is False
    assert i.financials[1]["income_cr"] == "1000"
    assert i.business == "Invented test business."
    j = parse_company(company_html().replace("Amount in ₹ Crore", "Amount in ₹ Lakh"), issue(financials=[]))
    assert j.financials == []


def test_year_rollover_and_leap_day():
    assert short_date("2-Jan", date(2026, 12, 29)) == date(2027, 1, 2)
    assert short_date("31-Dec 23:15", date(2027, 1, 1), timestamp=True).year == 2026
    assert short_date("29-Feb", date(2028, 2, 28)) == date(2028, 2, 29)


def test_provider_obeys_robots_and_does_not_follow_redirects():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(Exception, match="ROBOTS_ACCESS_DISALLOWED"):
                await InvestorGainProvider(client).fetch(NOW)
    asyncio.run(run())
    assert calls == ["/robots.txt"]


def test_refresh_is_independent_of_holdings_and_idempotent(store):
    with store() as db:
        provider = Provider([issue("1", gmp="9"), issue("2", gmp="50"), issue("3", gmp="20"), issue("4", segment="SME")])
        first = asyncio.run(ipos.refresh(db, provider=provider, current=NOW))
        second = asyncio.run(ipos.refresh(db, provider=provider, current=NOW + timedelta(minutes=1)))
        assert first["stored"] == 3 and first["queued"] == 1
        assert second["queued"] == 0
        jobs = list(db.scalars(select(TelegramDelivery)))
        assert len(jobs) == 1 and jobs[0].event_id is None
        assert len(jobs[0].message_parts) == 2
        assert "Demo Issuer 2" in jobs[0].message_parts[0]
        assert "Demo Issuer 3" in jobs[0].message_parts[1]
        assert "Demo Issuer 1" not in "".join(jobs[0].message_parts)


def test_every_issue_is_fetched_before_preopen_filter_and_catchup_deduplicates(store):
    friday = NOW + timedelta(days=3)  # Thursday; opening Friday.
    provider = Provider([issue("1", gmp="40", gmp_at=friday), issue("2", gmp=None, gmp_at=friday)])
    with store() as db:
        asyncio.run(ipos.refresh(db, provider=provider, current=friday))
        jobs = list(db.scalars(select(TelegramDelivery)))
        assert len(jobs) == 2  # First weekly catch-up + qualifying preopen.
        assert len(list(db.scalars(select(IPOIssue)))) == 2
        friday_morning = friday + timedelta(days=1)
        provider.issues[0].gmp_at = friday_morning
        asyncio.run(ipos.refresh(db, provider=provider, current=friday_morning))
        assert len(list(db.scalars(select(TelegramDelivery)))) == 2
        # A newly discovered company with the same opening date still gets its own review.
        provider.issues.append(issue("3", gmp_at=friday_morning))
        asyncio.run(ipos.refresh(db, provider=provider, current=friday_morning))
        assert len(list(db.scalars(select(TelegramDelivery)))) == 3


def test_no_post_opening_or_disabled_telegram_messages(store, monkeypatch):
    with store() as db:
        after = NOW.replace(hour=10)
        i = issue(opens_on=NOW.date(), gmp_at=after)
        assert asyncio.run(ipos.refresh(db, provider=Provider([i]), current=after))["queued"] == 0
        monkeypatch.setattr(settings, "telegram_enabled", False)
        assert asyncio.run(ipos.refresh(db, provider=Provider([issue()]), current=NOW))["queued"] == 0


def test_source_failure_preserves_data_and_hides_remote_error(store):
    with store() as db:
        asyncio.run(ipos.refresh(db, provider=Provider([issue()]), current=NOW, notify=False))
        result = asyncio.run(ipos.refresh(db, provider=Provider(error=True), current=NOW))
        assert result["status"] == "FAILED"
        assert len(ipos.list_issues(db, NOW)) == 1
        assert "private remote" not in str(ipos.status(db))


def test_telegram_worker_delivers_ranked_parts_and_saves_receipts(store, monkeypatch):
    sent = []
    async def send(chat, message, **kwargs):
        sent.append(message)
        assert chat == "456" and kwargs["parse_mode"] == "HTML"
        return 100 + len(sent)
    monkeypatch.setattr(telegram_alerts, "send_message", send)
    clock = [NOW]
    monkeypatch.setattr(telegram_alerts, "now", lambda: ipos.utc(clock[0]))
    with store() as db:
        asyncio.run(ipos.refresh(db, provider=Provider([issue("1", gmp="40"), issue("2", gmp="20")]), current=NOW))
    assert asyncio.run(telegram_alerts.deliver_one(store))
    clock[0] += timedelta(seconds=10)
    assert asyncio.run(telegram_alerts.deliver_one(store))
    assert "Demo Issuer 1" in sent[0] and "Demo Issuer 2" in sent[1]
    assert "₹500 Cr" in sent[0] and "OFS: ₹100 Cr" in sent[0]
    with store() as db:
        job = db.scalar(select(TelegramDelivery))
        assert job.status == "SENT" and job.message_ids == [101, 102]


def test_stale_or_rescheduled_brief_is_superseded_before_send(store, monkeypatch):
    with store() as db:
        asyncio.run(ipos.refresh(db, provider=Provider([issue()]), current=NOW))
    monkeypatch.setattr(telegram_alerts, "now", lambda: NOW + timedelta(hours=25))
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    with store() as db:
        assert db.scalar(select(TelegramDelivery)).status == "SUPERSEDED"


def test_ambiguous_telegram_send_is_not_replayed(store, monkeypatch):
    async def uncertain(*args, **kwargs):
        raise TelegramError("Unknown outcome", uncertain=True)
    monkeypatch.setattr(telegram_alerts, "send_message", uncertain)
    with store() as db:
        asyncio.run(ipos.refresh(db, provider=Provider([issue()]), current=NOW))
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    assert not asyncio.run(telegram_alerts.deliver_one(store))
    with store() as db:
        assert db.scalar(select(TelegramDelivery)).status == "UNCERTAIN"


def test_scheduler_is_ist_daily_and_retries_failed_fetches(store, monkeypatch):
    calls = []
    async def fake_refresh(db, **kwargs):
        current = kwargs["current"]
        calls.append(current)
        db.add(IPOCheck(attempted_at=current, error="Unavailable"))
        db.commit()
    monkeypatch.setattr(ipos, "refresh", fake_refresh)
    asyncio.run(ipos.run_due(store, current=NOW - timedelta(minutes=1)))
    assert calls == []
    asyncio.run(ipos.run_due(store, current=NOW))
    asyncio.run(ipos.run_due(store, current=NOW + timedelta(minutes=5)))
    assert len(calls) == 1
    asyncio.run(ipos.run_due(store, current=NOW + timedelta(minutes=30)))
    assert len(calls) == 2


def test_api_empty_state_and_refresh_error(client, monkeypatch):
    assert client.get("/api/ipos").json()["issues"] == []
    monkeypatch.setattr(ipos, "InvestorGainProvider", lambda: Provider(error=True))
    result = client.post("/api/ipos/refresh")
    assert result.status_code == 200 and result.json()["status"] == "FAILED"
    assert "private remote" not in result.text
