import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.deduplicator import event_fingerprint, existing_document, existing_fingerprint, existing_reference
from app.ingestion.downloader import sha256_bytes, validate_pdf
from app.ingestion.normalizer import NormalizedEvent
from app.ingestion.parsers.pdf import PdfExtractionError, extract_pdf_pages
from app.ingestion.sources.base import DownloadedDocument
from app.ingestion.storage import DocumentStorage
from app.models.event import CompanyEvent, Document, DocumentPage, EventReference, ProcessingStatus

logger = logging.getLogger(__name__)


def _document_status(db: Session, event: CompanyEvent, normalized: NormalizedEvent,
                     downloaded: DownloadedDocument | None, error: Exception | None,
                     storage: DocumentStorage) -> bool:
    """Attach one document attempt. Returns True only for a newly stored PDF."""
    if normalized.document_url is None:
        event.processing_status = ProcessingStatus.PROCESSED
        return False
    previous = db.scalar(select(Document).where(Document.event_id == event.id,
                                                Document.document_url == normalized.document_url))
    if previous and previous.processing_status != ProcessingStatus.FAILED:
        return False
    document = previous or Document(company_id=event.company_id, event_id=event.id,
                                    source=normalized.source, source_url=normalized.source_url,
                                    document_url=normalized.document_url, document_type="PDF",
                                    published_at=normalized.published_at)
    if not previous:
        db.add(document)
    if error is not None or downloaded is None:
        document.processing_status = ProcessingStatus.FAILED
        document.last_error = type(error).__name__ if error else "DownloadError"
        event.processing_status = ProcessingStatus.FAILED
        return False

    digest = sha256_bytes(downloaded.content)
    document.local_path = storage.store(downloaded.content, digest)
    document.sha256_hash = digest
    document.mime_type = downloaded.mime_type
    document.file_name = downloaded.file_name
    document.downloaded_at = datetime.now(timezone.utc)
    document.last_error = None
    db.flush()
    page_count = 0
    text_extracted = False
    try:
        pages = extract_pdf_pages(downloaded.content)
        page_count = len(pages)
        text_extracted = any(text for _, text in pages)
        for page_number, text in pages:
            db.add(DocumentPage(document_id=document.id, page_number=page_number, text=text))
        document.processing_status = (ProcessingStatus.PROCESSED if text_extracted
                                      else ProcessingStatus.NO_TEXT)
    except PdfExtractionError:
        document.processing_status = ProcessingStatus.FAILED
        document.last_error = "PdfExtractionError"
    event.processing_status = document.processing_status
    logger.info("event_monitor.document_stored", extra={"sha256": digest,
                                                         "event_id": event.id,
                                                         "document_id": document.id,
                                                         "size_bytes": len(downloaded.content),
                                                         "page_count": page_count,
                                                         "text_extracted": text_extracted})
    return True


def save_event(db: Session, normalized: NormalizedEvent, downloaded: DownloadedDocument | None,
               document_error: Exception | None, storage: DocumentStorage) -> tuple[bool, bool]:
    """Deduplicate by source identity, PDF content, then metadata fingerprint.

    Every new source observation is retained even when it resolves to an existing
    canonical event. DB constraints provide a final guard against concurrent inserts.
    """
    reference = existing_reference(db, normalized.source, normalized.external_id)
    if reference is not None:
        event = db.get(CompanyEvent, reference.event_id)
        if event and normalized.document_url:
            existing = db.scalar(select(Document).where(Document.event_id == event.id,
                                                        Document.document_url == normalized.document_url))
            if existing and existing.processing_status == ProcessingStatus.FAILED:
                stored = _document_status(db, event, normalized, downloaded, document_error, storage)
                db.commit()
                return False, stored
        return False, False

    digest = sha256_bytes(downloaded.content) if downloaded else None
    matching_document = existing_document(db, digest, normalized.company_id, normalized.data_origin) if digest else None
    fingerprint = event_fingerprint(normalized, digest)
    event = (matching_document.event if matching_document else existing_fingerprint(db, fingerprint))
    is_new = event is None
    if event is None:
        event = CompanyEvent(company_id=normalized.company_id, event_type=normalized.event_type,
                             title=normalized.title, description=normalized.description,
                             published_at=normalized.published_at, source=normalized.source,
                             external_id=normalized.external_id, source_url=normalized.source_url,
                             document_url=normalized.document_url, fingerprint=fingerprint,
                             data_origin=normalized.data_origin,
                             publication_precision=normalized.publication_precision,
                             period_end_date=normalized.period_end_date,
                             fiscal_year=normalized.fiscal_year, quarter=normalized.quarter,
                             reporting_basis=normalized.reporting_basis,
                             processing_status=ProcessingStatus.PENDING)
        db.add(event)
        db.flush()
    same_observation = (not normalized.external_id and db.scalar(
        select(EventReference).where(EventReference.event_id == event.id,
                                     EventReference.source == normalized.source,
                                     EventReference.source_url == normalized.source_url)))
    if not same_observation:
        db.add(EventReference(event_id=event.id, source=normalized.source,
                              external_id=normalized.external_id, source_url=normalized.source_url,
                              document_url=normalized.document_url, raw_payload=normalized.raw_payload,
                              data_origin=normalized.data_origin))
    stored = False
    if normalized.document_url and matching_document is None:
        stored = _document_status(db, event, normalized, downloaded, document_error, storage)
    elif is_new:
        event.processing_status = ProcessingStatus.PROCESSED
    db.commit()
    return is_new, stored
