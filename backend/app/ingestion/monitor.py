import asyncio
import logging
import re
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.deduplicator import existing_reference
from app.ingestion.downloader import validate_pdf
from app.ingestion.normalizer import normalize_event
from app.ingestion.sources import (
    BSESource,
    ESDSIRSource,
    EventSource,
    FixtureSource,
    MonitoredCompany,
    NSESource,
    SourceEvent,
)
from app.ingestion.storage import DocumentStorage, LocalDocumentStorage
from app.models import Company
from app.models.event import (
    CompanyEvent,
    CompanySource,
    DataOrigin,
    Document,
    EventReference,
    EventType,
    MonitoringState,
    ProcessingStatus,
)
from app.services.events.event_service import save_event
from app.services.financials.service import process_financial_event
from app.services.commentary import is_commentary_candidate, refresh_commentary_for_company
from app.services.portfolio import get_positions

logger = logging.getLogger(__name__)
monitor_lock = asyncio.Lock()


class SourceRunSummary(BaseModel):
    status: str
    received: int = 0
    company_matches: int = 0
    portfolio_matches: int = 0
    new_events: int = 0
    duplicates: int = 0
    documents_downloaded: int = 0
    unresolved_symbols: list[str] = Field(default_factory=list)
    error: str | None = None


class CheckSummary(BaseModel):
    sources_checked: int = 0
    events_found: int = 0
    company_matches: int = 0
    # Kept for clients created before registered-company monitoring.
    portfolio_matches: int = 0
    new_events: int = 0
    documents_downloaded: int = 0
    duplicates: int = 0
    errors: list[str] = Field(default_factory=list)
    sources: dict[str, SourceRunSummary] = Field(default_factory=dict)


def configured_sources() -> list[EventSource]:
    if settings.event_source_mode == "fixture":
        return [FixtureSource()]
    if settings.event_source_mode == "live":
        sources: list[EventSource] = []
        if settings.nse_source_enabled:
            sources.append(NSESource())
        if settings.bse_source_enabled:
            sources.append(BSESource())
        if settings.company_ir_source_enabled:
            sources.append(ESDSIRSource())
        return sources
    return []


def active_companies(
    db: Session, source: EventSource, symbols: set[str] | None = None
) -> list[MonitoredCompany]:
    configs = {
        row.company_id: row
        for row in db.scalars(
            select(CompanySource).where(CompanySource.source == source.name)
        ).all()
    }
    known_documents: dict[int, dict[str, datetime]] = {}
    if source.name == "ESDS_IR":
        for company_id, url, published_at in db.execute(
            select(
                CompanyEvent.company_id,
                EventReference.document_url,
                CompanyEvent.published_at,
            )
            .join(EventReference, EventReference.event_id == CompanyEvent.id)
            .where(
                EventReference.source == source.name,
                EventReference.document_url.is_not(None),
            )
        ):
            known_documents.setdefault(company_id, {})[url] = published_at
    live_source = source.name in {"NSE", "BSE", "ESDS_IR", "COMPANY_IR"}
    if source.name == "NSE":
        # NSE's official securities list resolves symbols to exact issuer names
        # during the source check. An explicit mapping is only an override or opt-out.
        monitored = [
            company
            for company in db.scalars(select(Company).order_by(Company.id))
            if re.search(r"\bNSE\b", company.exchange.upper()) or company.id in configs
        ]
    elif live_source:
        monitored = [db.get(Company, company_id) for company_id in configs]
    else:
        monitored = [
            position.company for position in get_positions(db) if position.quantity > 0
        ]
    companies = []
    for company in monitored:
        if company is None:
            continue
        if symbols is not None and company.symbol not in symbols:
            continue
        config = configs.get(company.id)
        if config and not config.enabled:
            continue
        if live_source and source.name != "NSE" and config is None:
            continue
        companies.append(
            MonitoredCompany(
                company_id=company.id,
                symbol=company.symbol,
                name=company.name,
                external_symbol=(
                    config.external_symbol
                    if config and config.external_symbol
                    else company.symbol
                ),
                isin=company.isin,
                ir_url=config.ir_url if config else None,
                source_company_id=config.source_company_id if config else None,
                known_documents=known_documents.get(company.id, {}),
            )
        )
    return companies


def needs_document_download(db: Session, source: str, raw: SourceEvent) -> bool:
    if not raw.document_url:
        return False
    reference = existing_reference(db, source, raw.external_id)
    if reference is None:
        return True
    document = db.scalar(
        select(Document).where(
            Document.event_id == reference.event_id,
            Document.document_url == raw.document_url,
        )
    )
    if document is not None:
        return document.processing_status == ProcessingStatus.FAILED
    return (
        db.scalar(
            select(Document).where(
                Document.event_id == reference.event_id,
                Document.local_path.is_not(None),
            )
        )
        is None
    )


async def check_for_events(
    db: Session,
    *,
    sources: list[EventSource] | None = None,
    storage: DocumentStorage | None = None,
    symbols: set[str] | None = None,
    since_override: datetime | None = None,
) -> CheckSummary:
    summary = CheckSummary()
    sources = configured_sources() if sources is None else sources
    storage = storage or LocalDocumentStorage(settings.document_storage_path)
    async with monitor_lock:
        logger.info("event_monitor.started", extra={"source_count": len(sources)})
        for source in sources:
            companies = active_companies(db, source, symbols)
            if not companies:
                summary.sources[source.name] = SourceRunSummary(status="skipped")
                continue
            summary.sources_checked += 1
            state = db.scalar(
                select(MonitoringState).where(MonitoringState.source == source.name)
            )
            if state is None:
                state = MonitoringState(source=source.name, consecutive_failures=0)
                db.add(state)
            state.last_attempted_check = datetime.now(timezone.utc)
            db.commit()
            try:
                items = await source.fetch_events(
                    companies, since_override or state.last_successful_check
                )
                received = getattr(source, "received_count", len(items))
                summary.events_found += received
                source_matches = 0
                source_new = 0
                source_documents = 0
                for item in items:
                    raw = SourceEvent.model_validate(item)
                    normalized = normalize_event(
                        raw, source.name, companies, source.origin
                    )
                    if normalized is None:
                        continue
                    was_known = existing_reference(
                        db, normalized.source, normalized.external_id
                    ) is not None
                    summary.company_matches += 1
                    summary.portfolio_matches += 1
                    source_matches += 1
                    downloaded = None
                    document_error = None
                    if needs_document_download(db, source.name, raw):
                        try:
                            downloaded = await source.download_document(raw)
                            validate_pdf(downloaded)
                        except Exception as exc:
                            document_error = exc
                            logger.warning(
                                "event_monitor.document_failed",
                                extra={
                                    "source": source.name,
                                    "error_type": type(exc).__name__,
                                },
                            )
                    is_new, stored = save_event(
                        db, normalized, downloaded, document_error, storage
                    )
                    if (
                        normalized.event_type == EventType.FINANCIAL_RESULTS
                        and normalized.data_origin == DataOrigin.LIVE
                    ):
                        reference = existing_reference(
                            db, normalized.source, normalized.external_id
                        )
                        if reference is not None:
                            try:
                                from app.models.financial import FinancialProcessingRun

                                prior = db.scalar(
                                    select(FinancialProcessingRun).where(
                                        FinancialProcessingRun.source_event_id
                                        == reference.event_id
                                    )
                                )
                                if stored or prior is None:
                                    process_financial_event(
                                        db, reference.event_id, storage=storage
                                    )
                            except Exception:
                                logger.exception(
                                    "financials.auto_process_failed",
                                    extra={
                                        "event_id": reference.event_id,
                                        "source": source.name,
                                    },
                                )
                    if (stored or not was_known) and is_commentary_candidate(
                        normalized.event_type, normalized.title
                    ):
                        try:
                            await asyncio.to_thread(
                                refresh_commentary_for_company,
                                db.get_bind(), normalized.company_id,
                            )
                        except Exception:
                            logger.exception("commentary.auto_process_failed", extra={
                                "company_id": normalized.company_id,
                                "source": source.name,
                            })
                    summary.new_events += int(is_new)
                    source_new += int(is_new)
                    summary.documents_downloaded += int(stored)
                    source_documents += int(stored)
                state.last_successful_check = datetime.now(timezone.utc)
                state.last_error = None
                state.consecutive_failures = 0
                if items:
                    newest = max(
                        items,
                        key=lambda item: SourceEvent.model_validate(item).published_at,
                    )
                    state.last_external_id = SourceEvent.model_validate(
                        newest
                    ).external_id
                db.commit()
                duplicates = source_matches - source_new
                summary.duplicates += duplicates
                summary.sources[source.name] = SourceRunSummary(
                    status="success",
                    received=received,
                    company_matches=source_matches,
                    portfolio_matches=source_matches,
                    new_events=source_new,
                    duplicates=duplicates,
                    documents_downloaded=source_documents,
                    unresolved_symbols=getattr(source, "unresolved_symbols", []),
                )
                logger.info(
                    "event_monitor.source_complete",
                    extra={
                        "source": source.name,
                        "events_received": received,
                        "portfolio_matches": source_matches,
                        "new_events": source_new,
                        "duplicates": duplicates,
                    },
                )
            except Exception as exc:
                db.rollback()
                state = db.scalar(
                    select(MonitoringState).where(MonitoringState.source == source.name)
                )
                if state is None:
                    state = MonitoringState(source=source.name, consecutive_failures=0)
                    db.add(state)
                state.consecutive_failures += 1
                state.last_error = type(exc).__name__
                db.commit()
                summary.errors.append(f"{source.name}: {type(exc).__name__}")
                summary.sources[source.name] = SourceRunSummary(
                    status="error", error=type(exc).__name__
                )
                logger.exception(
                    "event_monitor.source_failed", extra={"source": source.name}
                )
        logger.info(
            "event_monitor.finished",
            extra={
                "new_events": summary.new_events,
                "documents_downloaded": summary.documents_downloaded,
                "duplicates": summary.duplicates,
                "failures": len(summary.errors),
            },
        )
    return summary
