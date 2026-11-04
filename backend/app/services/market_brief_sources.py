"""Dated public index observations, exchange holidays and bounded news feeds."""
import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.ingestion.access_policy import SourceAccessPolicy
from app.ingestion.sources.base import SourceUnavailableError
from app.services.gold_sources import IST, utc

NSE_URL = "https://www.nseindia.com/api/allIndices"
CALENDAR_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
SENSEX_URL = "https://www.moneycontrol.com/indian-indices/sensex-4.html"
NSE_PAGE = "https://www.nseindia.com/market-data/live-market-indices"
FEEDS = (
    ("ET Markets", "INDIA", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", "economictimes.indiatimes.com"),
    ("ET Economy", "INDIA", "https://economictimes.indiatimes.com/news/economy/rssfeeds/1373380680.cms", "economictimes.indiatimes.com"),
    ("ET Global", "GLOBAL", "https://economictimes.indiatimes.com/markets/global-markets/rssfeeds/1804991.cms", "economictimes.indiatimes.com"),
    ("Moneycontrol Markets", "INDIA", "https://www.moneycontrol.com/rss/marketreports.xml", "www.moneycontrol.com"),
    ("BBC Business", "GLOBAL", "https://feeds.bbci.co.uk/news/business/rss.xml", "www.bbc.com"),
    ("BBC World", "GLOBAL", "https://feeds.bbci.co.uk/news/world/rss.xml", "www.bbc.com"),
    ("Federal Reserve", "GLOBAL", "https://www.federalreserve.gov/feeds/press_all.xml", "www.federalreserve.gov"),
    ("RBI", "INDIA", "https://www.rbi.org.in/pressreleases_rss.xml", "www.rbi.org.in"),
)
INDEX_NAMES = {"NIFTY 50", "NIFTY BANK", "INDIA VIX", "NIFTY 500", "NIFTY IT", "NIFTY AUTO",
               "NIFTY FMCG", "NIFTY PHARMA", "NIFTY METAL", "NIFTY REALTY", "NIFTY ENERGY"}
MATERIAL = re.compile(r"nifty|sensex|stock|equit|market|econom|inflation|\bgdp\b|\brbi\b|\bfed\b|"
    r"interest.rate|tariff|trade.deal|crude|oil.price|dollar|rupee|bond|yield|war\b|conflict|"
    r"ceasefire|sanction|jobs|payroll|central.bank|earnings|fiscal|budget|china|geopolit", re.I)


def number(value, *, positive=True):
    try:
        result = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError, TypeError):
        raise SourceUnavailableError("INVALID_MARKET_NUMBER") from None
    if not result.is_finite() or abs(result) > 100_000_000 or (positive and result <= 0):
        raise SourceUnavailableError("INVALID_MARKET_NUMBER")
    return result


def observation(name, last, previous, opened, high, low, as_of, current, source, url):
    values = [number(v) for v in (last, previous, opened, high, low)]
    last, previous, opened, high, low = values
    if utc(as_of) > utc(current) + timedelta(minutes=2) or high < low or not low <= last <= high:
        raise SourceUnavailableError("INVALID_INDEX_TIMESTAMP_OR_RANGE")
    return {"id": "Q_" + re.sub(r"\W+", "_", name), "name": name,
            "last": str(last), "previous_close": str(previous), "open": str(opened),
            "high": str(high), "low": str(low),
            "change_pct": str(((last / previous - 1) * 100).quantize(Decimal("0.01"))),
            "as_of": as_of.isoformat(), "source": source, "source_url": url}


def parse_nse(body, current):
    stamp = datetime.strptime(body["timestamp"], "%d-%b-%Y %H:%M").replace(tzinfo=IST)
    quotes, breadth = {}, None
    for row in body["data"]:
        name = row.get("index")
        if name not in INDEX_NAMES:
            continue
        try:
            quote = observation(name, row["last"], row["previousClose"], row["open"], row["high"], row["low"],
                                stamp, current, "NSE public index feed", NSE_PAGE)
            supplied = number(row["percentChange"], positive=False)
            if abs(supplied - Decimal(quote["change_pct"])) > Decimal("0.03"):
                continue
        except (SourceUnavailableError, KeyError, TypeError):
            continue
        quotes[name] = quote
        if name == "NIFTY 500":
            try:
                advances, declines, unchanged = (int(row[key]) for key in ("advances", "declines", "unchanged"))
                if min(advances, declines, unchanged) >= 0 and 450 <= advances + declines + unchanged <= 550:
                    breadth = {"id": "B_NIFTY500", "scope": "Nifty 500 constituents",
                               "advances": advances, "declines": declines, "unchanged": unchanged,
                               "as_of": stamp.isoformat(), "source_url": NSE_PAGE}
            except (ValueError, KeyError, TypeError):
                pass
    if "NIFTY 50" not in quotes:
        raise SourceUnavailableError("NIFTY_BENCHMARK_UNAVAILABLE")
    return quotes, breadth


def parse_sensex(html, current):
    soup = BeautifulSoup(html, "html.parser")
    if not soup.title or "SENSEX" not in soup.title.get_text().upper():
        raise SourceUnavailableError("SENSEX_PAGE_IDENTITY_CHANGED")
    def value(selector):
        tag = soup.select_one(selector)
        if tag is None:
            raise SourceUnavailableError("SENSEX_PAGE_SCHEMA_CHANGED")
        return tag.get_text(" ", strip=True)
    text = value("#sp_asOnDate")
    match = re.fullmatch(r"As on\s+(\d{2} \w{3}, \d{4})\s*\|\s*(\d{2}:\d{2}) IST", text)
    if not match:
        raise SourceUnavailableError("SENSEX_TIMESTAMP_UNVERIFIED")
    stamp = datetime.strptime(" ".join(match.groups()), "%d %b, %Y %H:%M").replace(tzinfo=IST)
    return observation("SENSEX", value("#sp_val"), value("#sp_previousclose"), value("#sp_open"),
                       value("#sp_high"), value("#sp_low"), stamp, current, "Moneycontrol indicative quote", SENSEX_URL)


def parse_calendar(body, current):
    rows = body.get("CM")
    if not isinstance(rows, list) or len(rows) < 8:
        raise SourceUnavailableError("NSE_TRADING_CALENDAR_UNAVAILABLE")
    dates = sorted({datetime.strptime(row["tradingDate"], "%d-%b-%Y").date().isoformat() for row in rows})
    year = current.astimezone(IST).year
    if sum(day.startswith(str(year)) for day in dates) < 8:
        raise SourceUnavailableError("NSE_CALENDAR_YEAR_MISMATCH")
    return {"year": year, "holidays": dates, "source_url": CALENDAR_URL,
            "note": "Regular NSE equity sessions only; special weekend/evening sessions are excluded."}


def parse_feed(xml, source, region, hostname, current, since):
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", xml, re.I):
        raise SourceUnavailableError("NEWS_XML_DECLARATION_REJECTED")
    root = ET.fromstring(xml.lstrip("\ufeff \n\r\t"))
    if root.tag != "rss" or root.find("channel") is None:
        raise SourceUnavailableError("NEWS_FEED_SCHEMA_CHANGED")
    result = []
    for item in root.findall("./channel/item")[:80]:
        title = BeautifulSoup(item.findtext("title") or "", "html.parser").get_text(" ", strip=True)[:350]
        if not title or not MATERIAL.search(title):
            continue
        try:
            stamp = parsedate_to_datetime(item.findtext("pubDate") or "")
            url = urlsplit((item.findtext("link") or "").strip())
            hosts = {hostname, hostname.removeprefix("www.")}
            if source.startswith("BBC"):
                hosts |= {"www.bbc.co.uk", "bbc.co.uk", "bbc.com"}
            if (stamp.tzinfo is None or not utc(since) <= utc(stamp) <= utc(current) or
                    url.scheme != "https" or url.hostname not in hosts or url.username or url.password or url.port not in (None, 443)):
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        clean_url = urlunsplit(("https", url.netloc, url.path, "", ""))
        summary = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True)[:900]
        identity = sha256(clean_url.encode()).hexdigest()[:20]
        result.append({"id": "N_" + identity, "title": title, "summary": summary,
                       "published_at": utc(stamp).isoformat(), "url": clean_url,
                       "source": source, "region": region})
    return result


class MarketBriefProvider:
    def __init__(self, client=None):
        self.client = client

    async def _run(self, action):
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=settings.market_brief_source_timeout_seconds,
            follow_redirects=False, trust_env=False, headers={"User-Agent": "almonds/0.9 (personal market research)"})
        policy = SourceAccessPolicy(client)
        locks = {}
        async def get(url):
            origin = urlsplit(url).netloc
            async with locks.setdefault(origin, asyncio.Lock()):
                await policy.check(url)
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise SourceUnavailableError(f"HTTP_{response.status_code}")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 3_000_000:
                            raise SourceUnavailableError("RESPONSE_TOO_LARGE")
                    return data.decode("utf-8", errors="replace")
        try:
            return await action(get)
        finally:
            if own:
                await client.aclose()

    async def calendar(self, current):
        async def action(get):
            return parse_calendar(json.loads(await get(CALENDAR_URL)), current)
        return await self._run(action)

    async def fetch(self, current, since):
        async def action(get):
            result = {"collected_at": utc(current).isoformat(), "indices": {}, "breadth": None,
                      "news": [], "news_sources_ok": [], "errors": []}
            async def collect(name, fn):
                try:
                    return await fn()
                except SourceUnavailableError as exc:
                    reason = str(exc)[:90]
                except (httpx.HTTPError, ValueError, KeyError, TypeError, ET.ParseError):
                    reason = "CONNECTION_OR_SCHEMA_ERROR"
                result["errors"].append({"source": name, "reason": reason})
                return None
            async def nse():
                return parse_nse(json.loads(await get(NSE_URL)), current)
            async def sensex():
                return parse_sensex(await get(SENSEX_URL), current)
            async def news(feed):
                source, region, url, host = feed
                rows = parse_feed(await get(url), source, region, host, current, since)
                result["news_sources_ok"].append(source)
                return rows
            values = await asyncio.gather(collect("NSE indices", nse), collect("SENSEX", sensex),
                *(collect(feed[0], lambda f=feed: news(f)) for feed in FEEDS))
            if values[0]:
                result["indices"], result["breadth"] = values[0]
                for name in ("NIFTY BANK", "INDIA VIX"):
                    if name not in result["indices"]:
                        result["errors"].append({"source": name, "reason": "INDEX_OBSERVATION_UNVERIFIED"})
            if values[1]:
                result["indices"]["SENSEX"] = values[1]
            seen, per_source = set(), {}
            all_news = sorted((row for rows in values[2:] for row in (rows or [])), key=lambda row: row["published_at"], reverse=True)
            for row in all_news:
                identity = re.sub(r"\W+", " ", row["title"].casefold()).strip()
                if identity in seen or per_source.get(row["source"], 0) >= 6:
                    continue
                seen.add(identity)
                per_source[row["source"]] = per_source.get(row["source"], 0) + 1
                result["news"].append(row)
                if len(result["news"]) == 36:
                    break
            return result
        return await self._run(action)
