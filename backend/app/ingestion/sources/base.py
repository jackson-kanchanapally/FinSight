from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.models.event import DataOrigin, PublicationPrecision, ReportingBasis


class SourceUnavailableError(Exception):
    pass


@dataclass(frozen=True)
class MonitoredCompany:
    company_id: int
    symbol: str
    name: str
    external_symbol: str
    isin: str | None = None
    ir_url: str | None = None
    source_company_id: str | None = None
    known_documents: dict[str, datetime] = field(default_factory=dict)


class SourceEvent(BaseModel):
    """Source data before matching to a company or assigning an event type."""

    external_symbol: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    published_at: datetime
    external_id: str | None = Field(default=None, max_length=255)
    source_url: str = Field(min_length=1, max_length=2048)
    document_url: str | None = Field(default=None, max_length=2048)
    raw_payload: dict | None = None
    publication_precision: PublicationPrecision = PublicationPrecision.TIMESTAMP
    period_end_date: date | None = None
    fiscal_year: str | None = Field(default=None, max_length=10)
    quarter: int | None = Field(default=None, ge=1, le=4)
    reporting_basis: ReportingBasis | None = None

    @field_validator("published_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("published_at must include a timezone")
        return value


@dataclass(frozen=True)
class DownloadedDocument:
    content: bytes
    mime_type: str
    file_name: str
    last_modified: datetime | None = None


class EventSource(ABC):
    name: str
    origin: DataOrigin = DataOrigin.LIVE

    @abstractmethod
    async def fetch_events(
        self, companies: list[MonitoredCompany], since: datetime | None
    ) -> list[SourceEvent]:
        """Fetch a source feed once and return source-level announcements."""

    @abstractmethod
    async def download_document(self, event: SourceEvent) -> DownloadedDocument:
        """Download only a document URL obtained from this trusted adapter."""
