"""Replaceable, bounded sources of dated closing prices."""

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from contextlib import nullcontext
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx
from app.ingestion.retry_policy import retry_after_seconds

from app.models.company import Company

INDIA = ZoneInfo("Asia/Kolkata")


class MarketProviderError(ValueError):
    def __init__(self, reason: str, retry_after_seconds=None):
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds
        super().__init__(reason)


@dataclass(frozen=True)
class PriceObservation:
    market_date: date
    close_price: Decimal
    observed_at: datetime
    fetched_at: datetime
    provider: str
    provider_symbol: str | None
    source_url: str
    data_origin: str = "EOD"
    currency: str = "INR"
    open_price: Decimal | None = None
    high_price: Decimal | None = None
    low_price: Decimal | None = None
    volume: int | None = None
    note: str | None = None


class MarketDataProvider(Protocol):
    name: str

    def get_quote(self, company: Company) -> PriceObservation: ...

    def get_market_snapshot(self, company: Company) -> PriceObservation: ...

    def get_price_history(
        self, company: Company, start_date: date, end_date: date
    ) -> list[PriceObservation]: ...


class ManualMarketDataProvider:
    name = "MANUAL"

    def get_quote(self, company: Company) -> PriceObservation:
        raise MarketProviderError("MANUAL_ENTRY_REQUIRED")

    get_market_snapshot = get_quote

    def get_price_history(
        self, company: Company, start_date: date, end_date: date
    ) -> list[PriceObservation]:
        raise MarketProviderError("MANUAL_ENTRY_REQUIRED")

    @staticmethod
    def observation(
        market_date: date, close_price: Decimal, source_url: str
    ) -> PriceObservation:
        return PriceObservation(
            market_date,
            close_price,
            datetime.combine(market_date, time(15, 30), INDIA),
            datetime.now(timezone.utc),
            "MANUAL",
            None,
            source_url,
            "MANUAL",
        )


class NSEMarketDataProvider:
    """Official NSE MCP Bhavcopy. Historical prices are raw, unadjusted EOD."""

    name = "NSE_MCP"
    endpoint = "https://mcp.nseindia.in/bhavcopy/cm/mcp"

    def __init__(self, timeout: float = 12, client: httpx.Client | None = None):
        self.timeout = timeout
        self.client = client

    @staticmethod
    def symbol_for(company: Company) -> str:
        symbol = company.symbol.strip().upper()
        if company.exchange.upper() != "NSE" or not re.fullmatch(
            r"[A-Z0-9&-]{1,20}", symbol
        ):
            raise MarketProviderError("NSE_SYMBOL_REQUIRED")
        return symbol

    def _rpc(
        self,
        client: httpx.Client,
        headers: dict,
        method: str,
        params: dict,
        request_id: int,
    ) -> dict:
        try:
            response = client.post(
                self.endpoint,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
            )
            if response.status_code == 429:
                raise MarketProviderError("RATE_LIMITED", retry_after_seconds(response.headers))
            response.raise_for_status()
            if method == "initialize":
                self._last_session_id = response.headers.get("mcp-session-id")
            if "text/event-stream" in response.headers.get("content-type", ""):
                payload = next(
                    (
                        line[5:].strip()
                        for line in response.text.splitlines()
                        if line.startswith("data:")
                    ),
                    None,
                )
                data = json.loads(payload) if payload else None
            else:
                data = response.json()
        except httpx.TimeoutException as exc:
            raise MarketProviderError("PROVIDER_TIMEOUT") from exc
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise MarketProviderError("PROVIDER_UNAVAILABLE") from exc
        except MarketProviderError:
            raise
        except (ValueError, StopIteration) as exc:
            raise MarketProviderError("MALFORMED_RESPONSE") from exc
        if not isinstance(data, dict):
            raise MarketProviderError("MALFORMED_RESPONSE")
        if data.get("error"):
            raise MarketProviderError("PROVIDER_UNAVAILABLE")
        result = data.get("result")
        if not isinstance(result, dict):
            raise MarketProviderError("MALFORMED_RESPONSE")
        return result

    def _session(self, client: httpx.Client) -> dict:
        result = self._rpc(
            client,
            {},
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "almonds", "version": "0.6"},
            },
            1,
        )
        # The official server currently uses the 2025-06-18 session protocol.
        if result.get("protocolVersion") != "2025-06-18":
            raise MarketProviderError("UNSUPPORTED_MCP_VERSION")
        session_id = getattr(self, "_last_session_id", None)
        if not session_id:
            raise MarketProviderError("MALFORMED_RESPONSE")
        headers = {"mcp-session-id": session_id, "mcp-protocol-version": "2025-06-18"}
        try:
            notification = client.post(
                self.endpoint,
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            notification.raise_for_status()
        except (
            httpx.TimeoutException,
            httpx.RequestError,
            httpx.HTTPStatusError,
        ) as exc:
            raise MarketProviderError("PROVIDER_UNAVAILABLE") from exc
        return headers

    def _call(
        self, client: httpx.Client, headers: dict, name: str, arguments: dict
    ) -> dict:
        result = self._rpc(
            client, headers, "tools/call", {"name": name, "arguments": arguments}, 2
        )
        if result.get("isError"):
            raise MarketProviderError("PROVIDER_UNAVAILABLE")
        content = result.get("content")
        if (
            not isinstance(content, list)
            or not content
            or not isinstance(content[0], dict)
            or content[0].get("type") != "text"
        ):
            raise MarketProviderError("MALFORMED_RESPONSE")
        try:
            value = json.loads(content[0]["text"])
        except (ValueError, TypeError, KeyError) as exc:
            raise MarketProviderError("MALFORMED_RESPONSE") from exc
        if not isinstance(value, dict):
            raise MarketProviderError("MALFORMED_RESPONSE")
        if value.get("error"):
            raise MarketProviderError(
                "SYMBOL_NOT_FOUND"
                if "symbol" in str(value["error"]).lower()
                else "PROVIDER_UNAVAILABLE"
            )
        return value

    @staticmethod
    def _decimal(value: object, *, positive: bool = True) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise MarketProviderError("MALFORMED_RESPONSE") from exc
        if (
            not result.is_finite()
            or result > Decimal("10000000")
            or (result <= 0 if positive else result < 0)
        ):
            raise MarketProviderError("MALFORMED_RESPONSE")
        return result

    def get_price_history(
        self, company: Company, start_date: date, end_date: date
    ) -> list[PriceObservation]:
        symbol = self.symbol_for(company)
        if start_date > end_date or end_date > datetime.now(INDIA).date():
            raise MarketProviderError("INVALID_DATE_RANGE")
        observations: dict[date, PriceObservation] = {}
        client_context = (
            nullcontext(self.client)
            if self.client
            else httpx.Client(
                timeout=self.timeout,
                headers={"Accept": "application/json, text/event-stream"},
            )
        )
        with client_context as client:
            headers = self._session(client)
            actions = self._call(
                client,
                headers,
                "get_corporate_actions",
                {
                    "symbol": symbol,
                    "fromDate": start_date.isoformat(),
                    "toDate": end_date.isoformat(),
                },
            )
            action_rows = actions.get("actions")
            if not isinstance(action_rows, list) or any(
                not isinstance(item, dict) for item in action_rows
            ):
                raise MarketProviderError("MALFORMED_RESPONSE")
            action_dates = {
                item.get("exDate")
                for item in action_rows
                if item.get("actionType") in {"SPLIT", "BONUS"}
            }
            cursor = end_date
            for _ in range(21):  # At most five years, three months per request.
                if cursor < start_date:
                    break
                data = self._call(
                    client,
                    headers,
                    "get_stock_history",
                    {"symbol": symbol, "months": 3, "endDate": cursor.isoformat()},
                )
                rows = data.get("data")
                if not isinstance(rows, list):
                    raise MarketProviderError("MALFORMED_RESPONSE")
                for row in rows:
                    try:
                        if not isinstance(row, dict):
                            raise MarketProviderError("MALFORMED_RESPONSE")
                        if row["symbol"] != symbol:
                            raise MarketProviderError("MALFORMED_RESPONSE")
                        day = date.fromisoformat(row["date"])
                        close = self._decimal(row["close"])
                        opening = self._decimal(row["open"])
                        high = self._decimal(row["high"])
                        low = self._decimal(row["low"])
                        raw_volume = row["volume"]
                        if (
                            isinstance(raw_volume, bool)
                            or not str(raw_volume).isdigit()
                        ):
                            raise MarketProviderError("MALFORMED_RESPONSE")
                        volume = int(raw_volume)
                        if (
                            volume < 0
                            or volume > 10**13
                            or high < max(opening, close, low)
                            or low > min(opening, close)
                            or day > cursor
                        ):
                            raise MarketProviderError("MALFORMED_RESPONSE")
                    except (KeyError, TypeError, ValueError) as exc:
                        raise MarketProviderError("MALFORMED_RESPONSE") from exc
                    if start_date <= day <= end_date:
                        observations[day] = PriceObservation(
                            day,
                            close,
                            datetime.combine(day, time(15, 30), INDIA),
                            datetime.now(timezone.utc),
                            self.name,
                            symbol,
                            self.endpoint,
                            "EOD",
                            "INR",
                            opening,
                            high,
                            low,
                            volume,
                            (
                                "Unadjusted NSE Bhavcopy; split/bonus in range"
                                if action_dates
                                else "Unadjusted NSE Bhavcopy"
                            ),
                        )
                next_date = data.get("next_end_date")
                try:
                    following = date.fromisoformat(next_date) if next_date else None
                except ValueError as exc:
                    raise MarketProviderError("MALFORMED_RESPONSE") from exc
                if not rows or following is None or following >= cursor:
                    break
                cursor = following
        return [observations[day] for day in sorted(observations)]

    def get_quote(self, company: Company) -> PriceObservation:
        from datetime import timedelta

        today = datetime.now(INDIA).date()
        history = self.get_price_history(company, today - timedelta(days=14), today)
        if not history:
            raise MarketProviderError("NO_RECENT_PRICE")
        return history[-1]

    get_market_snapshot = get_quote


class AlphaVantageProvider:
    """Documented free daily API; Indian coverage uses a mapped BSE code."""

    name = "ALPHA_VANTAGE"
    endpoint = "https://www.alphavantage.co/query"

    def __init__(
        self, api_key: str, timeout: float = 12, client: httpx.Client | None = None
    ):
        self.api_key = api_key
        self.timeout = timeout
        self.client = client

    @staticmethod
    def symbol_for(company: Company) -> str:
        code = company.bse_code or (
            company.symbol
            if company.exchange.upper() == "BSE" and company.symbol.isdigit()
            else None
        )
        if not code or len(code) != 6 or not code.isdigit():
            raise MarketProviderError("BSE_CODE_REQUIRED")
        return f"{code}.BSE"

    def get_quote(self, company: Company) -> PriceObservation:
        from datetime import timedelta

        today = datetime.now(INDIA).date()
        quotes = self.get_price_history(company, today - timedelta(days=14), today)
        if not quotes:
            raise MarketProviderError("NO_RECENT_PRICE")
        return quotes[-1]

    get_market_snapshot = get_quote

    def get_price_history(
        self, company: Company, start_date: date, end_date: date
    ) -> list[PriceObservation]:
        if not self.api_key:
            raise MarketProviderError("PROVIDER_NOT_CONFIGURED")
        if start_date > end_date:
            raise MarketProviderError("INVALID_DATE_RANGE")
        symbol = self.symbol_for(company)
        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": "compact",
            "apikey": self.api_key,
        }
        try:
            response = (self.client or httpx).get(
                self.endpoint, params=params, timeout=self.timeout
            )
            if response.status_code == 429:
                raise MarketProviderError("RATE_LIMITED")
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise MarketProviderError("PROVIDER_TIMEOUT") from exc
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            raise MarketProviderError("PROVIDER_UNAVAILABLE") from exc
        except MarketProviderError:
            raise
        except ValueError as exc:
            raise MarketProviderError("MALFORMED_RESPONSE") from exc
        if not isinstance(data, dict):
            raise MarketProviderError("MALFORMED_RESPONSE")
        if "Note" in data or "Information" in data:
            raise MarketProviderError("RATE_LIMITED")
        if "Error Message" in data:
            raise MarketProviderError("SYMBOL_NOT_FOUND")
        series = data.get("Time Series (Daily)")
        if not isinstance(series, dict):
            raise MarketProviderError("MALFORMED_RESPONSE")
        fetched_at = datetime.now(timezone.utc)
        source_url = f"https://www.alphavantage.co/documentation/#daily"
        observations = []
        for day, values in series.items():
            try:
                market_date = date.fromisoformat(day)
                close = Decimal(values["4. close"])
            except (TypeError, ValueError, KeyError, InvalidOperation) as exc:
                raise MarketProviderError("MALFORMED_RESPONSE") from exc
            if close <= 0 or market_date > fetched_at.astimezone(INDIA).date():
                raise MarketProviderError("MALFORMED_RESPONSE")
            if start_date <= market_date <= end_date:
                observations.append(
                    PriceObservation(
                        market_date,
                        close,
                        datetime.combine(market_date, time(15, 30), INDIA),
                        fetched_at,
                        self.name,
                        symbol,
                        source_url,
                    )
                )
        return sorted(observations, key=lambda item: item.market_date)
