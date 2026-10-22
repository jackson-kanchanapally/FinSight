import asyncio
import logging
import re
import time as time_module
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.database import get_db
from app.ingestion.monitor import CheckSummary, active_companies, check_for_events
from app.ingestion.sources.esds_ir import ESDSIRSource
from app.ingestion.storage import LocalDocumentStorage
from app.ingestion.downloader import DocumentDownloadError, sha256_bytes, validate_pdf
from app.ingestion.normalizer import NormalizedEvent
from app.ingestion.parsers.pdf import PdfExtractionError, extract_pdf_pages
from app.ingestion.sources.base import DownloadedDocument
from app.models import Company
from app.models.event import (
    CompanyEvent,
    CompanySource,
    DataOrigin,
    Document,
    DocumentPage,
    EventReference,
    EventType,
    MonitoringState,
    PublicationPrecision,
)
from app.services.events.event_service import save_event
from app.services.commentary import is_commentary_candidate, refresh_commentary_for_company
from app.schemas.event import (
    BootstrapRequest,
    CompanySourceCreate,
    CompanySourceRead,
    DocumentPageRead,
    DocumentRead,
    EventRead,
    EventReferenceRead,
    SourceStatusRead,
)

router = APIRouter(tags=["events"])
logger = logging.getLogger(__name__)
_last_bootstrap: dict[str, float] = {}
BOOTSTRAP_COOLDOWN_SECONDS = 300
MAX_MANUAL_PDF_BYTES = 10_000_000


def _event_read(event: CompanyEvent, db: Session) -> EventRead:
    from app.services.result_sources import event_source_verified
    documents = [
        DocumentRead(
            id=doc.id,
            source=doc.source,
            source_url=doc.source_url,
            document_url=doc.document_url,
            mime_type=doc.mime_type,
            file_name=doc.file_name,
            sha256_hash=doc.sha256_hash,
            published_at=doc.published_at,
            downloaded_at=doc.downloaded_at,
            processing_status=doc.processing_status,
            page_count=len(doc.pages),
            document_classification=event.event_type,
            download_url=f"/api/documents/{doc.id}/file" if doc.local_path else None,
        )
        for doc in event.documents
    ]
    return EventRead(
        id=event.id,
        company_id=event.company_id,
        symbol=event.company.symbol,
        company_name=event.company.name,
        event_type=event.event_type,
        title=event.title,
        description=event.description,
        published_at=event.published_at,
        source=event.source,
        external_id=event.external_id,
        source_url=event.source_url,
        document_url=event.document_url,
        importance=event.importance,
        processing_status=event.processing_status,
        created_at=event.created_at,
        data_origin=event.data_origin,
        source_verified=event_source_verified(db, event),
        publication_precision=event.publication_precision,
        period_end_date=event.period_end_date,
        fiscal_year=event.fiscal_year,
        quarter=event.quarter,
        reporting_basis=event.reporting_basis,
        references=[EventReferenceRead.model_validate(ref) for ref in event.references],
        documents=documents,
    )


def _event_query():
    return select(CompanyEvent).options(
        selectinload(CompanyEvent.company),
        selectinload(CompanyEvent.references),
        selectinload(CompanyEvent.documents).selectinload(Document.pages),
    )


@router.get("/api/events", response_model=list[EventRead])
def list_events(
    symbol: str | None = None,
    event_type: EventType | None = None,
    source: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[EventRead]:
    if from_date and to_date and from_date > to_date:
        raise HTTPException(
            status_code=422, detail="from_date must be on or before to_date"
        )
    stmt = _event_query()
    if symbol:
        stmt = stmt.join(Company).where(Company.symbol == symbol.strip().upper())
    if event_type:
        stmt = stmt.where(CompanyEvent.event_type == event_type)
    if source:
        stmt = stmt.where(CompanyEvent.references.any(source=source.strip().upper()))
    if from_date:
        stmt = stmt.where(
            CompanyEvent.published_at
            >= datetime.combine(from_date, time.min, tzinfo=timezone.utc)
        )
    if to_date:
        stmt = stmt.where(
            CompanyEvent.published_at
            < datetime.combine(
                to_date + timedelta(days=1), time.min, tzinfo=timezone.utc
            )
        )
    stmt = stmt.order_by(
        CompanyEvent.published_at.desc(), CompanyEvent.id.desc()
    ).limit(limit)
    return [_event_read(event, db) for event in db.scalars(stmt).all()]


@router.get("/api/events/sources/status", response_model=list[SourceStatusRead])
def source_status(db: Session = Depends(get_db)) -> list[SourceStatusRead]:
    enabled = {
        "NSE": settings.event_source_mode == "live" and settings.nse_source_enabled,
        "BSE": settings.event_source_mode == "live" and settings.bse_source_enabled,
        "ESDS_IR": settings.event_source_mode == "live"
        and settings.company_ir_source_enabled,
        "FIXTURE": settings.event_source_mode == "fixture",
    }
    states = {
        state.source: state for state in db.scalars(select(MonitoringState)).all()
    }
    result = []
    for name, is_enabled in enabled.items():
        state = states.get(name)
        status = (
            "disabled"
            if not is_enabled
            else (
                "error"
                if state and state.consecutive_failures
                else (
                    "healthy"
                    if state and state.last_successful_check
                    else "never_checked"
                )
            )
        )
        result.append(
            SourceStatusRead(
                source=name,
                enabled=is_enabled,
                status=status,
                last_attempted_check=state.last_attempted_check if state else None,
                last_successful_check=state.last_successful_check if state else None,
                last_external_id=state.last_external_id if state else None,
                last_error=state.last_error if state else None,
                consecutive_failures=state.consecutive_failures if state else 0,
            )
        )
    return result


@router.get("/api/events/{event_id}", response_model=EventRead)
def event_detail(event_id: int, db: Session = Depends(get_db)) -> EventRead:
    event = db.scalar(_event_query().where(CompanyEvent.id == event_id))
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return _event_read(event, db)


@router.get("/api/companies/{symbol}/events", response_model=list[EventRead])
def company_events(
    symbol: str,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[EventRead]:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    stmt = (
        _event_query()
        .where(CompanyEvent.company_id == company.id)
        .order_by(CompanyEvent.published_at.desc(), CompanyEvent.id.desc())
        .limit(limit)
    )
    return [_event_read(event, db) for event in db.scalars(stmt).all()]


@router.post("/api/companies/{symbol}/events/upload-pdf", response_model=EventRead)
async def upload_company_pdf(
    symbol: str,
    request: Request,
    title: str = Query(min_length=1, max_length=500),
    published_on: date = Query(),
    event_type: EventType = Query(default=EventType.OTHER),
    file_name: str = Query(default="document.pdf", max_length=255),
    source_url: str | None = Query(default=None, pattern=r"^https://", max_length=2048),
    db: Session = Depends(get_db),
) -> EventRead:
    """Import a PDF as manual evidence with its selected disclosure classification."""
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    if not title.strip():
        raise HTTPException(status_code=422, detail="Document title is required")
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/pdf"
    ):
        raise HTTPException(
            status_code=415, detail="Upload must have application/pdf content type"
        )
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > MAX_MANUAL_PDF_BYTES:
            raise HTTPException(status_code=413, detail="PDF exceeds the 10 MB limit")
    if not content:
        raise HTTPException(status_code=422, detail="PDF is empty")
    safe_name = re.sub(
        r"[^A-Za-z0-9._-]", "_", file_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    )[:100]
    if not safe_name.lower().endswith(".pdf"):
        safe_name += ".pdf"
    downloaded = DownloadedDocument(bytes(content), "application/pdf", safe_name)
    try:
        validate_pdf(downloaded, MAX_MANUAL_PDF_BYTES)
        if not extract_pdf_pages(downloaded.content):
            raise PdfExtractionError("PDF has no pages")
    except (DocumentDownloadError, PdfExtractionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    digest = sha256_bytes(downloaded.content)
    identity = f"{company.id}:{digest}"
    location = source_url or f"manual-upload:{identity}"
    normalized = NormalizedEvent(
        company_id=company.id,
        symbol=company.symbol,
        source="MANUAL_UPLOAD",
        event_type=event_type,
        title=title.strip(),
        description="Local PDF uploaded for testing; source contents are not independently verified.",
        published_at=datetime.combine(published_on, time.min, tzinfo=timezone.utc),
        external_id=identity,
        source_url=location,
        document_url=location,
        raw_payload={"file_name": safe_name, "sha256": digest},
        data_origin=DataOrigin.MANUAL,
        publication_precision=PublicationPrecision.DATE,
        period_end_date=None,
        fiscal_year=None,
        quarter=None,
        reporting_basis=None,
    )
    save_event(
        db,
        normalized,
        downloaded,
        None,
        LocalDocumentStorage(settings.document_storage_path),
    )
    if is_commentary_candidate(event_type, title):
        try:
            await asyncio.to_thread(refresh_commentary_for_company, db.get_bind(), company.id)
        except Exception:
            logger.exception("commentary.manual_process_failed", extra={"company_id": company.id})
    reference = db.scalar(
        select(EventReference).where(
            EventReference.source == "MANUAL_UPLOAD",
            EventReference.external_id == identity,
        )
    )
    event = db.scalar(_event_query().where(CompanyEvent.id == reference.event_id))
    return _event_read(event, db)


@router.post("/api/events/check", response_model=CheckSummary)
async def manual_check(db: Session = Depends(get_db)) -> CheckSummary:
    return await check_for_events(db)


@router.post("/api/events/bootstrap", response_model=CheckSummary)
async def bootstrap_events(
    payload: BootstrapRequest, db: Session = Depends(get_db)
) -> CheckSummary:
    if settings.event_source_mode != "live" or not settings.company_ir_source_enabled:
        raise HTTPException(
            status_code=409,
            detail="Enable the live company IR source before bootstrapping",
        )
    source = ESDSIRSource(
        bootstrap_since=datetime.now(timezone.utc) - timedelta(days=payload.days)
    )
    if not active_companies(db, source, {payload.symbol}):
        raise HTTPException(
            status_code=422,
            detail="A registered company and enabled ESDS IR source mapping are required",
        )
    now = time_module.monotonic()
    if (
        now - _last_bootstrap.get(payload.symbol, float("-inf"))
        < BOOTSTRAP_COOLDOWN_SECONDS
    ):
        raise HTTPException(
            status_code=429,
            detail="Bootstrap is limited to once every five minutes per symbol",
        )
    _last_bootstrap[payload.symbol] = now
    return await check_for_events(
        db,
        sources=[source],
        symbols={payload.symbol},
        since_override=source.bootstrap_since,
    )


@router.get("/api/documents/{document_id}/file")
def document_file(document_id: int, db: Session = Depends(get_db)) -> FileResponse:
    document = db.get(Document, document_id)
    if document is None or not document.local_path:
        raise HTTPException(status_code=404, detail="Document file not found")
    try:
        path = LocalDocumentStorage(settings.document_storage_path).resolve(
            document.local_path
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Document file not found") from exc
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=document.file_name or "document.pdf",
    )


@router.get("/api/documents/{document_id}/pages", response_model=list[DocumentPageRead])
def document_pages(
    document_id: int, db: Session = Depends(get_db)
) -> list[DocumentPage]:
    if db.get(Document, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return list(
        db.scalars(
            select(DocumentPage)
            .where(DocumentPage.document_id == document_id)
            .order_by(DocumentPage.page_number)
        ).all()
    )


@router.get("/api/companies/{symbol}/sources", response_model=list[CompanySourceRead])
def company_sources(symbol: str, db: Session = Depends(get_db)) -> list[CompanySource]:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return list(
        db.scalars(
            select(CompanySource)
            .where(CompanySource.company_id == company.id)
            .order_by(CompanySource.source)
        ).all()
    )


@router.post(
    "/api/companies/{symbol}/sources", response_model=CompanySourceRead, status_code=201
)
def create_company_source(
    symbol: str, payload: CompanySourceCreate, db: Session = Depends(get_db)
) -> CompanySource:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    existing = db.scalar(select(CompanySource).where(
        CompanySource.company_id == company.id, CompanySource.source == payload.source
    ))
    if existing is not None:
        if all(getattr(existing, key) == value for key, value in payload.model_dump().items()):
            return existing
        raise HTTPException(status_code=409, detail="Company source already exists; update its configuration explicitly")
    row = CompanySource(company_id=company.id, **payload.model_dump())
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Company source already exists"
        ) from exc
    db.refresh(row)
    return row


@router.put(
    "/api/companies/{symbol}/sources/{source}", response_model=CompanySourceRead
)
def update_company_source(
    symbol: str,
    source: str,
    payload: CompanySourceCreate,
    db: Session = Depends(get_db),
) -> CompanySource:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    if payload.source != source.strip().upper():
        raise HTTPException(
            status_code=422, detail="Source in path and body must match"
        )
    row = db.scalar(
        select(CompanySource).where(
            CompanySource.company_id == company.id,
            CompanySource.source == payload.source,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Company source not found")
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row
