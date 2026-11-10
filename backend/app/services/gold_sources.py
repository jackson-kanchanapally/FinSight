"""Bounded public gold sources with explicit units and source timestamps."""
import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from hashlib import sha256
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.ingestion.access_policy import SourceAccessPolicy
from app.ingestion.sources.base import SourceUnavailableError
from app.schemas.gold import DollarRupeeQuote, GlobalGoldQuote, GoldNewsData, IndiaGoldQuote

IST = ZoneInfo("Asia/Kolkata")
GLOBAL_URL = "https://api.gold-api.com/price/XAU"
GLOBAL_INR_URL = "https://api.gold-api.com/price/XAU/INR"
INDIA_URL = "https://groww.in/gold-rates"
HYDERABAD_URL = "https://groww.in/gold-rates/gold-rate-today-in-hyderabad"
NEWS_FEEDS = {
    "FXStreet": "https://www.fxstreet.com/rss/news",
    "Federal Reserve": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",
}
NEWS_HOSTS = {"FXStreet": {"www.fxstreet.com", "fxstreet.com"},
              "Federal Reserve": {"www.federalreserve.gov", "federalreserve.gov"},
              "BBC World": {"www.bbc.com", "bbc.com", "www.bbc.co.uk", "bbc.co.uk"}}


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_global(body, current, currency="USD"):
    if body.get("symbol") != "XAU" or body.get("currency") != currency:
        raise SourceUnavailableError("GLOBAL_GOLD_SYMBOL_OR_CURRENCY_CHANGED")
    as_of = datetime.fromisoformat(body["updatedAt"].replace("Z", "+00:00"))
    if as_of.tzinfo is None or utc(as_of) > utc(current) + timedelta(minutes=5):
        raise SourceUnavailableError("GLOBAL_GOLD_INVALID_TIMESTAMP")
    quote = (GlobalGoldQuote(usd_per_oz=body["price"], as_of=as_of, fetched_at=current)
             if currency == "USD" else DollarRupeeQuote(usd_inr_rate=body["exchangeRate"],
                 quoted_gold_inr_per_oz=body["price"], as_of=as_of, fetched_at=current))
    price = quote.usd_per_oz if currency == "USD" else quote.quoted_gold_inr_per_oz
    bounds = (Decimal("100"), Decimal("100000")) if currency == "USD" else (Decimal("10000"), Decimal("10000000"))
    if not bounds[0] <= price <= bounds[1]:
        raise SourceUnavailableError("GLOBAL_GOLD_PRICE_OUT_OF_RANGE")
    if currency == "INR" and not Decimal("30") <= quote.usd_inr_rate <= Decimal("200"):
        raise SourceUnavailableError("USD_INR_RATE_OUT_OF_RANGE")
    return quote


def parse_india(html, current, region="hyderabad"):
    if region not in {"india", "hyderabad"}:
        raise ValueError("Unsupported gold rate region")
    label = "India" if region == "india" else "Hyderabad"
    soup = BeautifulSoup(html, "html.parser")
    script = soup.select_one("script#__NEXT_DATA__")
    if script is None:
        raise SourceUnavailableError(f"{region.upper()}_GOLD_PAGE_SCHEMA_CHANGED")
    body = json.loads(script.get_text())
    item = body["props"]["pageProps"]["goldRateData"]["physicalGoldRate"][region]
    if item.get("priceLocation", "").casefold() != region:
        raise SourceUnavailableError(f"{region.upper()}_GOLD_LOCATION_UNVERIFIED")
    rate_date = date.fromisoformat(item["date"])
    if rate_date > current.astimezone(IST).date():
        raise SourceUnavailableError(f"{region.upper()}_GOLD_FUTURE_DATE")
    quote = IndiaGoldQuote(city=label, source_url=INDIA_URL if region == "india" else HYDERABAD_URL,
        inr_22k_per_g=item["price"]["TWENTY_TWO"],
        inr_24k_per_g=item["price"]["TWENTY_FOUR"],
        change_22k_pct=item.get("percentageChange", {}).get("TWENTY_TWO"),
        change_24k_pct=item.get("percentageChange", {}).get("TWENTY_FOUR"),
        rate_date=rate_date, fetched_at=current)
    ratio = quote.inr_22k_per_g / quote.inr_24k_per_g
    if not Decimal("0.85") <= ratio <= Decimal("0.98") or not Decimal("100") <= quote.inr_24k_per_g <= Decimal("1000000"):
        raise SourceUnavailableError(f"{region.upper()}_GOLD_PURITY_OR_UNIT_CHANGED")
    return quote


def headline_impact(title, source):
    """Conservative headline reading; never present it as a causal price model."""
    lower = title.casefold()
    if source == "Federal Reserve":
        return "UNCLEAR", "Official Fed policy release; the gold-price effect is not established by its headline."
    clause = re.split(r"\b(?:as|amid|after|despite|while|ahead of|on)\b", lower, maxsplit=1)[0]
    up = bool(re.search(r"(?:gold|xau[/ -]?usd|bullion).{0,65}\b(?:rises?|rall(?:y|ies)|climbs?|gains?|surges?|jumps?|advances?|rebounds?|soars?|higher|breaks.{0,25}above)\b", clause))
    down = bool(re.search(r"(?:gold|xau[/ -]?usd|bullion).{0,65}\b(?:falls?|drops?|slides?|retreats?|slumps?|plunges?|declines?|sinks?|loses|lower|tumbles?|crashes?|breaks.{0,25}(?:floor|below))\b", clause))
    # Conditional forecasts and negated moves do not establish an observed direction.
    if re.search(r"\b(could|may|might|forecast|prediction|expected|fails? to|not|unlikely)\b", clause):
        up = down = False
    if up and down:
        return "MIXED", "The headline describes moves in both directions."
    if up:
        return "POSITIVE", "The headline reports upward gold-price momentum."
    if down:
        return "NEGATIVE", "The headline reports downward gold-price pressure."
    return "UNCLEAR", "The headline does not establish a clear gold-price direction."


def important_headline(title, source):
    lower = title.casefold()
    if source == "Federal Reserve":
        return bool(re.search(r"fomc statement|interest rate|implementation note|monetary policy", lower))
    if re.search(r"forecast|prediction|price target|technical analysis|weekly outlook", lower):
        return False
    return bool(re.search(
        r"\bfed\b|fomc|\bcpi\b|inflation|payroll|jobs report|treasury|yields?|dollar|"
        r"central.bank|war\b|ceasefire|tariff|import duty|record high|all.time high|plung|surges?|slumps?|tumbles?|crashes?|year.{0,8}low|ytd low",
        lower))


def major_event_key(title, source):
    """Conservative reported-event filter; routine price commentary stays out."""
    text = title.casefold()
    if re.search(r"(?:gold|bullion).{0,14}(?:forecast|prediction)|price forecast|price target|technical analysis|weekly outlook|preview|ahead of|"
                 r"\bif\b|\bunless\b|\bcould\b|\bmight\b|\bexpected to\b|\bmay (?:cut|hike|raise|rise|fall|hold|resume)\b", text):
        return None
    if re.search(r"\b(?:will|to) (?:cut|hike|raise|hold|keep|resume|launch)\b|rumou?r|unconfirmed|rate.cut bets|fed.{0,20}bets", text):
        return None
    if re.search(r"\b(?:no|not|never)\b.{0,20}(?:ceasefire|rate cut|rate hike|war|invasion)", text):
        return None
    if re.search(r"\b(?:not|never|denies?|fails? to)\b.{0,18}\b(?:cut|hike|raise|hold|resume|launch|agree)\b", text):
        return None
    family = None
    if source == "Federal Reserve":
        # The implementation note accompanies the statement; do not alert twice.
        if "implementation note" in text:
            return None
        if re.search(r"\bfomc statement\b|(?:federal reserve|fomc).{0,35}(?:cuts?|raises?|holds?|keeps?).{0,25}(?:rates?|target range)", text):
            family = "FED_POLICY_DECISION"
    elif re.search(r"\b(?:fed|fomc|federal reserve)\b.{0,25}\b(?:cuts?|raises?|hikes?|holds?|keeps?)\b.{0,25}(?:rates?|target range)", text):
        family = "FED_POLICY_DECISION"
    surprise = re.search(r"unexpected|surpris|hotter.than|cooler.than|above.{0,12}(?:forecast|expect)|below.{0,12}(?:forecast|expect)|"
                         r"beats?.{0,12}(?:forecast|expect)|miss(?:es)?.{0,12}(?:forecast|expect)", text)
    reported = re.search(r"(?:cpi|pce|us inflation|non[- ]?farm payrolls?|nfp|us (?:jobs|employment)).{0,45}"
                         r"(?:\b(?:rises?|falls?|rose|fell|cools?|accelerates?|jumps?|slows?|prints?|surges?|plunges?|beats?|miss(?:es)?|surprises?|comes?|came)\b|\bat\s+\d)", text)
    if family is None and surprise and reported:
        if re.search(r"\b(?:us|united states)\b.{0,25}(?:cpi|pce|inflation)", text):
            family = "US_INFLATION_SURPRISE"
        elif re.search(r"nonfarm payroll|non-farm payroll|\bnfp\b|\bus\b.{0,15}(?:jobs|employment)", text):
            family = "US_JOBS_SURPRISE"
    if family is None:
        if re.search(r"ceasefire.{0,12}(?:collaps(?:es|ed)|breaks? down|broke down|ends|ended|violated)", text):
            family = "CEASEFIRE_BREAKDOWN"
        elif re.search(r"ceasefire.{0,12}(?:agreed|signed|announced|takes effect|took effect)|(?:agree|announc|sign).{0,15}(?:to |a )?ceasefire", text):
            family = "CEASEFIRE_AGREEMENT"
        elif re.search(r"war (?:breaks out|declared)|declares? war|launch(?:es|ed).{0,25}(?:airstrikes?|missile strikes?|military strikes?|invasion)|major (?:military |war )?escalation", text):
            family = "CONFLICT_ESCALATION"
        elif re.search(r"(?:india|government).{0,35}(?:cuts?|raises?|hikes?|changes?|abolishes?).{0,30}(?:gold.{0,12}(?:duty|tariff)|import duty)|"
                       r"gold import duty.{0,20}(?:cut|raised|hiked|changed|abolished)", text):
            family = "INDIA_GOLD_POLICY"
        elif re.search(r"(?:central.bank|pboc|people.s bank).{0,35}(?:resumes?|halts?|suspends?|stops?).{0,20}(?:gold buying|gold purchases)|"
                       r"central.bank gold (?:buying|purchases).{0,20}(?:record|halt|suspend)", text):
            family = "CENTRAL_BANK_GOLD_POLICY"
    if family is None:
        return None
    entities = sorted({name for name in ("iran", "israel", "ukraine", "russia", "china", "india")
                       if re.search(r"\b" + name + r"\b", text)})
    if family == "FED_POLICY_DECISION" or family.startswith("US_"):
        entities = ["us"]
    return family + ":" + ",".join(entities)


def parse_news(xml, source, current):
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", xml, re.IGNORECASE):
        raise SourceUnavailableError("GOLD_NEWS_XML_DECLARATION_REJECTED")
    root = ET.fromstring(xml.lstrip("\ufeff"))
    if root.tag != "rss" or root.find("channel") is None:
        raise SourceUnavailableError("GOLD_NEWS_FEED_SCHEMA_CHANGED")
    result = []
    for item in root.findall("./channel/item")[:100]:
        title = BeautifulSoup(item.findtext("title") or "", "html.parser").get_text(" ", strip=True)[:500]
        if not title:
            continue
        if source == "FXStreet" and not re.search(r"\bgold\b|xau[/ -]?usd|bullion", title, re.IGNORECASE):
            continue
        if source == "BBC World" and not major_event_key(title, source):
            continue
        try:
            url = urlsplit((item.findtext("link") or "").strip())
            safe_url = url.scheme == "https" and url.hostname in NEWS_HOSTS[source] and not url.username and not url.password and url.port in (None, 443)
        except ValueError:
            safe_url = False
        if not safe_url:
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            if published.tzinfo is None:
                continue
            published = utc(published)
        except (ValueError, TypeError, OverflowError):
            continue
        if not utc(current) - timedelta(hours=48) <= published <= utc(current) + timedelta(minutes=5):
            continue
        summary = BeautifulSoup(item.findtext("description") or "", "html.parser").get_text(" ", strip=True)[:1200]
        impact, reason = headline_impact(title, source)
        # Price-only edits and syndicated repetitions in one day share an identity.
        normalized = re.sub(r"\s+", " ", re.sub(r"[\d,.]+", "#", title.casefold())).strip()
        identity = sha256(f"{source}|{published.date()}|{normalized}".encode()).hexdigest()
        result.append(GoldNewsData(identity=identity, title=title, summary=summary, source=source,
            url=urlunsplit(("https", url.netloc, url.path, "", "")), published_at=published,
            impact=impact, impact_reason=reason, important=important_headline(title, source) or bool(major_event_key(title, source))))
    return result


@dataclass
class GoldFetch:
    global_quote: GlobalGoldQuote | None = None
    usd_inr_quote: DollarRupeeQuote | None = None
    india_quote: IndiaGoldQuote | None = None
    hyderabad_quote: IndiaGoldQuote | None = None
    headlines: list[GoldNewsData] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    news_sources_ok: list[str] = field(default_factory=list)


class GoldProvider:
    def __init__(self, client=None):
        self.client = client

    async def fetch(self, current, cached_india=None, cached_hyderabad=None):
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=settings.gold_source_timeout_seconds,
            follow_redirects=False, trust_env=False,
            headers={"User-Agent": "almonds/0.85 (personal gold research)"})
        policy = SourceAccessPolicy(client)
        result = GoldFetch()

        async def get(url, *, api=False):
            # Gold API explicitly permits its documented public endpoint. HTML and
            # RSS collection also respects each publisher's robots policy.
            if not api:
                await policy.check(url)
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise SourceUnavailableError(f"HTTP_{response.status_code}")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 1_500_000:
                        raise SourceUnavailableError("RESPONSE_TOO_LARGE")
                return data.decode("utf-8", errors="replace")

        async def collect(source, fetcher):
            try:
                return await fetcher()
            except SourceUnavailableError as exc:
                result.errors.append({"source": source, "reason": str(exc)[:120]})
            except (httpx.HTTPError, ValueError, KeyError, TypeError, ET.ParseError):
                result.errors.append({"source": source, "reason": "CONNECTION_OR_SCHEMA_ERROR"})
            return None

        async def global_quote():
            return parse_global(json.loads(await get(GLOBAL_URL, api=True)), current)

        async def usd_inr_quote():
            return parse_global(json.loads(await get(GLOBAL_INR_URL, api=True)), current, "INR")

        async def retail_quotes():
            def reusable(quote):
                return quote and quote.rate_date == current.astimezone(IST).date() and utc(current) - utc(quote.fetched_at) < timedelta(hours=1)
            if reusable(cached_india) and reusable(cached_hyderabad):
                return cached_india, cached_hyderabad
            html = await get(INDIA_URL)
            quotes = []
            for region, label in (("india", "India gold"), ("hyderabad", "Hyderabad gold")):
                async def parse(r=region):
                    return parse_india(html, current, r)
                quotes.append(await collect(label, parse))
            return tuple(quotes)

        async def headlines(source, url):
            items = parse_news(await get(url), source, current)
            result.news_sources_ok.append(source)
            return items

        try:
            values = await asyncio.gather(collect("Global gold", global_quote), collect("USD/INR", usd_inr_quote),
                collect("Indian gold rates", retail_quotes),
                *(collect(source, lambda s=source, u=url: headlines(s, u)) for source, url in NEWS_FEEDS.items()))
            result.global_quote = values[0]
            result.usd_inr_quote = values[1]
            result.india_quote, result.hyderabad_quote = values[2] or (None, None)
            for items in values[3:]:
                result.headlines.extend(items or [])
            return result
        finally:
            if own:
                await client.aclose()
