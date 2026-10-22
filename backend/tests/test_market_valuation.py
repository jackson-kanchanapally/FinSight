import asyncio
import importlib
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - register tables
from app.database import Base
from app.database import get_db
from app.main import app
from app.market.providers import (
    AlphaVantageProvider,
    MarketProviderError,
    PriceObservation,
)
from app.market.service import (
    latest_snapshot,
    refresh_market,
    save_manual_snapshot,
    store_observations,
)
from app.market.valuation import calculate_valuation, recalculate, scenario, ttm_at
from app.ai.analyst.context_builder import build_context
from app.ai.analyst.fallback import verified_fallback
from app.models.company import Company
from app.models.event import (
    CompanyEvent,
    DataOrigin,
    Document,
    EventType,
    ProcessingStatus,
    PublicationPrecision,
)
from app.models.financial import (
    FactMethod,
    FactStatus,
    FinancialFact,
    FinancialPeriod,
    FinancialScope,
    FinancialStatus,
    FinancialUnit,
    MetricCode,
    PeriodType,
)
from app.models.market import MarketSnapshot, ValuationSnapshot
from app.schemas.market import MarketSnapshotInput
from app.services.financials.period import identify_period


@pytest.fixture
def market_db(monkeypatch):
    # These fixtures describe September 2026; freeze freshness, not production rules.
    class FixtureDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr('app.market.expectations.datetime', FixtureDatetime)
    monkeypatch.setattr('app.ai.analyst.context_builder.datetime', FixtureDatetime)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        yield db
    Base.metadata.drop_all(engine)
    engine.dispose()


def company(db: Session, symbol="TEST", bse_code="123456") -> Company:
    item = Company(
        symbol=symbol, name=f"{symbol} Limited", exchange="NSE", bse_code=bse_code
    )
    db.add(item)
    db.commit()
    return item


def add_quarter(
    db: Session,
    item: Company,
    end: date,
    published: date,
    *,
    scope=FinancialScope.CONSOLIDATED,
    status=FinancialStatus.VALIDATED,
    missing=(),
    eps="1",
    pat="10",
    period_type=PeriodType.QUARTERLY,
) -> FinancialPeriod:
    event = CompanyEvent(
        company_id=item.id,
        event_type=EventType.FINANCIAL_RESULTS,
        title=f"Results {end}",
        published_at=datetime.combine(published, datetime.min.time(), timezone.utc),
        source="TEST",
        source_url="https://example.com/results",
        data_origin=DataOrigin.MANUAL,
        publication_precision=PublicationPrecision.DATE,
        processing_status=ProcessingStatus.PROCESSED,
        fingerprint=f"{item.id}-{end}-{scope.value}-{period_type.value}",
    )
    db.add(event)
    db.flush()
    document = Document(
        company_id=item.id,
        event_id=event.id,
        source="TEST",
        source_url=event.source_url,
        document_url=event.source_url,
        processing_status=ProcessingStatus.PROCESSED,
    )
    db.add(document)
    db.flush()
    identity = identify_period(end, period_type)
    period = FinancialPeriod(
        company_id=item.id,
        fiscal_year=identity.fiscal_year,
        quarter=identity.quarter,
        period_start=identity.start,
        period_end=end,
        period_type=period_type,
        scope=scope,
        status=status,
        source_event_id=event.id,
        source_document_id=document.id,
    )
    db.add(period)
    db.flush()
    values = {
        MetricCode.REVENUE_FROM_OPERATIONS: "100",
        MetricCode.PAT: pat,
        MetricCode.PBT: "13",
        MetricCode.FINANCE_COST: "1",
        MetricCode.DEPRECIATION: "1",
        MetricCode.DILUTED_EPS: eps,
        MetricCode.OPERATING_CASH_FLOW: "15",
        MetricCode.CAPITAL_EXPENDITURE: "3",
        MetricCode.TOTAL_DEBT: "20",
        MetricCode.CASH_AND_CASH_EQUIVALENTS: "5",
        MetricCode.TOTAL_EQUITY: "100",
    }
    for code, value in values.items():
        if code in missing:
            continue
        unit = (
            FinancialUnit.INR_PER_SHARE
            if code == MetricCode.DILUTED_EPS
            else FinancialUnit.INR_CRORE
        )
        db.add(
            FinancialFact(
                financial_period_id=period.id,
                metric_code=code,
                value=Decimal(value),
                unit=unit,
                source_document_id=document.id,
                source_label=code.value,
                source_column_date=end,
                extraction_method=FactMethod.STRUCTURED,
                status=FactStatus.VERIFIED_STRUCTURED,
            )
        )
    db.commit()
    return period


def four_quarters(db: Session, item: Company):
    ends = (date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30))
    return [
        add_quarter(db, item, end, date.fromordinal(end.toordinal() + 1))
        for end in ends
    ]


def quote(db: Session, item: Company, day=date(2026, 9, 28), **changes):
    payload = {
        "close_price": "100",
        "pe_ratio": "30",
        "market_cap_crore": "400",
        "forward_eps_growth_pct": "20",
        "growth_source_url": "https://example.com/growth",
        "as_of_date": day,
        "source_url": "https://example.com/price",
    }
    payload.update(changes)
    return save_manual_snapshot(db, item, MarketSnapshotInput.model_validate(payload))


def test_ttm_and_deterministic_valuation_with_inspectable_inputs(market_db):
    item = company(market_db)
    periods = four_quarters(market_db, item)
    market = quote(market_db, item)
    latest_id, details = calculate_valuation(
        market_db, market, FinancialScope.CONSOLIDATED
    )
    assert latest_id == periods[-1].id
    expected = {
        "ttm_revenue": "400",
        "ttm_pat": "40",
        "ttm_ebitda": "60",
        "ttm_eps": "4",
        "pe_ttm": "25",
        "ps_ttm": "1",
        "enterprise_value_crore": "415",
        "earnings_yield_pct": "4",
        "price_to_book": "4",
        "fcf_yield_pct": "12",
        "peg_forward": "1.25",
    }
    for key, value in expected.items():
        assert Decimal(details[key]["value"]) == Decimal(value), key
        assert details[key]["status"] == "VALID" and details[key]["inputs"]
    assert Decimal(details["ev_ebitda_ttm"]["value"]).quantize(
        Decimal(".0001")
    ) == Decimal("6.9167")
    assert Decimal(details["ev_sales_ttm"]["value"]) == Decimal("1.0375")
    assert details["metadata"]["growth_type"] == "USER_ASSUMPTION"
    record = recalculate(market_db, market, FinancialScope.CONSOLIDATED)
    assert record.pe_ttm == Decimal("25")
    assert recalculate(market_db, market, FinancialScope.CONSOLIDATED).id == record.id


def test_ai_context_uses_calculated_valuation_evidence(market_db):
    item = company(market_db)
    periods = four_quarters(market_db, item)
    market = quote(market_db, item)
    context = build_context(market_db, item.name, item.symbol, periods[-1])
    pe = context.registry[f"V{market.id}_PE_TTM"]
    peg = context.registry[f"V{market.id}_PEG_FORWARD"]
    assert pe["value"] == "25.000000" and peg["value"] == "1.250000"
    assert pe["input_evidence_ids"][0] == f"Q{market.id}"
    fallback = verified_fallback(context.registry, periods[-1])
    assert fallback.valuation_assessment.status == "ASSESSED"
    assert f"V{market.id}_PE_TTM" in fallback.valuation_assessment.evidence_ids


def test_point_in_time_excludes_july_results_on_june_15(market_db):
    item = company(market_db)
    april = add_quarter(market_db, item, date(2026, 3, 31), date(2026, 4, 1))
    july = add_quarter(market_db, item, date(2026, 6, 30), date(2026, 7, 1))
    before = quote(market_db, item, date(2026, 6, 15))
    after = quote(market_db, item, date(2026, 7, 15))
    assert (
        recalculate(market_db, before, FinancialScope.CONSOLIDATED).financial_period_id
        == april.id
    )
    assert (
        recalculate(market_db, after, FinancialScope.CONSOLIDATED).financial_period_id
        == july.id
    )
    assert (
        ttm_at(market_db, item.id, date(2026, 6, 15), FinancialScope.CONSOLIDATED)[1][
            "revenue"
        ]["value"]
        is None
    )


def test_verified_annual_bridge_is_ttm_without_future_filing(market_db):
    item = company(market_db)
    prior = add_quarter(market_db, item, date(2025, 6, 30), date(2025, 7, 1))
    annual = add_quarter(
        market_db,
        item,
        date(2026, 3, 31),
        date(2026, 4, 1),
        period_type=PeriodType.ANNUAL,
        eps="4",
        pat="40",
    )
    current = add_quarter(market_db, item, date(2026, 6, 30), date(2026, 7, 1))
    for fact in annual.facts:
        replacement = {
            MetricCode.REVENUE_FROM_OPERATIONS: "400",
            MetricCode.PBT: "52",
            MetricCode.FINANCE_COST: "4",
            MetricCode.DEPRECIATION: "4",
        }.get(fact.metric_code)
        if replacement:
            fact.value = Decimal(replacement)
    market_db.commit()
    details = calculate_valuation(
        market_db, quote(market_db, item), FinancialScope.CONSOLIDATED
    )[1]
    assert details["metadata"]["ttm_method"] == "ANNUAL_BRIDGE"
    assert details["metadata"]["period_ids"] == [annual.id, current.id, prior.id]
    assert Decimal(details["ttm_revenue"]["value"]) == 400
    assert Decimal(details["ttm_pat"]["value"]) == 40
    assert Decimal(details["ttm_ebitda"]["value"]) == 60
    assert Decimal(details["ttm_eps"]["value"]) == 4
    assert Decimal(details["pe_ttm"]["value"]) == 25
    june = ttm_at(market_db, item.id, date(2026, 6, 15), FinancialScope.CONSOLIDATED)[1]
    assert june["revenue"]["value"] is None


def test_annual_bridge_waits_for_annual_publication(market_db):
    item = company(market_db)
    add_quarter(market_db, item, date(2025, 6, 30), date(2025, 7, 1))
    add_quarter(
        market_db,
        item,
        date(2026, 3, 31),
        date(2026, 9, 24),
        period_type=PeriodType.ANNUAL,
    )
    add_quarter(market_db, item, date(2026, 6, 30), date(2026, 7, 1))
    before = ttm_at(market_db, item.id, date(2026, 7, 15), FinancialScope.CONSOLIDATED)[
        1
    ]
    after = ttm_at(market_db, item.id, date(2026, 9, 25), FinancialScope.CONSOLIDATED)[
        1
    ]
    assert before["revenue"]["value"] is None
    assert after["revenue"]["value"] is not None
    assert after["method"] == "ANNUAL_BRIDGE"


def test_missing_quarter_scope_and_review_fact_block_ttm(market_db):
    item = company(market_db)
    periods = four_quarters(market_db, item)
    market = quote(market_db, item)
    periods[1].status = FinancialStatus.NEEDS_REVIEW
    market_db.commit()
    _, details = calculate_valuation(market_db, market, FinancialScope.CONSOLIDATED)
    assert details["ttm_revenue"]["value"] is None
    assert details["pe_ttm"]["value"] is None
    assert (
        ttm_at(market_db, item.id, market.as_of_date, FinancialScope.STANDALONE)[1][
            "pat"
        ]["value"]
        is None
    )
    periods[1].status = FinancialStatus.VALIDATED
    fact = next(f for f in periods[1].facts if f.metric_code == MetricCode.PAT)
    fact.status = FactStatus.NEEDS_REVIEW
    market_db.commit()
    assert (
        ttm_at(market_db, item.id, market.as_of_date, FinancialScope.CONSOLIDATED)[1][
            "pat"
        ]["value"]
        is None
    )


@pytest.mark.parametrize("eps", ["0", "-1"])
def test_nonpositive_eps_blocks_pe_and_peg(market_db, eps):
    item = company(market_db)
    periods = four_quarters(market_db, item)
    fact = next(f for f in periods[-1].facts if f.metric_code == MetricCode.DILUTED_EPS)
    fact.value = Decimal(eps)
    market_db.commit()
    market_db.expire_all()
    details = calculate_valuation(
        market_db, quote(market_db, item), FinancialScope.CONSOLIDATED
    )[1]
    assert details["ttm_eps"]["status"] == "NEEDS_REVIEW"
    assert (
        details["pe_ttm"]["value"] is None and details["peg_forward"]["value"] is None
    )


def test_missing_balance_prevents_ev_and_negative_fcf_is_explicit(market_db):
    item = company(market_db)
    periods = four_quarters(market_db, item)
    for period in periods:
        next(
            f for f in period.facts if f.metric_code == MetricCode.CAPITAL_EXPENDITURE
        ).value = Decimal("20")
    for period in periods:
        for fact in list(period.facts):
            if fact.metric_code == MetricCode.CASH_AND_CASH_EQUIVALENTS:
                market_db.delete(fact)
    market_db.commit()
    market_db.expire_all()
    details = calculate_valuation(
        market_db, quote(market_db, item), FinancialScope.CONSOLIDATED
    )[1]
    assert details["enterprise_value_crore"]["value"] is None
    assert details["ev_ebitda_ttm"]["value"] is None
    assert Decimal(details["fcf_yield_pct"]["value"]) < 0
    assert details["fcf_yield_pct"]["reason"] == "Negative free cash flow"


def test_manual_history_is_idempotent_and_keeps_old_dates(market_db):
    item = company(market_db)
    older = quote(market_db, item, date(2026, 9, 25))
    newest = quote(market_db, item, date(2026, 9, 28))
    again = quote(market_db, item, date(2026, 9, 28), close_price="101")
    assert newest.id == again.id and older.id != newest.id
    assert latest_snapshot(market_db, item.id).close_price == Decimal("101")
    assert market_db.query(MarketSnapshot).count() == 2


def test_valuation_api_history_and_scenario(market_db):
    item = company(market_db)
    four_quarters(market_db, item)
    older = quote(market_db, item, date(2026, 9, 25))
    newest = quote(market_db, item)
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            current = client.get(f"/api/companies/{item.symbol}/valuation")
            assert current.status_code == 200, current.text
            assert current.json()["market_snapshot_id"] == newest.id
            assert Decimal(current.json()["pe_ttm"]) == 25
            assert current.json()["metric_details"]["pe_ttm"]["inputs"]
            history = client.get(f"/api/companies/{item.symbol}/valuation/history")
            assert history.status_code == 200
            assert [row["market_snapshot_id"] for row in history.json()] == [
                newest.id,
                older.id,
            ]
            response = client.post(
                f"/api/companies/{item.symbol}/valuation/scenario",
                json={
                    "assumptions": [
                        {
                            "name": "Base",
                            "eps_growth_pct": "25",
                            "terminal_pe": "40",
                            "years": 3,
                        }
                    ]
                },
            )
            assert response.status_code == 200, response.text
            assert Decimal(response.json()[0]["implied_future_price"]) == Decimal(
                "312.5"
            )
            assert (
                client.get(f"/api/companies/{item.symbol}/market").json()["latest"][
                    "id"
                ]
                == newest.id
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", "https://example.com"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self.data


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        assert kwargs["params"]["symbol"] == "123456.BSE"
        assert kwargs["params"]["outputsize"] == "compact"
        return self.response


def test_free_provider_history_and_failure(market_db):
    item = company(market_db)
    data = {
        "Time Series (Daily)": {
            "2026-09-25": {"4. close": "100.5"},
            "2026-09-24": {"4. close": "99"},
        }
    }
    client = FakeClient(FakeResponse(data))
    provider = AlphaVantageProvider("test-key", client=client)
    rows = provider.get_price_history(item, date(2026, 9, 24), date(2026, 9, 25))
    assert [row.close_price for row in rows] == [Decimal("99"), Decimal("100.5")]
    assert all(
        row.data_origin == "EOD" and row.provider_symbol == "123456.BSE" for row in rows
    )
    assert len(store_observations(market_db, item, rows)) == 2
    store_observations(market_db, item, rows)
    assert market_db.query(MarketSnapshot).count() == 2
    assert client.calls == 1
    for response, reason in (
        (FakeResponse({}, 429), "RATE_LIMITED"),
        (FakeResponse({"Error Message": "bad"}), "SYMBOL_NOT_FOUND"),
        (FakeResponse({"unexpected": 1}), "MALFORMED_RESPONSE"),
    ):
        with pytest.raises(MarketProviderError, match=reason):
            AlphaVantageProvider(
                "test-key", client=FakeClient(response)
            ).get_price_history(item, date(2026, 9, 24), date(2026, 9, 25))


def test_provider_timeout_and_stale_response_preserve_history(market_db):
    item = company(market_db)
    today = datetime.now().date()
    original = quote(market_db, item, today)

    class TimeoutClient(FakeClient):
        def get(self, url, **kwargs):
            raise httpx.TimeoutException("timeout")

    with pytest.raises(MarketProviderError, match="PROVIDER_TIMEOUT"):
        AlphaVantageProvider("test-key", client=TimeoutClient(None)).get_quote(item)

    class StaleProvider:
        name = "ALPHA_VANTAGE"

        def get_price_history(self, company, start_date, end_date):
            return [
                PriceObservation(
                    date.fromordinal(today.toordinal() - 30),
                    Decimal("99"),
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                    self.name,
                    "123456.BSE",
                    "https://example.com/price",
                )
            ]

    with pytest.raises(MarketProviderError, match="STALE_PROVIDER_RESPONSE"):
        refresh_market(market_db, item, provider=StaleProvider(), force=True)
    assert latest_snapshot(market_db, item.id).id == original.id
    assert market_db.query(MarketSnapshot).count() == 1


def test_scheduler_isolates_provider_failures(market_db, monkeypatch):
    item = company(market_db)
    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "SessionLocal", lambda: market_db)

    def fail(*args, **kwargs):
        raise MarketProviderError("PROVIDER_TIMEOUT")

    monkeypatch.setattr(main_module, "refresh_market", fail)
    asyncio.run(main_module.scheduled_market_refresh())
    assert market_db.get(Company, item.id) is not None


def test_scenario_math_is_deterministic():
    result = scenario(Decimal("25"), Decimal("1500"), Decimal("25"), Decimal("40"), 3)
    assert Decimal(result["future_eps"]) == Decimal("48.828125")
    assert Decimal(result["implied_future_price"]) == Decimal("1953.125")
    assert Decimal(result["annualized_return_pct"]).quantize(Decimal(".01")) == Decimal(
        "9.20"
    )
