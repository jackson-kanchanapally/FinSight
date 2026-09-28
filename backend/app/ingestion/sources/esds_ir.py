"""Company-specific parser for ESDS Software Solution's public IR page."""

import asyncio
import logging
import re
from datetime import datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pymupdf
from bs4 import BeautifulSoup

from app.config import settings
from app.ingestion.downloader import SafePdfDownloader
from app.ingestion.sources.base import DownloadedDocument, EventSource, MonitoredCompany, SourceEvent, SourceUnavailableError
from app.models.event import PublicationPrecision, ReportingBasis

logger = logging.getLogger(__name__)
IR_URL = "https://www.esds.co.in/investor"
IR_COMPANY_ID = "544898"
ANNOUNCEMENT_PATH = "/assets/images/investor-relations/Announcements/"
MAX_PAGE_BYTES = 1_000_000
MAX_NEW_DOCUMENTS = 10
INDIA_TIME = ZoneInfo("Asia/Kolkata")


def parse_esds_announcement_rows(payload: bytes) -> list[tuple[str, str]]:
    if len(payload) > MAX_PAGE_BYTES:
        raise SourceUnavailableError("ESDS IR page exceeds the size limit")
    soup = BeautifulSoup(payload, "html.parser")
    if "ESDS Software Solution Limited" not in soup.get_text(" ", strip=True):
        raise SourceUnavailableError("ESDS IR issuer identity is missing")
    section = next((item for item in soup.select(".esds-vtab-A-v1__item")
                    if (heading := item.select_one(".esds-vtab-A-v1__trig-name"))
                    and heading.get_text(" ", strip=True) == "Announcements"), None)
    if section is None:
        raise SourceUnavailableError("ESDS IR announcements section is missing")
    rows = []
    for row in section.select("tr.esds-vtab-A-v1__doc-row"):
        title_node = row.select_one(".esds-vtab-A-v1__doc-name")
        link_node = row.select_one("a.esds-vtab-A-v1__btn-dl[href]")
        if title_node is None or link_node is None:
            raise SourceUnavailableError("ESDS IR announcement row changed")
        title = title_node.get_text(" ", strip=True)
        url = urljoin(IR_URL, link_node["href"])
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname != "www.esds.co.in" or
                parts.username or parts.password or parts.port not in (None, 443) or
                not parts.path.startswith(ANNOUNCEMENT_PATH) or not parts.path.lower().endswith(".pdf")):
            raise SourceUnavailableError("ESDS IR announcement has an unapproved document URL")
        rows.append((title, url))
    return rows


def _pdf_facts(document: DownloadedDocument, symbol: str, company_id: str, title: str) -> dict:
    try:
        with pymupdf.open(stream=document.content, filetype="pdf") as pdf:
            first_page = pdf[0].get_text() if len(pdf) else ""
    except Exception as exc:
        raise SourceUnavailableError("ESDS announcement PDF cannot be read") from exc
    if not re.search(rf"Scrip\s+Symbol\s*:\s*{re.escape(symbol)}\b", first_page, re.IGNORECASE):
        raise SourceUnavailableError("ESDS announcement PDF symbol does not match configuration")
    if not re.search(rf"Scrip\s+Code\s*:\s*{re.escape(company_id)}\b", first_page, re.IGNORECASE):
        raise SourceUnavailableError("ESDS announcement PDF scrip code does not match configuration")
    date_match = re.search(r"\bDate\s*:\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})", first_page)
    if not date_match:
        raise SourceUnavailableError("ESDS announcement PDF has no issuer date")
    try:
        document_date = datetime.strptime(date_match.group(1), "%B %d, %Y").date()
    except ValueError as exc:
        raise SourceUnavailableError("ESDS announcement PDF issuer date is malformed") from exc

    facts: dict = {"published_at": datetime.combine(document_date, datetime.min.time(), INDIA_TIME),
                   "publication_precision": PublicationPrecision.DATE,
                   "document_date": document_date.isoformat()}
    title_match = re.search(r"\bQ([1-4])\s*FY\s*(20\d{2}|\d{2})\b", title, re.IGNORECASE)
    period_match = re.search(r"quarter ended\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})", first_page, re.IGNORECASE)
    if title_match and period_match and "result" in title.casefold():
        try:
            facts["period_end_date"] = datetime.strptime(period_match.group(1), "%B %d, %Y").date()
        except ValueError:
            pass
        facts["quarter"] = int(title_match.group(1))
        year = title_match.group(2)
        facts["fiscal_year"] = f"FY{year if len(year) == 4 else '20' + year}"
        lower = first_page.casefold()
        if "standalone" in lower and "consolidated" in lower:
            facts["reporting_basis"] = ReportingBasis.BOTH
        elif "standalone" in lower:
            facts["reporting_basis"] = ReportingBasis.STANDALONE
        elif "consolidated" in lower:
            facts["reporting_basis"] = ReportingBasis.CONSOLIDATED
    return facts


class ESDSIRSource(EventSource):
    name = "ESDS_IR"

    def __init__(self, *, client: httpx.AsyncClient | None = None,
                 downloader: SafePdfDownloader | None = None,
                 bootstrap_since: datetime | None = None):
        self.client = client
        self.downloader = downloader or SafePdfDownloader({"www.esds.co.in"},
                            timeout_seconds=settings.live_source_timeout_seconds, client=client)
        self.bootstrap_since = bootstrap_since
        self.received_count = 0
        self._documents: dict[str, DownloadedDocument] = {}

    async def _fetch_page(self) -> bytes:
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=settings.live_source_timeout_seconds,
                     follow_redirects=False, trust_env=False,
                     headers={"User-Agent": "almonds/0.35 (personal portfolio monitor)"})
        try:
            for attempt in range(3):
                try:
                    async with client.stream("GET", IR_URL, follow_redirects=False) as response:
                        if response.status_code == 429:
                            retry_after = response.headers.get("retry-after", "")
                            if attempt == 0 and retry_after.isdigit() and int(retry_after) <= 10:
                                await asyncio.sleep(int(retry_after))
                                continue
                            raise SourceUnavailableError("ESDS IR rate limited the page request")
                        if response.status_code == 403:
                            raise SourceUnavailableError("ESDS IR denied the page request")
                        if response.status_code >= 500:
                            raise httpx.HTTPStatusError("ESDS IR server error", request=response.request, response=response)
                        if response.status_code != 200:
                            raise SourceUnavailableError(f"ESDS IR returned HTTP {response.status_code}")
                        if not response.headers.get("content-type", "").lower().startswith("text/html"):
                            raise SourceUnavailableError("ESDS IR content type changed")
                        payload = bytearray()
                        async for chunk in response.aiter_bytes():
                            payload.extend(chunk)
                            if len(payload) > MAX_PAGE_BYTES:
                                raise SourceUnavailableError("ESDS IR page exceeds the size limit")
                        return bytes(payload)
                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                    if attempt == 2:
                        raise SourceUnavailableError("ESDS IR page unavailable after retries") from exc
                    await asyncio.sleep(0.5 * (attempt + 1))
        finally:
            if own_client:
                await client.aclose()
        raise SourceUnavailableError("ESDS IR page request failed")

    async def fetch_events(self, companies: list[MonitoredCompany], since: datetime | None) -> list[SourceEvent]:
        company = next((company for company in companies if company.ir_url == IR_URL and
                        company.source_company_id == IR_COMPANY_ID), None)
        if company is None:
            return []
        rows = parse_esds_announcement_rows(await self._fetch_page())
        self.received_count = len(rows)
        self._documents.clear()
        events: list[SourceEvent] = []
        new_documents = 0
        for title, url in rows:
            published = company.known_documents.get(url)
            if published is not None and published.tzinfo is None:
                # SQLite drops timezone data in offline tests; IR dates are local dates.
                published = published.replace(tzinfo=INDIA_TIME)
            elif published is not None:
                published = published.astimezone(INDIA_TIME)
            facts = {"published_at": published, "publication_precision": PublicationPrecision.DATE} if published else None
            if facts is None:
                if new_documents >= MAX_NEW_DOCUMENTS:
                    logger.warning("esds_ir.new_document_limit_reached", extra={"limit": MAX_NEW_DOCUMENTS})
                    break
                document = await self.downloader.download(url)
                self._documents[url] = document
                facts = _pdf_facts(document, company.external_symbol, company.source_company_id, title)
                new_documents += 1
            if self.bootstrap_since and facts["published_at"] < self.bootstrap_since:
                continue
            events.append(SourceEvent(external_symbol=company.external_symbol, title=title[:500],
                                      description=title, published_at=facts["published_at"],
                                      publication_precision=PublicationPrecision.DATE,
                                      period_end_date=facts.get("period_end_date"),
                                      fiscal_year=facts.get("fiscal_year"), quarter=facts.get("quarter"),
                                      reporting_basis=facts.get("reporting_basis"),
                                      external_id=url, source_url=IR_URL, document_url=url,
                                      raw_payload={"ir_url": IR_URL, "document_title": title,
                                                   "document_url": url, "date_basis": "pdf_letter_date",
                                                   "document_date": facts["published_at"].date().isoformat()}))
        return events

    async def download_document(self, event: SourceEvent) -> DownloadedDocument:
        if event.document_url is None:
            raise SourceUnavailableError("ESDS IR event has no PDF attachment")
        return self._documents.pop(event.document_url, None) or await self.downloader.download(event.document_url)
