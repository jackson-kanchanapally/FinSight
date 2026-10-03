import hashlib
import re
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.normalizer import NormalizedEvent
from app.models.event import CompanyEvent, DataOrigin, Document, EventReference


def event_fingerprint(event: NormalizedEvent, document_hash: str | None = None) -> str:
    title = re.sub(r"\W+", " ", event.title.casefold()).strip()
    day = event.published_at.astimezone(timezone.utc).date().isoformat()
    # A PDF is stronger evidence than a title: separate same-day filings can
    # share a title but carry different content.
    identity = f"pdf:{document_hash}" if document_hash else f"metadata:{day}|{title}"
    return hashlib.sha256(
        f"{event.company_id}|{event.data_origin.value}|{identity}".encode()
    ).hexdigest()


def existing_reference(
    db: Session, source: str, external_id: str | None
) -> EventReference | None:
    if not external_id:
        return None
    return db.scalar(
        select(EventReference).where(
            EventReference.source == source, EventReference.external_id == external_id
        )
    )


def existing_document(
    db: Session, digest: str, company_id: int, origin: DataOrigin
) -> Document | None:
    # Fixture evidence is isolated; manual and official observations can share
    # the earliest canonical PDF without changing its original provenance.
    return db.scalar(
        select(Document)
        .join(CompanyEvent)
        .where(
            Document.sha256_hash == digest,
            Document.company_id == company_id,
            (
                CompanyEvent.data_origin == DataOrigin.FIXTURE
                if origin == DataOrigin.FIXTURE
                else CompanyEvent.data_origin != DataOrigin.FIXTURE
            ),
        )
        .order_by(CompanyEvent.created_at, Document.id)
    )


def existing_fingerprint(db: Session, fingerprint: str) -> CompanyEvent | None:
    return db.scalar(
        select(CompanyEvent).where(CompanyEvent.fingerprint == fingerprint)
    )
