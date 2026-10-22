"""Bounded on-demand view of Almonds evidence; no new ingestion pipeline."""
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select

from app.ai.analyst.context_builder import build_context
from app.ai.events.context import build_event_context
from app.config import settings
from app.models.analysis import AIAnalysis
from app.models.commentary import ManagementStatement
from app.models.event import CompanyEvent, DataOrigin, Document, EventType
from app.models.financial import FinancialPeriod, FinancialScope, FinancialStatus, PeriodType
from app.models.research import ResearchJob
from app.models.telegram import TelegramQuery
from app.models.transaction import Transaction
from app.services.portfolio import get_positions
from app.services.quarterly_review import statement_evidence
from app.services.telegram_alerts import now, utc


def normalized(value):
    value = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    return " ".join(re.findall(r"[^\W_]+", value))


def name_key(value):
    words = normalized(value).split()
    while words and words[-1] in {"limited", "ltd", "inc", "corporation", "plc"}:
        words.pop()
    return " ".join(words)


def portfolio_companies(db):
    """The registered list is the uploaded portfolio, even before trade import.

    Once trade history exists, use the existing position service to exclude a
    fully sold security. Never expand the list through a market/security lookup.
    """
    traded_ids = set(db.scalars(select(Transaction.company_id).distinct()))
    return [position.company for position in get_positions(db)
            if position.quantity > 0 or position.company.id not in traded_ids]


def resolve_holding(db, query):
    """Exact normalized identifiers and explicit aliases within the uploaded portfolio."""
    key = normalized(query)
    if not key:
        return []
    matches = []
    for company in portfolio_companies(db):
        aliases = settings.telegram_company_aliases.get(company.symbol, [])
        identifiers = [company.symbol, company.isin or "", company.bse_code or "", *aliases]
        if key in {normalized(value) for value in identifiers if value} or name_key(query) == name_key(company.name):
            matches.append(company)
    return matches


@dataclass
class QuickContext:
    company_id: int
    company_name: str
    symbol: str
    registry: dict
    gaps: list
    latest_data_at: datetime | None
    confidence: str
    hash: str
    text: str


def _url_key(value):
    parsed = urlsplit(value or "")
    query = urlencode(sorted((key, value) for key, value in parse_qsl(parsed.query)
                             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}))
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, query, ""))


def queue_refresh(db, company):
    """Coalesce missing/stale inputs into existing cooldown/lease-controlled jobs."""
    from app.services.research_jobs import ensure_jobs
    ensure_jobs(db, company)
    queued = False
    for job in db.scalars(select(ResearchJob).where(ResearchJob.company_id == company.id)):
        if not (job.kind.startswith("DISCLOSURES:") or job.kind in {"MARKET", "COMMENTARY"}):
            continue
        if job.kind == "MARKET" and settings.market_data_provider != "nse_mcp":
            continue
        # RETRY keeps its provider backoff. Never reset a running lease.
        stale = not job.last_success_at or utc(job.last_success_at) < now()-timedelta(hours=24)
        if stale and job.status in {"SUCCEEDED", "PENDING"} and not job.lease_until:
            job.next_run_at = min(utc(job.next_run_at), now())
            job.status = "PENDING"
            queued = True
    return queued


def build_quick_context(db, company):
    registry, gaps, timestamps = {}, [], []
    confidence = "LOW"
    previous = db.scalar(select(TelegramQuery).where(TelegramQuery.company_id == company.id,
        TelegramQuery.structured_output.is_not(None), TelegramQuery.analyzed_at.is_not(None))
        .order_by(TelegramQuery.analyzed_at.desc()).limit(1))
    periods = list(db.scalars(select(FinancialPeriod).join(CompanyEvent, CompanyEvent.id == FinancialPeriod.source_event_id)
        .where(FinancialPeriod.company_id == company.id, FinancialPeriod.status == FinancialStatus.VALIDATED,
               CompanyEvent.data_origin != DataOrigin.FIXTURE, CompanyEvent.published_at <= now(),
               FinancialPeriod.period_end <= now().date())
        .order_by(FinancialPeriod.period_end.desc(), (FinancialPeriod.scope == FinancialScope.CONSOLIDATED).desc(),
                  (FinancialPeriod.period_type == PeriodType.QUARTERLY).desc()).limit(1)))
    report = {}
    if periods:
        period = periods[0]
        context = build_context(db, company.name, company.symbol, period, include_memory=False)
        report = context.investor_report
        registry.update(context.registry)
        confidence = report.get("evidence_confidence", {}).get("level", "LOW")
        event = db.get(CompanyEvent, period.source_event_id)
        timestamps.append(utc(event.published_at))
        if (now().date()-period.period_end).days > 150:
            gaps.append("Financial results are stale: latest stored reporting period ended " + period.period_end.isoformat() + ".")
        verification = report.get("source_verification", {}).get("status")
        if verification != "VERIFIED":
            gaps.append("Financial source verification: " + (verification or "UNAVAILABLE") + ".")
        if report.get("result_freshness", {}).get("status") == "POTENTIALLY_STALE":
            gaps.append("A newer detected result may supersede the stored financial data.")
    else:
        gaps.append("Validated financial results are unavailable.")

    # Put actual public source URLs on existing document locators, never model URLs.
    for item in registry.values():
        document_id = item.get("document_id") or item.get("source_document_id")
        if document_id:
            document = db.get(Document, document_id)
            if document:
                item["source_url"] = document.source_url
                event = db.get(CompanyEvent, document.event_id)
                if event:
                    item["published_at"] = event.published_at.isoformat()
                    item["origin"] = event.data_origin.value

    events = list(db.scalars(select(CompanyEvent).where(CompanyEvent.company_id == company.id,
        CompanyEvent.data_origin != DataOrigin.FIXTURE, CompanyEvent.published_at <= now())
        .order_by(CompanyEvent.published_at.desc(), CompanyEvent.id.desc()).limit(80)))
    material = {EventType.FINANCIAL_RESULTS, EventType.ORDER_WIN, EventType.ORDER_CANCELLATION,
                EventType.REGULATORY, EventType.AUDITOR_CHANGE, EventType.ACQUISITION,
                EventType.FUND_RAISE, EventType.CAPEX, EventType.CREDIT_RATING, EventType.LITIGATION}
    since = utc(previous.analyzed_at) if previous else None
    events.sort(key=lambda event: (not (since and utc(event.published_at) > since),
        event.event_type not in material, -utc(event.published_at).timestamp()))
    seen_titles, seen_urls, count = set(), set(), 0
    for event in events:
        # A recurring title on another date is a separate development. Query
        # parameters can identify different filings; only tracking fields disappear.
        title = (normalized(event.title + " " + (event.description or "")), utc(event.published_at).date())
        url = _url_key(event.document_url or event.source_url)
        if not event.document_url and not urlsplit(url).path.lower().endswith(".pdf"):
            url = (url, title)
        if title in seen_titles or (url and url in seen_urls):
            continue
        seen_titles.add(title)
        if url:
            seen_urls.add(url)
        evidence = build_event_context(db, event).registry
        for key, item in evidence.items():
            item.update(published_at=event.published_at.isoformat(), origin=event.data_origin.value,
                        event_type=event.event_type.value,
                        source_url=event.document_url or event.source_url,
                        since_previous=bool(since and utc(event.published_at) > since))
            if item["kind"] == "DOCUMENT_PAGE":
                item["excerpt"] = item.pop("text")
            registry.setdefault(key, item)
        timestamps.append(utc(event.published_at))
        count += 1
        if count >= 6:
            break
    statements = list(db.scalars(select(ManagementStatement).join(CompanyEvent, CompanyEvent.id == ManagementStatement.event_id)
        .where(ManagementStatement.company_id == company.id, CompanyEvent.data_origin != DataOrigin.FIXTURE,
               ManagementStatement.published_at <= now())
        .order_by(ManagementStatement.published_at.desc()).limit(8)))
    for statement in statements:
        item = statement_evidence(statement, "RECENT_COMMENTARY")
        event = db.get(CompanyEvent, statement.event_id)
        item.update(source_url=event.document_url or event.source_url, origin=event.data_origin.value)
        registry[statement.evidence_id] = item
        timestamps.append(utc(statement.published_at))

    # Financial metrics/valuations are already computed by deterministic services.
    # Keep a compact subset of that registry, rather than serializing the full report.
    kinds = {"FACT", "METRIC", "VALUATION", "SCENARIO", "RELATIONSHIP", "EVENT", "DOCUMENT_PAGE", "MANAGEMENT_STATEMENT"}
    registry = {key: item for key, item in registry.items() if item.get("kind") in kinds}
    for item in registry.values():
        if item.get("as_of_date"):
            timestamps.append(datetime.fromisoformat(item["as_of_date"]).replace(tzinfo=timezone.utc))
    jobs = list(db.scalars(select(ResearchJob).where(ResearchJob.company_id == company.id,
        ResearchJob.kind.like("DISCLOSURES:%"))))
    checked = [utc(job.last_success_at) for job in jobs if job.last_success_at]
    if not checked or max(checked) < now()-timedelta(hours=24):
        gaps.append("Disclosure coverage has not been successfully checked in the last 24 hours.")
    latest = max(timestamps) if timestamps else None
    if latest is None or latest < now()-timedelta(days=30):
        gaps.append("Recent source data is missing or stale.")
    if not any(item.get("kind") == "VALUATION" for item in registry.values()):
        gaps.append("Fresh calculated valuation metrics are unavailable.")
    gaps.append("Independent news and peer/consensus datasets are not configured; coverage is limited to stored issuer evidence.")
    if any(item.get("origin") == "MANUAL" for item in registry.values()):
        gaps.append("Manually uploaded source contents are not independently verified unless stated above.")
    if any("stale" in gap.lower() or "verification" in gap.lower() or "coverage" in gap.lower() for gap in gaps):
        confidence = "LOW"
    prior = db.scalar(select(AIAnalysis).where(AIAnalysis.company_id == company.id,
        AIAnalysis.status.in_(["SUCCESS", "VERIFIED_FALLBACK"]))
        .order_by(AIAnalysis.created_at.desc()).limit(1))
    payload = {"company": company.name, "symbol": company.symbol,
        "evidence": registry, "gaps": gaps, "confidence_ceiling": confidence,
        "previous_analysis_at": since.isoformat() if since else None,
        "prior_analysis": {"timestamp": prior.created_at.isoformat(), "summary": (prior.summary or "")[:700],
            "risks": (prior.structured_output or {}).get("concerns", [])[:2]} if prior else None,
        "previous_quick_analysis": previous.structured_output if previous else None,
        "history_instruction": "Prior AI interpretations are context only, never new factual evidence."}
    # Cap entire valid JSON by removing low-priority documentary context first.
    while len(json.dumps(payload, default=str)) > settings.ai_max_context_chars:
        removable = [key for key, item in registry.items() if item["kind"] in {"DOCUMENT_PAGE", "RELATIONSHIP", "MANAGEMENT_STATEMENT"}]
        if not removable:
            removable = list(registry)[-1:]
        if not removable:
            break
        registry.pop(removable[-1])
    # The previous request and analysis clock must not invalidate their own cache.
    fingerprint = {"version": "QUICK_V1", "evidence": registry, "gaps": gaps, "confidence": confidence}
    fingerprint["evidence"] = {key: {k: v for k, v in item.items() if k != "since_previous"}
                               for key, item in registry.items()}
    encoded = json.dumps(fingerprint, sort_keys=True, default=str)
    return QuickContext(company.id, company.name, company.symbol, registry, gaps, latest,
        confidence, hashlib.sha256(encoded.encode()).hexdigest(), json.dumps(payload, default=str))
