"""NSE MCP client, insight services and routes against an in-process fake server."""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from app.config import settings
from app.market.nse_mcp import NSEMCPClient, NSEMCPError, set_nse_mcp
from tests.conftest import create_company, post_tx

TODAY = date.today()


def days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


class FakeNSE:
    """Speaks the subset of Streamable HTTP MCP that NSE's servers use."""

    def __init__(self):
        self.tools: dict[tuple[str, str], object] = {}
        self.calls: list[tuple[str, str, dict]] = []
        self.initialized: list[str] = []
        self.expire_next = False
        self.sessions: set[str] = set()

    def on(self, server: str, tool: str, value):
        self.tools[(server, tool)] = value

    def handler(self, request: httpx.Request) -> httpx.Response:
        server = "live" if "/cmmkt/" in request.url.path else "bhavcopy"
        body = json.loads(request.content)
        method = body.get("method")
        if method == "initialize":
            sid = f"{server}-{len(self.initialized) + 1}"
            self.initialized.append(server)
            self.sessions.add(sid)
            return httpx.Response(
                200,
                headers={"Mcp-Session-Id": sid},
                json={"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}},
            )
        sid = request.headers.get("mcp-session-id")
        if sid not in self.sessions:
            return httpx.Response(404, json={"error": "unknown session"})
        if method == "notifications/initialized":
            return httpx.Response(202)
        if self.expire_next:
            self.expire_next = False
            self.sessions.discard(sid)
            return httpx.Response(404, json={"error": "session expired"})
        if method == "tools/list":
            names = [t for (s, t) in self.tools if s == server]
            return self._sse(body["id"], {"tools": [{"name": n} for n in names]})
        name, arguments = body["params"]["name"], body["params"]["arguments"]
        self.calls.append((server, name, arguments))
        value = self.tools.get((server, name))
        if callable(value):
            value = value(arguments)
        if value is None:
            value = {"error": f"Symbol not found: {arguments.get('symbol')}"}
        if isinstance(value, tuple) and value[0] == "isError":
            result = {"content": [{"type": "text", "text": value[1]}], "isError": True}
        else:
            result = {"content": [{"type": "text", "text": json.dumps(value)}], "isError": False}
        return self._sse(body["id"], result)

    @staticmethod
    def _sse(request_id, result) -> httpx.Response:
        payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream;charset=UTF-8"},
            content=f"event:message\ndata:{payload}\n\n".encode(),
        )


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(settings, "nse_mcp_enabled", True)
    server = FakeNSE()
    set_nse_mcp(NSEMCPClient(transport=httpx.MockTransport(server.handler)))
    yield server
    set_nse_mcp(None)


def live_quote(symbol, ltp, prev, high52=1611.8):
    return {
        "updatedAt": "2026-11-10T05:16:27Z",
        "stock": {
            "symbol": symbol, "series": "EQ", "openPrice": prev, "highPrice": max(ltp, prev),
            "lowPrice": min(ltp, prev), "preClosePrice": prev, "lastTradedPrice": ltp,
            "perChange": round((ltp / prev - 1) * 100, 2), "volume": 1000, "value": 1.2,
            "fiftyTwoWeekHigh": high52, "fiftyTwoWeekLow": 900.0, "perChange30d": 4.5,
            "latestTimestamp": "2026-11-09 16:00:27",
        },
    }


def relative(symbol, beta):
    return {
        "date": days_ago(1), "base_date": days_ago(366), "months": 12,
        "stock": {"symbol": symbol, "return_pct": 12.0}, "index": {"index": "Nifty 50", "return_pct": 4.0},
        "outperformance_pct_points": 8.0, "beta": beta, "correlation": 0.5,
    }


# -- client -------------------------------------------------------------------

def test_client_reuses_session_caches_and_decodes_sse(fake):
    fake.on("bhavcopy", "get_52_week_high_low", {"symbol": "TCS", "52w_high": 3350.0})
    client = NSEMCPClient(transport=httpx.MockTransport(fake.handler))

    async def run():
        first = await client.call("bhavcopy", "get_52_week_high_low", {"symbol": "TCS"})
        second = await client.call("bhavcopy", "get_52_week_high_low", {"symbol": "TCS"})
        await client.call("bhavcopy", "get_52_week_high_low", {"symbol": "TCS"}, ttl=0)
        await client.call("bhavcopy", "get_52_week_high_low", {"symbol": "TCS"}, ttl=0)
        return first, second

    first, second = asyncio.run(run())
    assert first == second == {"symbol": "TCS", "52w_high": 3350.0}
    assert fake.initialized == ["bhavcopy"]  # one session for every call
    assert len(fake.calls) == 3  # second call served from cache


def test_client_maps_tool_errors_and_reinitialises_expired_sessions(fake):
    fake.on("bhavcopy", "get_ltp_by_date", ("isError", "bad date"))
    fake.on("live", "cm_get_data_status", {"crawlIntervalMinutes": 5})
    client = NSEMCPClient(transport=httpx.MockTransport(fake.handler))

    async def run():
        with pytest.raises(NSEMCPError) as missing:
            await client.call("bhavcopy", "get_stock_history", {"symbol": "NOPE"}, ttl=0)
        with pytest.raises(NSEMCPError) as failed:
            await client.call("bhavcopy", "get_ltp_by_date", {"symbol": "TCS"}, ttl=0)
        await client.call("live", "cm_get_data_status", {}, ttl=0)
        fake.expire_next = True
        status = await client.call("live", "cm_get_data_status", {}, ttl=0)
        return missing.value, failed.value, status

    missing, failed, status = asyncio.run(run())
    assert missing.reason == "SYMBOL_NOT_FOUND"
    assert failed.reason == "TOOL_ERROR" and failed.detail == "bad date"
    assert status == {"crawlIntervalMinutes": 5}
    assert fake.initialized.count("live") == 2


def test_client_refuses_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "nse_mcp_enabled", False)
    with pytest.raises(NSEMCPError) as exc:
        asyncio.run(NSEMCPClient().call("live", "cm_get_data_status"))
    assert exc.value.reason == "DISABLED"


# -- routes -------------------------------------------------------------------

def test_portfolio_is_marked_to_market_with_live_quote_and_beta(client, fake):
    create_company(client)
    assert post_tx(client, "BUY", 10, 1000, "2026-01-05").status_code == 201
    fake.on("live", "cm_get_stock_quote", live_quote("ESDS", 1147.0, 1166.6, high52=1400.0))
    fake.on("bhavcopy", "get_stock_vs_index", relative("ESDS", 1.25))

    body = client.get("/api/nse/portfolio").json()
    holding = body["holdings"][0]
    assert holding["status"] == "PRICED"
    assert holding["quote"]["source"] == "LIVE"
    assert holding["market_value"] == "11470.00"
    assert holding["unrealised_pnl"] == "1470.00"
    assert holding["unrealised_pnl_pct"] == "14.70"
    assert holding["day_change_value"] == "-196.00"
    assert holding["from_52w_high_pct"] == "-18.07"
    assert holding["weight_pct"] == "100.00"
    assert holding["relative"]["beta"] == 1.25
    assert body["totals"]["beta"] == "1.250"
    assert body["totals"]["day_change_pct"] == "-1.68"
    assert body["errors"] == []


def test_portfolio_falls_back_to_bhavcopy_bulk_quote(client, fake):
    create_company(client)
    post_tx(client, "BUY", 4, 500, "2026-01-05")
    fake.on("live", "cm_get_stock_quote", None)  # live lookup fails
    fake.on("bhavcopy", "get_bulk_quote", {"quotes": [
        {"symbol": "ESDS", "close": 600.0, "prev_close": 590.0, "pct_change": 1.69, "date": days_ago(1),
         "open": 591.0, "high": 605.0, "low": 588.0, "volume": 10}]})
    fake.on("bhavcopy", "get_stock_vs_index", None)

    body = client.get("/api/nse/portfolio").json()
    holding = body["holdings"][0]
    assert holding["quote"]["source"] == "EOD"
    assert holding["market_value"] == "2400.00"
    assert holding["day_change_value"] == "40.00"
    assert body["totals"]["beta"] is None
    # The live miss was recovered, so only the missing beta is reported.
    assert [e["source"] for e in body["errors"]] == ["Relative performance ESDS"]


def test_stock_insight_combines_tools_and_flags_unadjusted_prices(client, fake):
    fake.on("live", "cm_get_stock_quote", live_quote("RELIANCE", 1324.4, 1324.0))
    fake.on("bhavcopy", "get_52_week_high_low", {
        "symbol": "RELIANCE", "last_close": 1324.4, "52w_high": 1611.8, "52w_high_date": days_ago(200),
        "52w_low": 1152.2, "52w_low_date": days_ago(40), "position_in_range_pct": 37.4,
        "from_52w_high_pct": -17.8, "from_52w_low_pct": 14.9})
    fake.on("bhavcopy", "moving_average", lambda args: {20: 1223.84, 50: 1236.12, 200: 1324.71}[args["days"]])
    fake.on("bhavcopy", "get_volume_analysis", {"days_analysed": 30, "avg_volume": 100, "last_volume": 50,
                                                "last_vs_avg_pct": -50.0, "volume_spike_days": 1, "recent_5_days": []})
    fake.on("bhavcopy", "get_stock_vs_index", relative("RELIANCE", 0.987))
    fake.on("bhavcopy", "get_corporate_actions", {"actions": [
        {"exDate": days_ago(100), "actionType": "BONUS", "purpose": "Bonus 1:1", "adjustmentFactor": 0.5},
        {"exDate": days_ago(30), "actionType": "DIVIDEND", "purpose": "Dividend - Rs 5.5 Per Share"},
        {"exDate": days_ago(500), "actionType": "DIVIDEND", "purpose": "Dividend - Rs 10 Per Share"},
        {"exDate": (TODAY + timedelta(days=20)).isoformat(), "actionType": "DIVIDEND", "purpose": "Interim Dividend - Rs 2"},
    ]})
    fake.on("bhavcopy", "get_stock_history", {"from_date": days_ago(90), "to_date": days_ago(1),
        "summary": {"return_pct": 3.1}, "data": [{"date": days_ago(2), "close": 1300.0, "volume": 5},
                                                 {"date": days_ago(1), "close": 1324.4, "volume": 6}]})

    body = client.get("/api/nse/stocks/reliance").json()
    assert body["symbol"] == "RELIANCE"
    assert body["quote"]["price"] == "1324.40"
    assert [a["price_vs_average_pct"] for a in body["moving_averages"]] == ["8.22", "7.14", "-0.02"]
    assert body["trend"] == "50-day average below 200-day"
    assert body["dividends"]["trailing_12m_per_share"] == "5.50"
    assert body["dividends"]["yield_pct"] == "0.42"
    assert body["corporate_actions"]["upcoming"][0]["purpose"] == "Interim Dividend - Rs 2"
    assert "Bonus 1:1" in body["warnings"][0] and "unadjusted" in body["warnings"][0]
    assert len(body["history"]["points"]) == 2
    assert body["relative"]["beta"] == 0.987


def test_stock_insight_unknown_symbol_and_bad_input(client, fake):
    assert client.get("/api/nse/stocks/NOTREAL").status_code == 404
    assert client.get("/api/nse/stocks/bad%20sym!").status_code == 422


def test_market_pulse_selects_key_indices(client, fake):
    fake.on("live", "cm_get_live_indices", {"nseTimestamp": "09-Nov-2026 15:30:00", "indices": {
        "derivatives": [{"indexName": "NIFTY 50", "last": 25044.65, "previousClose": 24986.4, "percChange": 0.23},
                        {"indexName": "NIFTY FPI 150", "last": 1593.0}],
        "broad": [{"indexName": "INDIA VIX", "last": 17.4, "percChange": -5.5}]}})
    fake.on("bhavcopy", "get_market_mood", {"date": days_ago(1), "stock_breadth": {"up": 1237, "down": 1708}})
    fake.on("bhavcopy", "get_index_valuation", {"index": "Nifty 50", "pe": {"current": 20.48, "percentile": 22.2}})
    fake.on("bhavcopy", "get_index_movers", {"date": days_ago(1), "gainers": [{"index": "Nifty PSU Bank"}], "losers": []})
    fake.on("live", "nse_get_market_movers", {"gainers": [{"symbol": "KPEL", "perChange": 20}], "losers": []})

    body = client.get("/api/nse/market-pulse?sector_period=1W").json()
    assert [i["name"] for i in body["indices"]["items"]] == ["NIFTY 50", "INDIA VIX"]
    assert body["valuation"]["pe"]["percentile"] == 22.2
    assert body["sector_movers"]["period"] == "1W"
    assert body["stock_movers"]["gainers"][0]["symbol"] == "KPEL"
    assert body["errors"] == []
    assert client.get("/api/nse/market-pulse?sector_period=5Y").status_code == 422


def test_market_pulse_reports_disabled(client):
    response = client.get("/api/nse/market-pulse")
    assert response.status_code == 503
    assert "NSE_MCP_ENABLED" in response.json()["detail"]


def test_search_falls_back_to_compact_symbol_and_marks_inactive(client, fake):
    def search(args):
        if args["query"] == "TATAMOTORS":
            return {"results": [{"symbol": "TATAMOTORS", "last_close": 700.0, "last_date": days_ago(1)}]}
        if args["query"] == "HDFC":
            return {"results": [{"symbol": "HDFC", "last_close": 461.8, "last_date": "2023-06-06"},
                                {"symbol": "HDFCBANK", "last_close": 798.1, "last_date": days_ago(1)}]}
        return {"results": []}

    fake.on("bhavcopy", "search_symbols", search)
    body = client.get("/api/nse/search", params={"q": "tata motors"}).json()
    assert body["exact_match"]["symbol"] == "TATAMOTORS"
    assert body["results"][0]["symbol"] == "TATAMOTORS"
    assert client.get("/api/nse/search", params={"q": "zzz"}).json()["results"] == []
    hdfc = client.get("/api/nse/search", params={"q": "HDFC"}).json()
    assert [r["symbol"] for r in hdfc["results"]] == ["HDFCBANK", "HDFC"]
    assert hdfc["exact_match"]["inactive"] is True


def test_market_brief_index_fallback_uses_official_mcp(fake):
    from app.services.market_brief_sources import nse_mcp_indices

    fake.on("live", "cm_get_live_indices", {"nseTimestamp": "09-Nov-2026 15:30:00", "indices": {
        "derivatives": [{"indexName": "NIFTY 50", "last": 25044.65, "previousClose": 24986.4,
                         "open": 25076.4, "high": 25084.05, "low": 24838.75}],
        "broad": [{"indexName": "INDIA VIX", "last": 17.4, "previousClose": 18.4, "open": 18.0,
                   "high": 18.5, "low": 17.0}]}})
    quotes, breadth = asyncio.run(nse_mcp_indices(datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)))
    assert breadth is None
    assert set(quotes) == {"NIFTY 50", "INDIA VIX"}
    assert quotes["NIFTY 50"]["change_pct"] == "0.23"
    assert quotes["NIFTY 50"]["source"] == "NSE official MCP (live indices)"


def test_session_flags_weekday_holiday_as_stale():
    from app.services.nse_insights import market_session, nse_date

    tuesday_morning = datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc)  # 10:30 IST
    stale = market_session(tuesday_morning, nse_date("09-Nov-2026 15:30:00"))
    assert stale["state"] == "OPEN" and stale["stale"] is True
    fresh = market_session(tuesday_morning, nse_date("2026-11-10 10:29:00"))
    assert fresh["stale"] is False and fresh["data_date"] == "2026-11-10"
    assert market_session(datetime(2026, 11, 8, 5, 0, tzinfo=timezone.utc))["state"] == "CLOSED"  # Sunday
