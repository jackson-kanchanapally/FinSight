"""Investor views built on NSE's official MCP servers.

Everything here is a factual read of exchange data — prices, ranges, breadth,
valuation percentiles and relative performance. Nothing is a recommendation.
Bhavcopy prices are unadjusted; where a split or bonus can distort a figure the
response says so instead of silently adjusting it.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.market.nse_mcp import NSEMCPError, get_nse_mcp
from app.services.portfolio import get_positions

IST = ZoneInfo("Asia/Kolkata")
SYMBOL = re.compile(r"^[A-Z0-9&-]{1,20}$")
KEY_INDICES = (
    "NIFTY 50",
    "NIFTY BANK",
    "NIFTY NEXT 50",
    "NIFTY MIDCAP 100",
    "NIFTY SMLCAP 100",
    "NIFTY IT",
    "INDIA VIX",
)
SECTOR_PERIODS = {"1D", "1W", "1M", "3M", "6M", "1Y"}
BENCHMARK = "Nifty 50"
DISCLAIMER = (
    "Data from NSE's official MCP servers. NSE provides it for information and "
    "education; it is not investment advice. Bhavcopy prices are not adjusted "
    "for splits or bonuses."
)
CENT = Decimal("0.01")


# -- small helpers -----------------------------------------------------------

def dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def money(value: Decimal | None) -> str | None:
    return None if value is None else str(value.quantize(CENT))


def pct(numerator: Decimal | None, denominator: Decimal | None) -> str | None:
    if numerator is None or not denominator:
        return None
    return str((numerator / denominator * 100).quantize(CENT))


def normalise_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if not SYMBOL.fullmatch(value):
        raise ValueError("Use an NSE symbol of up to 20 letters, digits, & or -")
    return value


def nse_date(value: Any) -> date | None:
    """Date part of NSE stamps such as '09-Nov-2026 15:30:00' or '2026-11-09 16:00:27'."""
    text = str(value or "").strip()[:11]
    for pattern, size in (("%d-%b-%Y", 11), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:size], pattern).date()
        except ValueError:
            continue
    return None


def market_session(now: datetime | None = None, data_date: date | None = None) -> dict:
    """Regular NSE hours by the clock, cross-checked against the data's own date.

    Holidays are not modelled; on a weekday holiday the clock says OPEN but NSE's
    newest data is from the previous session, which ``stale`` reports.
    """
    current = (now or datetime.now(timezone.utc)).astimezone(IST)
    open_now = current.weekday() < 5 and time(9, 15) <= current.time() <= time(15, 30)
    stale = bool(open_now and data_date and data_date < current.date())
    return {
        "state": "OPEN" if open_now else "CLOSED",
        "checked_at": current.isoformat(timespec="seconds"),
        "data_date": data_date.isoformat() if data_date else None,
        "stale": stale,
        "note": (
            "Inside regular hours, but NSE's latest data is from an earlier session (holiday or delayed feed)."
            if stale
            else "Regular hours 09:15–15:30 IST, Mon–Fri; exchange holidays are not checked."
        ),
    }


async def _safe(errors: list[dict], label: str, call: Awaitable[Any]) -> Any:
    try:
        return await call
    except NSEMCPError as exc:
        errors.append({"source": label, "reason": exc.reason, "detail": exc.detail})
        return None


def _rows(value: Any, key: str) -> list[dict]:
    rows = value.get(key) if isinstance(value, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


# -- market pulse --------------------------------------------------------------

def _flatten_indices(payload: Any) -> list[dict]:
    groups = payload.get("indices") if isinstance(payload, dict) else None
    flat: list[dict] = []
    if isinstance(groups, dict):
        for group, rows in groups.items():
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get("indexName"):
                    flat.append({**row, "group": group})
    return flat


def _index_view(row: dict) -> dict:
    return {
        "name": row["indexName"],
        "group": row.get("group"),
        "last": row.get("last"),
        "previous_close": row.get("previousClose"),
        "change": row.get("change"),
        "change_pct": row.get("percChange"),
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "as_of": row.get("timeVal"),
    }


async def market_pulse(sector_period: str = "1D") -> dict:
    period = sector_period.upper()
    if period not in SECTOR_PERIODS:
        raise ValueError(f"sector_period must be one of {sorted(SECTOR_PERIODS)}")
    client = get_nse_mcp()
    errors: list[dict] = []
    live, mood, valuation, sectors, movers = await asyncio.gather(
        _safe(errors, "Live indices", client.call("live", "cm_get_live_indices", {"group": "", "nameFilter": ""})),
        _safe(errors, "Market mood", client.call("bhavcopy", "get_market_mood", {"date": "today"})),
        _safe(
            errors,
            "Nifty 50 valuation",
            client.call("bhavcopy", "get_index_valuation", {"indexName": BENCHMARK, "months": 60, "date": "today"}),
        ),
        _safe(
            errors,
            "Sector movers",
            client.call("bhavcopy", "get_index_movers", {"date": "today", "period": period, "n": 6, "scope": "equity"}),
        ),
        _safe(errors, "Stock movers", client.call("live", "nse_get_market_movers", {"indexName": "", "limit": 8})),
    )
    flat = {row["indexName"].upper(): row for row in _flatten_indices(live)}
    key_indices = [_index_view(flat[name]) for name in KEY_INDICES if name in flat]
    if live and not key_indices:
        errors.append({"source": "Live indices", "reason": "KEY_INDICES_MISSING", "detail": None})

    def mover(row: dict) -> dict:
        return {
            "symbol": row.get("symbol"),
            "series": row.get("series"),
            "ltp": row.get("ltp"),
            "previous_close": row.get("prev_price"),
            "change_pct": row.get("perChange"),
            "volume": row.get("trade_quantity"),
            "turnover": row.get("turnover"),
        }

    return {
        "session": market_session(data_date=nse_date(live.get("nseTimestamp")) if isinstance(live, dict) else None),
        "indices": {
            "as_of": live.get("nseTimestamp") if isinstance(live, dict) else None,
            "items": key_indices,
            "available": len(flat),
        },
        "mood": (
            {
                key: mood.get(key)
                for key in ("date", "benchmarks", "index_breadth", "stock_breadth", "india_vix", "method")
            }
            if isinstance(mood, dict)
            else None
        ),
        "valuation": (
            {
                key: valuation.get(key)
                for key in ("index", "date", "close", "pe", "pb", "div_yield_pct", "window_from", "window_to", "method")
            }
            if isinstance(valuation, dict)
            else None
        ),
        "sector_movers": (
            {
                "date": sectors.get("date"),
                "base_date": sectors.get("base_date"),
                "period": period,
                "gainers": _rows(sectors, "gainers"),
                "losers": _rows(sectors, "losers"),
            }
            if isinstance(sectors, dict)
            else None
        ),
        "stock_movers": (
            {
                "updated_at": movers.get("gainersUpdatedAt"),
                "gainers": [mover(row) for row in _rows(movers, "gainers")],
                "losers": [mover(row) for row in _rows(movers, "losers")],
            }
            if isinstance(movers, dict)
            else None
        ),
        "errors": errors,
        "disclaimer": DISCLAIMER,
    }


# -- quotes ------------------------------------------------------------------

def _live_quote(payload: Any) -> dict | None:
    stock = payload.get("stock") if isinstance(payload, dict) else None
    if not isinstance(stock, dict):
        return None
    ltp, previous = dec(stock.get("lastTradedPrice")), dec(stock.get("preClosePrice"))
    if not ltp or ltp <= 0:
        return None
    return {
        "source": "LIVE",
        "price": ltp,
        "previous_close": previous if previous and previous > 0 else None,
        "change_pct": dec(stock.get("perChange")),
        "open": dec(stock.get("openPrice")),
        "high": dec(stock.get("highPrice")),
        "low": dec(stock.get("lowPrice")),
        "volume": stock.get("volume"),
        "value_cr": stock.get("value"),
        "week52_high": dec(stock.get("fiftyTwoWeekHigh")),
        "week52_low": dec(stock.get("fiftyTwoWeekLow")),
        "change_30d_pct": dec(stock.get("perChange30d")),
        "series": stock.get("series"),
        "as_of": stock.get("latestTimestamp"),
    }


def _eod_quote(row: dict) -> dict | None:
    close = dec(row.get("close"))
    if not close or close <= 0:
        return None
    previous = dec(row.get("prev_close"))
    return {
        "source": "EOD",
        "price": close,
        "previous_close": previous if previous and previous > 0 else None,
        "change_pct": dec(row.get("pct_change")),
        "open": dec(row.get("open")),
        "high": dec(row.get("high")),
        "low": dec(row.get("low")),
        "volume": row.get("volume"),
        "value_cr": None,
        "week52_high": None,
        "week52_low": None,
        "change_30d_pct": None,
        "series": None,
        "as_of": row.get("date"),
    }


async def quotes_for(symbols: list[str]) -> tuple[dict[str, dict], list[dict]]:
    """Live quotes first; one Bhavcopy bulk call fills any gaps."""
    client = get_nse_mcp()
    errors: list[dict] = []
    unique = list(dict.fromkeys(symbols))
    live = await asyncio.gather(
        *(_safe(errors, f"Live quote {s}", client.call("live", "cm_get_stock_quote", {"symbol": s})) for s in unique)
    )
    quotes = {s: q for s, q in ((s, _live_quote(p)) for s, p in zip(unique, live)) if q}
    missing = [s for s in unique if s not in quotes]
    for start in range(0, len(missing), 50):
        chunk = missing[start : start + 50]
        bulk = await _safe(errors, "EOD bulk quote", client.call("bhavcopy", "get_bulk_quote", {"symbols": chunk}))
        for row in _rows(bulk, "quotes"):
            symbol = str(row.get("symbol", "")).upper()
            quote = _eod_quote(row)
            if symbol in chunk and quote:
                quotes[symbol] = quote
    # Live misses that EOD recovered are not user-facing failures.
    errors = [e for e in errors if not (e["source"].startswith("Live quote ") and e["source"][11:] in quotes)]
    return quotes, errors


def _quote_json(quote: dict) -> dict:
    return {
        key: (money(value) if isinstance(value, Decimal) and key not in {"change_pct", "change_30d_pct"} else
              (str(value) if isinstance(value, Decimal) else value))
        for key, value in quote.items()
    }


# -- portfolio -------------------------------------------------------------------

async def portfolio_live(db: Session) -> dict:
    positions = [p for p in get_positions(db) if p.quantity > 0]
    nse_positions = [p for p in positions if p.company.exchange.strip().upper() == "NSE"]
    symbols = [p.company.symbol.upper() for p in nse_positions]
    client = get_nse_mcp()
    quotes, errors = await quotes_for(symbols) if symbols else ({}, [])
    risk_rows = await asyncio.gather(
        *(
            _safe(
                errors,
                f"Relative performance {s}",
                client.call(
                    "bhavcopy",
                    "get_stock_vs_index",
                    {"symbol": s, "indexName": BENCHMARK, "months": 12, "date": "today"},
                ),
            )
            for s in symbols
        )
    )
    risk = dict(zip(symbols, risk_rows))

    holdings: list[dict] = []
    total_value = total_cost_priced = total_day = Decimal(0)
    beta_weight = beta_value = Decimal(0)
    for position in positions:
        symbol = position.company.symbol.upper()
        quote = quotes.get(symbol)
        row: dict[str, Any] = {
            "symbol": symbol,
            "name": position.company.name,
            "quantity": str(position.quantity),
            "cost_basis": money(position.cost_basis),
            "average_purchase_price": money(position.average_price),
            "quote": _quote_json(quote) if quote else None,
            "market_value": None,
            "unrealised_pnl": None,
            "unrealised_pnl_pct": None,
            "day_change_value": None,
            "from_52w_high_pct": None,
            "relative": None,
            "status": "PRICED" if quote else (
                "NOT_NSE" if position.company.exchange.strip().upper() != "NSE" else "UNPRICED"
            ),
        }
        if quote:
            value = position.quantity * quote["price"]
            unrealised = value - position.cost_basis
            row["market_value"] = money(value)
            row["unrealised_pnl"] = money(unrealised)
            row["unrealised_pnl_pct"] = pct(unrealised, position.cost_basis)
            total_value += value
            total_cost_priced += position.cost_basis
            if quote["previous_close"]:
                day = position.quantity * (quote["price"] - quote["previous_close"])
                row["day_change_value"] = money(day)
                total_day += day
            if quote["week52_high"]:
                row["from_52w_high_pct"] = pct(quote["price"] - quote["week52_high"], quote["week52_high"])
        relative = risk.get(symbol)
        if isinstance(relative, dict):
            beta = dec(relative.get("beta"))
            row["relative"] = {
                "benchmark": BENCHMARK,
                "months": relative.get("months"),
                "base_date": relative.get("base_date"),
                "date": relative.get("date"),
                "stock_return_pct": (relative.get("stock") or {}).get("return_pct"),
                "index_return_pct": (relative.get("index") or {}).get("return_pct"),
                "outperformance_pct_points": relative.get("outperformance_pct_points"),
                "beta": relative.get("beta"),
                "correlation": relative.get("correlation"),
            }
            if beta is not None and quote:
                beta_weight += position.quantity * quote["price"]
                beta_value += position.quantity * quote["price"] * beta
        holdings.append(row)

    priced = [h for h in holdings if h["status"] == "PRICED"]
    previous_value = total_value - total_day
    holdings.sort(key=lambda h: dec(h["market_value"]) or Decimal(0), reverse=True)
    for row in holdings:
        value = dec(row["market_value"])
        row["weight_pct"] = pct(value, total_value) if value is not None else None
    quote_dates = [d for d in (nse_date(q["as_of"]) for q in quotes.values()) if d]
    return {
        "session": market_session(data_date=max(quote_dates) if quote_dates else None),
        "holdings": holdings,
        "totals": {
            "market_value": money(total_value) if priced else None,
            "cost_basis_priced": money(total_cost_priced) if priced else None,
            "unrealised_pnl": money(total_value - total_cost_priced) if priced else None,
            "unrealised_pnl_pct": pct(total_value - total_cost_priced, total_cost_priced) if priced else None,
            "day_change_value": money(total_day) if priced else None,
            "day_change_pct": pct(total_day, previous_value) if priced and previous_value else None,
            "beta": str((beta_value / beta_weight).quantize(Decimal("0.001"))) if beta_weight else None,
            "beta_coverage_pct": pct(beta_weight, total_value) if beta_weight else None,
            "priced_positions": len(priced),
            "open_positions": len(holdings),
        },
        "errors": errors,
        "disclaimer": DISCLAIMER,
    }


# -- one stock ---------------------------------------------------------------

DIVIDEND = re.compile(r"(?:Rs\.?|₹)\s*([0-9]+(?:\.[0-9]+)?)", re.I)


def _dividends(actions: list[dict], today: date, price: Decimal | None) -> dict | None:
    year_ago = today - timedelta(days=365)
    paid = Decimal(0)
    events = 0
    for action in actions:
        if action.get("actionType") != "DIVIDEND":
            continue
        try:
            ex_date = date.fromisoformat(str(action.get("exDate")))
        except ValueError:
            continue
        if not year_ago < ex_date <= today:
            continue
        amounts = [dec(m) for m in DIVIDEND.findall(str(action.get("purpose", "")))]
        amounts = [a for a in amounts if a is not None]
        if amounts:
            paid += sum(amounts, Decimal(0))
            events += 1
    if not events:
        return None
    return {
        "trailing_12m_per_share": money(paid),
        "events": events,
        "yield_pct": pct(paid, price) if price else None,
        "basis": "Sum of NSE dividend ex-dates in the last 365 days ÷ current price.",
    }


async def stock_insight(symbol: str) -> dict:
    symbol = normalise_symbol(symbol)
    client = get_nse_mcp()
    today = datetime.now(IST).date()
    errors: list[dict] = []
    jobs = {
        "quote": ("Live quote", client.call("live", "cm_get_stock_quote", {"symbol": symbol})),
        "range": ("52-week range", client.call("bhavcopy", "get_52_week_high_low", {"symbol": symbol})),
        "sma20": ("20-day average", client.call("bhavcopy", "moving_average", {"symbol": symbol, "days": 20})),
        "sma50": ("50-day average", client.call("bhavcopy", "moving_average", {"symbol": symbol, "days": 50})),
        "sma200": ("200-day average", client.call("bhavcopy", "moving_average", {"symbol": symbol, "days": 200})),
        "volume": ("Volume analysis", client.call("bhavcopy", "get_volume_analysis", {"symbol": symbol, "days": 30})),
        "relative": (
            "Relative to Nifty 50",
            client.call(
                "bhavcopy",
                "get_stock_vs_index",
                {"symbol": symbol, "indexName": BENCHMARK, "months": 12, "date": "today"},
            ),
        ),
        "actions": (
            "Corporate actions",
            client.call(
                "bhavcopy",
                "get_corporate_actions",
                {
                    "symbol": symbol,
                    "fromDate": (today - timedelta(days=3 * 365)).isoformat(),
                    "toDate": (today + timedelta(days=180)).isoformat(),
                },
            ),
        ),
        "history": (
            "Price history",
            client.call("bhavcopy", "get_stock_history", {"symbol": symbol, "months": 3, "endDate": "today"}),
        ),
    }
    values = await asyncio.gather(*(_safe(errors, label, call) for label, call in jobs.values()))
    data = dict(zip(jobs, values))
    not_found = {e["source"] for e in errors if e["reason"] == "SYMBOL_NOT_FOUND"}
    if data["quote"] is None and data["range"] is None and data["history"] is None and not_found:
        raise LookupError(f"{symbol} was not found on NSE")

    quote = _live_quote(data["quote"])
    history_rows = _rows(data["history"], "data")
    if quote is None and history_rows:
        last = history_rows[-1]
        quote = _eod_quote(
            {"close": last.get("close"), "prev_close": last.get("prevClose"), "open": last.get("open"),
             "high": last.get("high"), "low": last.get("low"), "volume": last.get("volume"),
             "date": last.get("date"),
             "pct_change": pct(dec(last.get("close")) - dec(last.get("prevClose")), dec(last.get("prevClose")))
             if dec(last.get("close")) is not None and dec(last.get("prevClose")) else None}
        )
    price = quote["price"] if quote else dec((data["range"] or {}).get("last_close"))

    actions = _rows(data["actions"], "actions")
    recent_cutoff = today - timedelta(days=400)
    distorting = []
    for action in actions:
        if action.get("actionType") not in {"SPLIT", "BONUS"}:
            continue
        try:
            ex_date = date.fromisoformat(str(action.get("exDate")))
        except ValueError:
            continue
        if recent_cutoff <= ex_date <= today:
            distorting.append(action)
    warnings = []
    if distorting:
        events = ", ".join(f"{a.get('purpose') or a.get('actionType')} (ex {a.get('exDate')})" for a in distorting)
        warnings.append(
            f"{events}: the 52-week range, moving averages and price history use unadjusted prices "
            "and are distorted across that date. Relative performance and beta are adjusted by NSE."
        )
    if data["actions"] is None:
        warnings.append("Corporate actions could not be checked, so unadjusted-price distortions cannot be ruled out.")

    averages = []
    for days in (20, 50, 200):
        value = data[f"sma{days}"]
        average = dec(value if not isinstance(value, dict) else value.get("sma") or value.get("value"))
        if average and average > 0:
            averages.append({
                "days": days,
                "value": money(average),
                "price_vs_average_pct": pct(price - average, average) if price else None,
            })
    by_days = {a["days"]: dec(a["value"]) for a in averages}
    trend = None
    if 50 in by_days and 200 in by_days:
        trend = "50-day average above 200-day" if by_days[50] > by_days[200] else "50-day average below 200-day"

    week_range = data["range"] if isinstance(data["range"], dict) else None
    volume = data["volume"] if isinstance(data["volume"], dict) else None
    relative = data["relative"] if isinstance(data["relative"], dict) else None
    return {
        "symbol": symbol,
        "session": market_session(data_date=nse_date(quote["as_of"]) if quote else None),
        "quote": _quote_json(quote) if quote else None,
        "range_52w": (
            {
                "high": week_range.get("52w_high"),
                "high_date": week_range.get("52w_high_date"),
                "low": week_range.get("52w_low"),
                "low_date": week_range.get("52w_low_date"),
                "position_pct": week_range.get("position_in_range_pct"),
                "from_high_pct": week_range.get("from_52w_high_pct"),
                "from_low_pct": week_range.get("from_52w_low_pct"),
                "last_close": week_range.get("last_close"),
            }
            if week_range
            else None
        ),
        "moving_averages": averages,
        "trend": trend,
        "volume": (
            {
                key: volume.get(key)
                for key in ("days_analysed", "avg_volume", "last_volume", "last_vs_avg_pct",
                            "volume_spike_days", "recent_5_days")
            }
            if volume
            else None
        ),
        "relative": (
            {
                "benchmark": BENCHMARK,
                "base_date": relative.get("base_date"),
                "date": relative.get("date"),
                "stock_return_pct": (relative.get("stock") or {}).get("return_pct"),
                "index_return_pct": (relative.get("index") or {}).get("return_pct"),
                "outperformance_pct_points": relative.get("outperformance_pct_points"),
                "beta": relative.get("beta"),
                "correlation": relative.get("correlation"),
                "method": relative.get("method"),
            }
            if relative
            else None
        ),
        "dividends": _dividends(actions, today, price),
        "corporate_actions": {
            "upcoming": [a for a in actions if str(a.get("exDate", "")) > today.isoformat()],
            "recent": sorted(
                (a for a in actions if str(a.get("exDate", "")) <= today.isoformat()),
                key=lambda a: str(a.get("exDate")),
                reverse=True,
            )[:8],
        } if data["actions"] is not None else None,
        "history": {
            "from_date": (data["history"] or {}).get("from_date"),
            "to_date": (data["history"] or {}).get("to_date"),
            "summary": (data["history"] or {}).get("summary"),
            "points": [
                {"date": row.get("date"), "close": row.get("close"), "volume": row.get("volume")}
                for row in history_rows
            ],
        } if history_rows else None,
        "warnings": warnings,
        "errors": errors,
        "disclaimer": DISCLAIMER,
    }


# -- symbols ---------------------------------------------------------------------

async def search_symbols(query: str) -> dict:
    text = query.strip()
    if not 1 <= len(text) <= 40 or not re.fullmatch(r"[A-Za-z0-9&.\- ]+", text):
        raise ValueError("Search with 1–40 letters, digits, spaces, &, . or -")
    client = get_nse_mcp()
    # NSE search is symbol-oriented: "tata motors" finds nothing but "TATAMOTORS"
    # and "TATA" do. Try the text, its compact form, then its first word.
    attempts = list(dict.fromkeys(
        q for q in (text.upper(), text.replace(" ", "").upper(), text.split()[0].upper()) if q
    ))
    rows: list[dict] = []
    for attempt in attempts:
        result = await client.call("bhavcopy", "search_symbols", {"query": attempt})
        rows = _rows(result, "results")
        if rows:
            break
    stale_before = (datetime.now(IST).date() - timedelta(days=14)).isoformat()
    for row in rows:
        # Symbols that stopped trading (mergers, delistings) still appear in search.
        row["inactive"] = bool(row.get("last_date")) and str(row["last_date"]) < stale_before
    rows.sort(key=lambda r: (r["inactive"], str(r.get("symbol"))))
    compact = text.replace(" ", "").upper()
    exact = next((r for r in rows if str(r.get("symbol", "")).upper() == compact), None)
    return {"query": text, "results": rows[:20], "exact_match": exact}


async def server_status() -> dict:
    client = get_nse_mcp()
    servers = []
    for name in ("bhavcopy", "live"):
        entry: dict[str, Any] = {"name": name, "url": client.url(name)}  # type: ignore[arg-type]
        try:
            tools = await client.list_tools(name)  # type: ignore[arg-type]
            entry.update(ok=True, tools=sorted(str(t.get("name")) for t in tools if isinstance(t, dict)))
        except NSEMCPError as exc:
            entry.update(ok=False, reason=exc.reason, detail=exc.detail, tools=[])
        servers.append(entry)
    return {"servers": servers, "disclaimer": DISCLAIMER}
