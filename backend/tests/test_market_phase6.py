import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.market.providers import (
    NSEMarketDataProvider,
    MarketProviderError,
    PriceObservation,
)
from app.market.reference import comparisons, import_market_lens_csv
from app.market.valuation import reverse_growth, scenario, valuation_change
from app.models.company import Company
from app.models.financial import FinancialScope
from app.models.market import MarketSnapshot
from tests.test_market_valuation import (
    add_quarter,
    company,
    four_quarters,
    market_db,
    quote,
)
from app.models.financial import PeriodType
from app.market.valuation import recalculate
from app.market.service import latest_snapshot, store_observations
from datetime import datetime, timezone
from app.database import get_db
from app.main import app


def _nse_client(*, malformed=False, status=200):
    calls = []

    def handler(request):
        data = json.loads(request.content)
        calls.append(data)
        if status == 429:
            return httpx.Response(429)
        if data["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "test-session"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"protocolVersion": "2025-06-18"},
                },
            )
        if data["method"] == "notifications/initialized":
            return httpx.Response(202)
        name = data["params"]["name"]
        if name == "get_corporate_actions":
            value = {"actions": []}
        else:
            value = {
                "data": [
                    {
                        "symbol": "TEST",
                        "date": "2026-09-23",
                        "open": 95,
                        "high": 105 if not malformed else 90,
                        "low": 94,
                        "close": 100,
                        "volume": 1000,
                    }
                ],
                "next_end_date": "2026-06-22",
            }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="event:message\ndata:"
            + json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(value)}],
                        "isError": False,
                    },
                }
            )
            + "\n\n",
        )

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_nse_official_mcp_normalization_and_historical_storage(market_db):
    item = company(market_db)
    client, calls = _nse_client()
    provider = NSEMarketDataProvider(client=client)
    from app.market.service import store_observations

    rows = provider.get_price_history(item, date(2026, 9, 1), date(2026, 9, 28))
    assert len(rows) == 1
    assert (
        rows[0].open_price,
        rows[0].high_price,
        rows[0].low_price,
        rows[0].volume,
    ) == (Decimal(95), Decimal(105), Decimal(94), 1000)
    stored = store_observations(market_db, item, rows)
    assert stored[0].data_origin == "EOD" and stored[0].provider == "NSE_MCP"
    assert stored[0].volume == 1000
    assert store_observations(market_db, item, rows)[0].id == stored[0].id
    names = [call["params"]["name"] for call in calls if call["method"] == "tools/call"]
    assert names == ["get_corporate_actions", "get_stock_history"]


def test_official_same_day_observation_precedes_manual_reference(market_db):
    item = company(market_db)
    quote(market_db, item, date(2026, 9, 23), close_price="99")
    official = store_observations(
        market_db,
        item,
        [
            PriceObservation(
                date(2026, 9, 23),
                Decimal(100),
                datetime.now(timezone.utc),
                datetime.now(timezone.utc),
                "NSE_MCP",
                "TEST",
                "https://mcp.nseindia.in/bhavcopy/cm/mcp",
            )
        ],
    )[0]
    assert latest_snapshot(market_db, item.id).id == official.id


def test_nse_malformed_ohlcv_and_rate_limit_are_safe(market_db):
    item = company(market_db)
    for options, reason in (
        ({"malformed": True}, "MALFORMED_RESPONSE"),
        ({"status": 429}, "RATE_LIMITED"),
    ):
        client, _ = _nse_client(**options)
        with pytest.raises(MarketProviderError, match=reason):
            NSEMarketDataProvider(client=client).get_price_history(
                item, date(2026, 9, 1), date(2026, 9, 28)
            )


def test_nse_timeout_does_not_create_observations(market_db):
    item = company(market_db)

    def timeout(_request):
        raise httpx.TimeoutException("timed out")

    client = httpx.Client(transport=httpx.MockTransport(timeout))
    with pytest.raises(MarketProviderError, match="PROVIDER_TIMEOUT"):
        NSEMarketDataProvider(client=client).get_price_history(
            item, date(2026, 9, 1), date(2026, 9, 28)
        )
    assert market_db.query(MarketSnapshot).count() == 0


def test_scenario_reverse_and_valuation_change(market_db):
    case = scenario(Decimal(25), Decimal(1000), Decimal(25), Decimal(40), 3)
    assert Decimal(case["future_eps"]) == Decimal("48.828125")
    assert Decimal(case["total_return_pct"]) == Decimal("95.312500")
    reverse = reverse_growth(Decimal(25), Decimal(1000), Decimal(40), 3, Decimal(0))
    assert Decimal(reverse["required_eps_cagr_pct"]) == 0
    item = company(market_db)
    four_quarters(market_db, item)
    old_market = quote(market_db, item, date(2026, 9, 25), close_price="100")
    new_market = quote(market_db, item, date(2026, 9, 28), close_price="120")
    old = recalculate(market_db, old_market, FinancialScope.CONSOLIDATED)
    new = recalculate(market_db, new_market, FinancialScope.CONSOLIDATED)
    change = valuation_change(old, new, old_market, new_market)
    assert Decimal(change["price_change_pct"]) == 20
    assert Decimal(change["eps_change_pct"]) == 0
    assert Decimal(change["pe_change_pct"]) == 20


def test_market_lens_csv_stays_separate_and_compares(market_db):
    item = company(market_db)
    four_quarters(market_db, item)
    market = quote(market_db, item)
    valuation = recalculate(market_db, market, FinancialScope.CONSOLIDATED)
    result = import_market_lens_csv(
        market_db,
        "Stock,P/E,P/B\nTEST,25,4\nOTHER,15,2\n",
        date(2026, 9, 28),
        "https://marketlens.nseindia.com/screener",
    )
    assert result == {"imported_metrics": 2, "skipped_companies": 1}
    listed = comparisons(market_db, valuation, Decimal(5))
    assert len(listed) == 2 and all(row["status"] == "MATCH" for row in listed)
    assert valuation.pe_ttm == 25


def test_future_published_annual_facts_never_enter_june_valuation(market_db):
    item = company(market_db)
    for end in (
        date(2025, 6, 30),
        date(2025, 9, 30),
        date(2025, 12, 31),
        date(2026, 3, 31),
    ):
        add_quarter(market_db, item, end, date(2026, 4, 1))
    future = add_quarter(
        market_db,
        item,
        date(2026, 3, 31),
        date(2026, 7, 1),
        period_type=PeriodType.ANNUAL,
        pat="9999",
    )
    market = quote(market_db, item, date(2026, 6, 15))
    valuation = recalculate(market_db, market, FinancialScope.CONSOLIDATED)
    assert valuation.ttm_pat == Decimal(40)
    future_ids = {f"F{fact.id}" for fact in future.facts}
    used_ids = {
        evidence
        for item in valuation.metric_details.values()
        if isinstance(item, dict)
        for evidence in item.get("inputs", [])
    }
    assert not (future_ids & used_ids)
    assert future.id not in valuation.metric_details["metadata"]["period_ids"]


def test_reverse_change_and_reference_api(market_db):
    item = company(market_db)
    four_quarters(market_db, item)
    older = quote(market_db, item, date(2026, 9, 25), close_price="100")
    newer = quote(market_db, item, date(2026, 9, 28), close_price="120")
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            reverse = client.post(
                f"/api/companies/{item.symbol}/valuation/reverse",
                json={
                    "terminal_pe": "30",
                    "years": 3,
                    "target_annualized_return_pct": "0",
                },
            )
            assert reverse.status_code == 200, reverse.text
            assert Decimal(reverse.json()["required_eps_cagr_pct"]) == 0
            change = client.get(
                f"/api/companies/{item.symbol}/valuation/change",
                params={
                    "old_market_snapshot_id": older.id,
                    "new_market_snapshot_id": newer.id,
                },
            )
            assert change.status_code == 200, change.text
            assert Decimal(change.json()["price_change_pct"]) == 20
            imported = client.post(
                "/api/market-lens/import",
                json={
                    "csv_text": "Stock,P/E\nTEST,30\n",
                    "as_of_date": "2026-09-28",
                    "source_url": "https://marketlens.nseindia.com/screener",
                },
            )
            assert imported.status_code == 200, imported.text
            comparison = client.get(f"/api/companies/{item.symbol}/valuation/reference")
            assert comparison.status_code == 200, comparison.text
            assert comparison.json()[0]["status"] == "MATCH"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_change_endpoint_reports_unavailable_historical_eps_without_422(market_db):
    item = company(market_db)
    four_quarters(market_db, item)
    old = quote(market_db, item, date(2026, 6, 15))
    current = quote(market_db, item, date(2026, 9, 28))
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/companies/{item.symbol}/valuation/change",
                params={
                    "old_market_snapshot_id": old.id,
                    "new_market_snapshot_id": current.id,
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "INSUFFICIENT_DATA"
            assert "old TTM EPS" in response.json()["missing_inputs"]
    finally:
        app.dependency_overrides.pop(get_db, None)
