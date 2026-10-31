"""Bounded, robots-aware public InvestorGain adapter. No invented fallback data."""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.ingestion.access_policy import SourceAccessPolicy
from app.ingestion.sources.base import SourceUnavailableError
from app.schemas.ipo import IPOData

IST = ZoneInfo("Asia/Kolkata")
ORIGIN = "https://www.investorgain.com"
REPORT = ORIGIN + "/report/ipo-gmp-live/331/"
DASHBOARD = ORIGIN + "/ipo-dashboard/mainline/"


def number(value):
    value = value.replace(",", "").replace("−", "-").strip()
    match = re.search(r"(?<!\w)-?\d+(?:\.\d+)?", value)
    try:
        return Decimal(match.group()) if match else None
    except InvalidOperation:
        return None


def text(node):
    return node.get_text(" ", strip=True) if node else ""


def short_date(value, today, *, timestamp=False):
    """Yearless dates are resolved around today, including Dec/Jan and leap days."""
    pattern = r"\b(\d{1,2}-[A-Za-z]{3})(?:\s+(\d{1,2}:\d{2}))?"
    match = re.search(pattern, value)
    if not match:
        return None
    candidates = []
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            parsed = datetime.strptime(f"{match[1]}-{year} {match[2] or '00:00'}", "%d-%b-%Y %H:%M").replace(tzinfo=IST)
            candidates.append(parsed)
        except ValueError:
            pass
    if not candidates:
        return None
    chosen = min(candidates, key=lambda v: abs((v.date() - today).days))
    return chosen if timestamp else chosen.date()


def identity(href):
    url = urlsplit(urljoin(ORIGIN, href))
    match = re.fullmatch(r"/(?:gmp|ipo)/([a-z0-9-]+)/(\d+)/", url.path)
    if url.scheme != "https" or url.netloc != "www.investorgain.com" or not match:
        return None
    return match[2], ORIGIN + f"/ipo/{match[1]}/{match[2]}/"


def parse_report(html, today):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#reportTable")
    headers = [text(h) for h in table.select("thead th")] if table else []
    if not table or not any("GMP" in h for h in headers):
        raise SourceUnavailableError("IPO_GMP_TABLE_SCHEMA_CHANGED")
    issues = {}
    for row in table.select("tbody tr"):
        cells = {c.get("data-label"): c for c in row.select("td[data-label]")}
        name = cells.get("Name")
        link = name.select_one("a[href]") if name else None
        key = identity(link["href"]) if link else None
        # Only the explicit IPO badge is mainboard; FPO/SME/unknown are excluded.
        badges = [text(b) for b in name.select(".badge")] if name else []
        if not key or "IPO" not in badges or any("SME" in b for b in badges):
            continue
        get = lambda label: text(cells.get(label))
        gmp_cell = cells.get("GMP")
        first_bold = gmp_cell.select_one("b") if gmp_cell else None
        quote = number(text(first_bold))  # '--' stays unknown, never zero.
        lot = number(get("Lot"))
        issues[key[0]] = IPOData(source_id=key[0], name=text(link), source_url=key[1],
            opens_on=short_date(get("Open"), today), closes_on=short_date(get("Close"), today),
            lists_on=short_date(get("Listing"), today), price_high=number(get("Price (₹)")),
            issue_cr=number(get("IPO Size")), lot_size=int(lot) if lot else None,
            gmp=quote, gmp_at=short_date(get("Updated-On"), today, timestamp=True),
            subscription=number(get("Sub")))
    return issues


def parse_dashboard(html):
    soup = BeautifulSoup(html, "html.parser")
    table = next((t for t in soup.select("table") if [text(h) for h in t.select("thead th")] == ["Company", "Open", "Close"]), None)
    if table is None:
        raise SourceUnavailableError("IPO_MAINBOARD_TABLE_SCHEMA_CHANGED")
    issues = {}
    for row in table.select("tbody tr"):
        cells = row.select("td")
        link = row.select_one("a[href]")
        key = identity(link["href"]) if link else None
        if not key or len(cells) != 3 or "SME" in link.get("title", "").upper() or "FPO" in link.get("title", "").upper():
            continue
        def full_date(value):
            try:
                return datetime.strptime(value, "%d-%m-%Y").date()
            except ValueError:
                return None
        issues[key[0]] = IPOData(source_id=key[0], name=text(link), source_url=key[1],
            opens_on=full_date(text(cells[1])), closes_on=full_date(text(cells[2])))
    return issues


def parse_company(html, issue):
    soup = BeautifulSoup(html, "html.parser")
    # Explicit detail classification prevents cross-segment matches.
    meta = soup.select_one(".ipo-hero-sub")
    if not meta or "IPO" not in text(meta) or "SME" in text(meta).upper() or "FPO" in text(meta).upper():
        raise SourceUnavailableError("IPO_DETAIL_SEGMENT_UNVERIFIED")
    values = {text(c[0]): text(c[1]) for row in soup.select('table[aria-label="IPO key information"] tr')
              if len(c := row.select("td")) == 2}
    if "Issue Size" not in values or "Price Band" not in values:
        raise SourceUnavailableError("IPO_DETAIL_SCHEMA_CHANGED")
    amounts = re.findall(r"\d[\d,]*(?:\.\d+)?", values["Price Band"])
    issue.price_low = number(amounts[0]) if amounts else issue.price_low
    issue.price_high = number(amounts[1] if len(amounts) > 1 else amounts[0]) if amounts else issue.price_high
    issue.issue_cr = number(values["Issue Size"])
    issue.fresh_cr = number(values.get("Fresh Issue", ""))
    issue.ofs_cr = number(values.get("Offer for Sale", values.get("OFS", "")))
    # Infer the other component only when the reported component equals the total.
    if issue.fresh_cr == issue.issue_cr and issue.fresh_cr is not None and issue.ofs_cr is None:
        issue.ofs_cr = Decimal(0)
    if issue.ofs_cr == issue.issue_cr and issue.ofs_cr is not None and issue.fresh_cr is None:
        issue.fresh_cr = Decimal(0)
    sector = soup.select_one(".ipo-acc-chip-val")
    issue.sector = text(sector) or None
    about = soup.select_one("#about-company") or soup.select_one('[itemprop="description"]')
    # Site section IDs vary; find the containing company card by its heading.
    heading = next((h for h in soup.select("h2") if text(h) == "About Company"), None)
    if heading:
        card = heading.find_parent("section") or heading.find_parent(class_="d-card")
        paragraphs = card.select("p") if card else []
        issue.business = " ".join(text(p) for p in paragraphs)[:1400] or None
    elif about:
        issue.business = text(about)[:1400]
    issue.objects = [text(c[1])[:300] for row in soup.select("#ObjectiveIssue tbody tr")
                     if len(c := row.select("td")) >= 2 and text(c[0]).isdigit()][:6]
    table = soup.select_one(".fin-table-new")
    if table and "Amount in ₹ Crore" in text(table):
        headers = [text(h) for h in table.select("thead th")][1:]
        metrics = {text(c[0]).lower(): [number(text(v)) for v in c[1:]]
                   for row in table.select("tbody tr") if len(c := row.select("td")) > 1}
        issue.financials = []
        for i, label in enumerate(headers):
            try:
                ended = datetime.strptime(label, "%d %b %Y").date()
            except ValueError:
                continue
            period = {"ended_on": ended.isoformat(), "annual": ended.month == 3 and ended.day == 31}
            for metric, aliases in {"income_cr": ["total income", "revenue"], "pat_cr": ["profit after tax"],
                    "debt_cr": ["total borrowing", "total borrowings"], "net_worth_cr": ["net worth"]}.items():
                array = next((metrics[a] for a in aliases if a in metrics), [])
                period[metric] = str(array[i]) if i < len(array) and array[i] is not None else None
            issue.financials.append(period)
    for table in soup.select(".kpi-table"):
        headers = [text(h) for h in table.select("thead th")]
        for row in table.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < 2:
                continue
            if text(cells[0]) == "EPS (₹)" and headers[1:2] == ["Pre IPO"]:
                issue.annual_eps = number(text(cells[1]))
            if text(cells[0]) == "ROE":
                for i, label in enumerate(headers[1:], 1):
                    if label.startswith("Mar 31,") and i < len(cells):
                        issue.annual_roe = number(text(cells[i]))
                        break
    basis = re.search(r"Pre IPO EPS.*?latest FY earnings as of\s+(\d{4}-\d{2}-\d{2})", text(soup.select_one(".kpi-disclaimer-content")))
    if basis:
        issue.annual_eps_period = date.fromisoformat(basis[1])
    rhp = next((a for a in soup.select('a[href]') if "RHP" in a.get("title", "") and "DRHP" not in a.get("title", "")), None)
    if rhp and urlsplit(rhp["href"]).scheme == "https":
        issue.rhp_url = rhp["href"]
    return IPOData.model_validate(issue.model_dump())


class InvestorGainProvider:
    def __init__(self, client=None):
        self.client = client

    async def fetch(self, current):
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=settings.live_source_timeout_seconds,
            follow_redirects=False, trust_env=False, headers={"User-Agent": "almonds/0.84 (personal research)"})
        policy = SourceAccessPolicy(client)
        async def get(url):
            if urlsplit(url).netloc != "www.investorgain.com":
                raise SourceUnavailableError("IPO_SOURCE_HOST_REJECTED")
            await policy.check(url)
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise SourceUnavailableError(f"IPO_SOURCE_HTTP_{response.status_code}")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 4_000_000:
                        raise SourceUnavailableError("IPO_SOURCE_TOO_LARGE")
                return data.decode("utf-8", errors="replace")
        try:
            today = current.astimezone(IST).date()
            issues = parse_dashboard(await get(DASHBOARD))
            issues.update(parse_report(await get(REPORT), today))
            active = [v for v in issues.values() if v.closes_on is None or v.closes_on >= today]
            if len(active) > 100:
                raise SourceUnavailableError("IPO_SOURCE_UNEXPECTED_ISSUE_COUNT")
            for issue in active:
                try:
                    parsed = parse_company(await get(issue.source_url), issue.model_copy(deep=True))
                    issues[issue.source_id] = parsed
                except (httpx.HTTPError, SourceUnavailableError, ValueError):
                    issue.detail_error = "Company detail fetch or validation failed; excluded from alerts"
            return [issues[i.source_id] for i in active]
        except httpx.HTTPError:
            raise SourceUnavailableError("IPO_SOURCE_CONNECTION_FAILED") from None
        finally:
            if own:
                await client.aclose()
