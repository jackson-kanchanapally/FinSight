"""Async client for NSE's official Model Context Protocol servers.

NSE publishes two unauthenticated Streamable-HTTP MCP endpoints:

* ``bhavcopy`` – end-of-day Bhavcopy data (five-year window, updated after close):
  stock history, 52-week range, moving averages, volume analysis, corporate
  actions, index history/valuation, market mood, breadth and movers.
* ``live`` – capital-market live data (1–5 minute delay during market hours):
  stock and index quotes, live gainers/losers and all-stock price watch.

The client keeps one MCP session per server, accepts either JSON or SSE
responses, re-initialises an expired session once, turns tool-level ``{"error"}``
payloads into :class:`NSEMCPError`, caches results briefly, and bounds
concurrency so a dashboard refresh never floods the exchange.

NSE describes the service as informational and educational; it is not intended
for trading or commercial redistribution. Almonds uses it for personal research.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from typing import Any, Literal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

Server = Literal["bhavcopy", "live"]
PROTOCOL_VERSION = "2025-06-18"
MAX_RESPONSE_BYTES = 4_000_000


class NSEMCPError(RuntimeError):
    """A bounded, user-presentable reason for an NSE MCP failure."""

    def __init__(self, reason: str, detail: str | None = None):
        self.reason = reason
        self.detail = (detail or "")[:200] or None
        super().__init__(reason if not self.detail else f"{reason}: {self.detail}")


def _decode(response: httpx.Response) -> dict:
    """Return the JSON-RPC message from a JSON or ``text/event-stream`` body."""
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise NSEMCPError("RESPONSE_TOO_LARGE")
    content_type = response.headers.get("content-type", "")
    try:
        if "text/event-stream" in content_type:
            messages = [
                json.loads(line[5:].strip())
                for line in response.text.splitlines()
                if line.startswith("data:") and line[5:].strip()
            ]
            # Servers may interleave notifications; the response carries an id.
            replies = [m for m in messages if isinstance(m, dict) and "id" in m]
            if not replies:
                raise NSEMCPError("MALFORMED_RESPONSE")
            return replies[-1]
        data = response.json()
    except ValueError as exc:
        raise NSEMCPError("MALFORMED_RESPONSE") from exc
    if not isinstance(data, dict):
        raise NSEMCPError("MALFORMED_RESPONSE")
    return data


def _tool_value(result: dict) -> Any:
    """Unpack an MCP ``tools/call`` result into its JSON value."""
    if result.get("isError"):
        raise NSEMCPError("TOOL_ERROR", _first_text(result))
    if "structuredContent" in result and result["structuredContent"] is not None:
        value = result["structuredContent"]
    else:
        text = _first_text(result)
        if text is None:
            raise NSEMCPError("MALFORMED_RESPONSE")
        try:
            value = json.loads(text)
        except ValueError:
            # A few tools answer with a short plain-text message.
            raise NSEMCPError("TOOL_ERROR", text) from None
    if isinstance(value, dict) and value.get("error"):
        message = str(value["error"])
        reason = "SYMBOL_NOT_FOUND" if "not found" in message.lower() else "TOOL_ERROR"
        raise NSEMCPError(reason, message)
    return value


def _first_text(result: dict) -> str | None:
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                return str(item.get("text", ""))
    return None


class NSEMCPClient:
    """Small, dependency-free MCP client tailored to NSE's two servers."""

    def __init__(
        self,
        *,
        urls: dict[str, str] | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_concurrency: int | None = None,
    ):
        self._urls = urls
        self._timeout = timeout
        self._transport = transport
        self._max_concurrency = max_concurrency
        self._ids = itertools.count(1)
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._reset_loop_state(None)

    # -- configuration -------------------------------------------------
    def url(self, server: Server) -> str:
        if self._urls:
            return self._urls[server]
        return settings.nse_mcp_bhavcopy_url if server == "bhavcopy" else settings.nse_mcp_live_url

    @staticmethod
    def default_ttl(server: Server) -> float:
        if server == "live":
            return float(settings.nse_mcp_live_cache_seconds)
        return float(settings.nse_mcp_eod_cache_minutes * 60)

    # -- event-loop bound resources -----------------------------------
    def _reset_loop_state(self, loop: asyncio.AbstractEventLoop | None) -> None:
        self._loop = loop
        self._http: httpx.AsyncClient | None = None
        self._sessions: dict[str, str] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._semaphore: asyncio.Semaphore | None = None

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            # httpx clients, locks and semaphores belong to one event loop.
            self._reset_loop_state(loop)
            self._semaphore = asyncio.Semaphore(
                self._max_concurrency or settings.nse_mcp_max_concurrency
            )

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=self._timeout or settings.nse_mcp_timeout_seconds,
                transport=self._transport,
                follow_redirects=False,
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                    "User-Agent": "almonds/1.0 (personal research; NSE MCP client)",
                },
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None
        self._sessions.clear()

    def clear_cache(self) -> None:
        self._cache.clear()

    # -- protocol ------------------------------------------------------
    async def _post(self, server: Server, payload: dict, session_id: str | None) -> httpx.Response:
        headers = {}
        if session_id:
            headers = {"Mcp-Session-Id": session_id, "MCP-Protocol-Version": PROTOCOL_VERSION}
        try:
            response = await self._client().post(self.url(server), json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise NSEMCPError("TIMEOUT") from exc
        except httpx.HTTPError as exc:
            raise NSEMCPError("UNAVAILABLE", type(exc).__name__) from exc
        if response.status_code == 429:
            raise NSEMCPError("RATE_LIMITED")
        return response

    async def _session(self, server: Server) -> str:
        if server in self._sessions:
            return self._sessions[server]
        lock = self._session_locks.setdefault(server, asyncio.Lock())
        async with lock:
            if server in self._sessions:
                return self._sessions[server]
            response = await self._post(
                server,
                {
                    "jsonrpc": "2.0",
                    "id": next(self._ids),
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "almonds", "version": "1.0"},
                    },
                },
                None,
            )
            if response.status_code != 200:
                raise NSEMCPError("UNAVAILABLE", f"initialize HTTP {response.status_code}")
            message = _decode(response)
            if not isinstance(message.get("result"), dict):
                raise NSEMCPError("MALFORMED_RESPONSE", "initialize")
            session_id = response.headers.get("mcp-session-id", "")
            notified = await self._post(
                server, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session_id or None
            )
            if notified.status_code >= 400:
                raise NSEMCPError("UNAVAILABLE", f"initialized HTTP {notified.status_code}")
            self._sessions[server] = session_id
            return session_id

    async def _rpc(self, server: Server, method: str, params: dict) -> dict:
        for attempt in range(2):
            session_id = await self._session(server)
            response = await self._post(
                server,
                {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params},
                session_id or None,
            )
            if response.status_code in (400, 404) and attempt == 0:
                # Expired or unknown session: start a new one and retry once.
                self._sessions.pop(server, None)
                continue
            if response.status_code != 200:
                raise NSEMCPError("UNAVAILABLE", f"HTTP {response.status_code}")
            message = _decode(response)
            if message.get("error"):
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise NSEMCPError("PROTOCOL_ERROR", detail)
            result = message.get("result")
            if not isinstance(result, dict):
                raise NSEMCPError("MALFORMED_RESPONSE")
            return result
        raise NSEMCPError("UNAVAILABLE", "session could not be re-established")

    # -- public API ----------------------------------------------------
    async def call(
        self,
        server: Server,
        tool: str,
        arguments: dict | None = None,
        *,
        ttl: float | None = None,
    ) -> Any:
        """Call one NSE MCP tool and return its decoded JSON value."""
        if not settings.nse_mcp_enabled:
            raise NSEMCPError("DISABLED")
        arguments = arguments or {}
        key = (server, tool, json.dumps(arguments, sort_keys=True, default=str))
        ttl = self.default_ttl(server) if ttl is None else ttl
        cached = self._cache.get(key) if ttl > 0 else None
        now = time.monotonic()
        if cached and cached[0] > now:
            return cached[1]
        self._bind_loop()
        assert self._semaphore is not None
        async with self._semaphore:
            started = time.monotonic()
            try:
                result = await self._rpc(server, "tools/call", {"name": tool, "arguments": arguments})
                value = _tool_value(result)
            except NSEMCPError as exc:
                logger.info("nse_mcp.call_failed server=%s tool=%s reason=%s", server, tool, exc.reason)
                raise
            logger.debug("nse_mcp.call server=%s tool=%s ms=%d", server, tool, (time.monotonic() - started) * 1000)
        if ttl > 0:
            if len(self._cache) > 2000:
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            self._cache[key] = (time.monotonic() + ttl, value)
        return value

    async def list_tools(self, server: Server) -> list[dict]:
        if not settings.nse_mcp_enabled:
            raise NSEMCPError("DISABLED")
        self._bind_loop()
        result = await self._rpc(server, "tools/list", {})
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise NSEMCPError("MALFORMED_RESPONSE")
        return tools


_default: NSEMCPClient | None = None


def get_nse_mcp() -> NSEMCPClient:
    """Process-wide client; tests replace it with :func:`set_nse_mcp`."""
    global _default
    if _default is None:
        _default = NSEMCPClient()
    return _default


def set_nse_mcp(client: NSEMCPClient | None) -> None:
    global _default
    _default = client
