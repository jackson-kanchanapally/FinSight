from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.event import (
    DataOrigin,
    EventType,
    ProcessingStatus,
    PublicationPrecision,
    ReportingBasis,
)


class EventReferenceRead(BaseModel):
    source: str
    external_id: str | None
    source_url: str
    document_url: str | None
    discovered_at: datetime
    data_origin: DataOrigin
    model_config = ConfigDict(from_attributes=True)


class DocumentRead(BaseModel):
    id: int
    source: str
    source_url: str
    document_url: str
    mime_type: str | None
    file_name: str | None
    sha256_hash: str | None
    published_at: datetime | None
    downloaded_at: datetime | None
    processing_status: ProcessingStatus
    document_classification: EventType
    page_count: int
    download_url: str | None


class DocumentPageRead(BaseModel):
    page_number: int
    text: str
    model_config = ConfigDict(from_attributes=True)


class EventRead(BaseModel):
    id: int
    company_id: int
    symbol: str
    company_name: str
    event_type: EventType
    title: str
    description: str | None
    published_at: datetime
    source: str
    external_id: str | None
    source_url: str
    document_url: str | None
    importance: str
    processing_status: ProcessingStatus
    data_origin: DataOrigin
    source_verified: bool
    publication_precision: PublicationPrecision
    period_end_date: date | None
    fiscal_year: str | None
    quarter: int | None
    reporting_basis: ReportingBasis | None
    created_at: datetime
    references: list[EventReferenceRead]
    documents: list[DocumentRead]


class SourceStatusRead(BaseModel):
    source: str
    enabled: bool
    status: str
    last_attempted_check: datetime | None
    last_successful_check: datetime | None
    last_external_id: str | None
    last_error: str | None
    consecutive_failures: int


class BootstrapRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.-]+$")
    days: int = Field(default=90, ge=1, le=90)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.upper()


class CompanySourceCreate(BaseModel):
    source: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9_]+$")
    external_symbol: str | None = Field(default=None, max_length=100)
    source_company_id: str | None = Field(default=None, max_length=255)
    ir_url: str | None = Field(default=None, max_length=2048)
    enabled: bool = True

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        return value.upper()

    @field_validator("ir_url")
    @classmethod
    def secure_ir_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("IR URL must use HTTPS")
        return value


class CompanySourceRead(CompanySourceCreate):
    id: int
    company_id: int
    model_config = ConfigDict(from_attributes=True)
