"""NSE's public corporate-announcements RSS feed.

The feed is linked from https://www.nseindia.com/static/rss-feed. Its items
contain issuer names, not symbols. The officially linked securities CSVs map
registered symbols to exact issuer names before RSS matching.
"""

import asyncio
import csv
import io
import re
from dataclasses import replace
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from app.config import settings
from app.ingestion.downloader import SafePdfDownloader
from app.ingestion.sources.base import (
    EventSource,
    MonitoredCompany,
    SourceEvent,
    SourceUnavailableError,
)

FEED_URL = "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"
EQUITY_DIRECTORY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
SME_DIRECTORY_URL = (
    "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"
)
DOCUMENT_HOSTS = {"nsearchives.nseindia.com", "archives.nseindia.com"}
MAX_FEED_BYTES = 5_000_000
MAX_DIRECTORY_BYTES = 2_000_000


def _issuer_key(value: str) -> str:
    return " ".join(value.casefold().split())


def _trusted_link(url: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname in DOCUMENT_HOSTS
        and parts.username is None
        and parts.password is None
        and parts.port in (None, 443)
    )


def parse_nse_announcements(
    payload: bytes, companies: list[MonitoredCompany]
) -> list[SourceEvent]:
    if (
        len(payload) > MAX_FEED_BYTES
        or b"<!DOCTYPE" in payload.upper()
        or b"<!ENTITY" in payload.upper()
    ):
        raise ValueError("NSE feed is too large or contains forbidden XML declarations")
    try:
        root = ElementTree.fromstring(payload)
        channel = root.find("channel") if root.tag == "rss" else None
        if channel is None:
            raise ValueError("Expected NSE RSS channel")
        build_date = parsedate_to_datetime(channel.findtext("lastBuildDate") or "")
        if build_date.tzinfo is None:
            raise ValueError("NSE feed has no timezone")
        configured: dict[str, list[MonitoredCompany]] = {}
        for company in companies:
            if company.source_company_id:
                configured.setdefault(
                    _issuer_key(company.source_company_id), []
                ).append(company)
        events: list[SourceEvent] = []
        for item in channel.findall("item"):
            issuer = " ".join((item.findtext("title") or "").split())
            matches = configured.get(_issuer_key(issuer), [])
            if len(matches) != 1:
                continue
            company = matches[0]
            link = (item.findtext("link") or "").strip()
            description = " ".join((item.findtext("description") or "").split())
            raw_date = (item.findtext("pubDate") or "").strip()
            if not _trusted_link(link) or not description or not raw_date:
                raise ValueError(
                    "Matched NSE item is missing a trusted link, description, or date"
                )
            published = datetime.strptime(raw_date, "%d-%b-%Y %H:%M:%S").replace(
                tzinfo=build_date.tzinfo
            )
            subject = (
                description.rsplit("|SUBJECT:", 1)[-1].strip()
                if "|SUBJECT:" in description
                else description
            )
            subject = re.sub(r"\s+", " ", subject).strip()
            if not subject:
                raise ValueError("Matched NSE item has no subject")
            events.append(
                SourceEvent(
                    external_symbol=company.external_symbol,
                    title=subject[:500],
                    description=description,
                    published_at=published,
                    external_id=(item.findtext("guid") or link).strip(),
                    source_url=link,
                    document_url=(
                        link if urlsplit(link).path.lower().endswith(".pdf") else None
                    ),
                    raw_payload={
                        "feed_url": FEED_URL,
                        "issuer_title": issuer,
                        "description": description,
                        "pubDate": raw_date,
                    },
                )
            )
        return events
    except (ElementTree.ParseError, TypeError, ValueError, OverflowError) as exc:
        raise SourceUnavailableError(
            "NSE RSS format is unavailable or malformed"
        ) from exc


def parse_nse_directory(payload: bytes) -> dict[str, list[tuple[str, str | None]]]:
    """Parse NSE's publicly linked equity/SME CSV without guessing issuer names."""
    if len(payload) > MAX_DIRECTORY_BYTES:
        raise SourceUnavailableError("NSE securities CSV exceeds the size limit")
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
        entries: dict[str, list[tuple[str, str | None]]] = {}
        if not reader.fieldnames:
            raise ValueError("Empty securities CSV")
        columns = {
            name.strip().upper().replace(" ", "_"): name for name in reader.fieldnames
        }
        if not {"SYMBOL", "NAME_OF_COMPANY", "ISIN_NUMBER"}.issubset(columns):
            raise ValueError("NSE securities CSV columns changed")
        for row in reader:
            symbol = (row[columns["SYMBOL"]] or "").strip().upper()
            name = " ".join((row[columns["NAME_OF_COMPANY"]] or "").split())
            isin = (row[columns["ISIN_NUMBER"]] or "").strip().upper() or None
            if symbol and name:
                item = (name, isin)
                if item not in entries.setdefault(symbol, []):
                    entries[symbol].append(item)
        if not entries:
            raise ValueError("NSE securities CSV has no listings")
        return entries
    except (UnicodeError, csv.Error, ValueError, KeyError) as exc:
        raise SourceUnavailableError(
            "NSE securities CSV is unavailable or malformed"
        ) from exc


class NSESource(EventSource):
    name = "NSE"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        downloader: SafePdfDownloader | None = None,
    ):
        self.client = client
        self.received_count = 0
        self.unresolved_symbols: list[str] = []
        self.downloader = downloader or SafePdfDownloader(
            DOCUMENT_HOSTS, timeout_seconds=settings.live_source_timeout_seconds
        )

    async def fetch_events(
        self, companies: list[MonitoredCompany], since: datetime | None
    ) -> list[SourceEvent]:
        if not companies:
            return []
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=settings.live_source_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "almonds/0.35 (personal portfolio monitor)"},
        )
        try:
            unresolved = [
                company for company in companies if not company.source_company_id
            ]
            directory: dict[str, list[tuple[str, str | None]]] = {}
            if unresolved:
                for url in (EQUITY_DIRECTORY_URL, SME_DIRECTORY_URL):
                    data = await self._fetch_directory(client, url)
                    for symbol, listings in parse_nse_directory(data).items():
                        current = directory.setdefault(symbol, [])
                        for listing in listings:
                            if listing not in current:
                                current.append(listing)
            resolved: list[MonitoredCompany] = []
            self.unresolved_symbols = []
            for company in companies:
                if company.source_company_id:
                    resolved.append(company)
                    continue
                options = directory.get(company.symbol.upper(), [])
                if company.isin:
                    options = [
                        item for item in options if item[1] == company.isin.upper()
                    ]
                if len(options) != 1:
                    self.unresolved_symbols.append(company.symbol)
                    continue
                resolved.append(replace(company, source_company_id=options[0][0]))
            issuer_counts: dict[str, int] = {}
            for company in resolved:
                key = _issuer_key(company.source_company_id or "")
                issuer_counts[key] = issuer_counts.get(key, 0) + 1
            safe = []
            for company in resolved:
                if issuer_counts[_issuer_key(company.source_company_id or "")] > 1:
                    self.unresolved_symbols.append(company.symbol)
                else:
                    safe.append(company)
            if not safe:
                return []
            for attempt in range(3):
                try:
                    async with client.stream(
                        "GET", FEED_URL, follow_redirects=False
                    ) as response:
                        if response.status_code == 429:
                            retry_after = response.headers.get("retry-after", "")
                            if (
                                attempt == 0
                                and retry_after.isdigit()
                                and int(retry_after) <= 10
                            ):
                                await asyncio.sleep(int(retry_after))
                                continue
                            raise SourceUnavailableError(
                                "NSE rate limited the RSS request"
                            )
                        if response.status_code == 403:
                            raise SourceUnavailableError("NSE denied the RSS request")
                        if response.status_code >= 500:
                            raise httpx.HTTPStatusError(
                                "NSE server error",
                                request=response.request,
                                response=response,
                            )
                        if response.status_code != 200:
                            raise SourceUnavailableError(
                                f"NSE RSS returned HTTP {response.status_code}"
                            )
                        if response.headers.get("content-type", "").split(";", 1)[
                            0
                        ].lower() not in (
                            "application/xml",
                            "text/xml",
                            "application/rss+xml",
                        ):
                            raise SourceUnavailableError("NSE RSS content type changed")
                        payload = bytearray()
                        async for chunk in response.aiter_bytes():
                            payload.extend(chunk)
                            if len(payload) > MAX_FEED_BYTES:
                                raise SourceUnavailableError(
                                    "NSE RSS exceeds the size limit"
                                )
                        items = parse_nse_announcements(bytes(payload), safe)
                        self.received_count = len(
                            ElementTree.fromstring(bytes(payload)).findall(
                                "./channel/item"
                            )
                        )
                        return items
                except (
                    httpx.TimeoutException,
                    httpx.TransportError,
                    httpx.HTTPStatusError,
                ) as exc:
                    if attempt == 2:
                        raise SourceUnavailableError(
                            "NSE RSS unavailable after retries"
                        ) from exc
                    await asyncio.sleep(0.5 * (attempt + 1))
        finally:
            if own_client:
                await client.aclose()
        raise SourceUnavailableError("NSE RSS request failed")

    async def _fetch_directory(self, client: httpx.AsyncClient, url: str) -> bytes:
        for attempt in range(3):
            try:
                async with client.stream(
                    "GET", url, follow_redirects=False
                ) as response:
                    if response.status_code == 429:
                        raise SourceUnavailableError(
                            "NSE rate limited the securities CSV"
                        )
                    if response.status_code >= 500:
                        raise httpx.HTTPStatusError(
                            "NSE securities CSV server error",
                            request=response.request,
                            response=response,
                        )
                    if (
                        response.status_code != 200
                        or response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .lower()
                        != "text/csv"
                    ):
                        raise SourceUnavailableError(
                            "NSE securities CSV is unavailable"
                        )
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > MAX_DIRECTORY_BYTES:
                            raise SourceUnavailableError(
                                "NSE securities CSV exceeds the size limit"
                            )
                    return bytes(payload)
            except (
                httpx.TimeoutException,
                httpx.TransportError,
                httpx.HTTPStatusError,
            ) as exc:
                if attempt == 2:
                    raise SourceUnavailableError(
                        "NSE securities CSV unavailable after retries"
                    ) from exc
                await asyncio.sleep(0.5 * (attempt + 1))
        raise SourceUnavailableError("NSE securities CSV request failed")

    async def download_document(self, event: SourceEvent):
        if event.document_url is None:
            raise SourceUnavailableError("NSE item has no PDF attachment")
        return await self.downloader.download(event.document_url)
