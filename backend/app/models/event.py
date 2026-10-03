import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EventType(str, enum.Enum):
    FINANCIAL_RESULTS = "FINANCIAL_RESULTS"
    BOARD_MEETING = "BOARD_MEETING"
    EARNINGS_CALL = "EARNINGS_CALL"
    INVESTOR_PRESENTATION = "INVESTOR_PRESENTATION"
    ANNUAL_REPORT = "ANNUAL_REPORT"
    ORDER_WIN = "ORDER_WIN"
    ORDER_CANCELLATION = "ORDER_CANCELLATION"
    PARTNERSHIP = "PARTNERSHIP"
    ACQUISITION = "ACQUISITION"
    DIVESTMENT = "DIVESTMENT"
    CAPEX = "CAPEX"
    NEW_PRODUCT = "NEW_PRODUCT"
    MANAGEMENT_CHANGE = "MANAGEMENT_CHANGE"
    AUDITOR_CHANGE = "AUDITOR_CHANGE"
    DIVIDEND = "DIVIDEND"
    BONUS = "BONUS"
    STOCK_SPLIT = "STOCK_SPLIT"
    BUYBACK = "BUYBACK"
    FUND_RAISE = "FUND_RAISE"
    CREDIT_RATING = "CREDIT_RATING"
    SHAREHOLDING = "SHAREHOLDING"
    PROMOTER_TRANSACTION = "PROMOTER_TRANSACTION"
    PLEDGE = "PLEDGE"
    REGULATORY = "REGULATORY"
    LITIGATION = "LITIGATION"
    COMPLIANCE = "COMPLIANCE"
    OTHER = "OTHER"


class ProcessingStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    NO_TEXT = "NO_TEXT"
    FAILED = "FAILED"


class DataOrigin(str, enum.Enum):
    LIVE = "LIVE"
    FIXTURE = "FIXTURE"
    MANUAL = "MANUAL"


class PublicationPrecision(str, enum.Enum):
    TIMESTAMP = "TIMESTAMP"
    DATE = "DATE"


class ReportingBasis(str, enum.Enum):
    STANDALONE = "STANDALONE"
    CONSOLIDATED = "CONSOLIDATED"
    BOTH = "BOTH"


class CompanyEvent(Base):
    __tablename__ = "company_events"
    __table_args__ = (
        Index("ix_company_events_company_published", "company_id", "published_at"),
        Index("ix_company_events_published_at", "published_at"),
        Index("ix_company_events_event_type", "event_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    document_url: Mapped[str | None] = mapped_column(String(2048))
    data_origin: Mapped[DataOrigin] = mapped_column(
        Enum(DataOrigin, name="data_origin", create_type=False),
        nullable=False,
        default=DataOrigin.LIVE,
        server_default="LIVE",
    )
    publication_precision: Mapped[PublicationPrecision] = mapped_column(
        Enum(PublicationPrecision, name="publication_precision", create_type=False),
        nullable=False,
        default=PublicationPrecision.TIMESTAMP,
        server_default="TIMESTAMP",
    )
    period_end_date: Mapped[date | None] = mapped_column(Date)
    fiscal_year: Mapped[str | None] = mapped_column(String(10))
    quarter: Mapped[int | None] = mapped_column(Integer)
    reporting_basis: Mapped[ReportingBasis | None] = mapped_column(
        Enum(ReportingBasis, name="reporting_basis", create_type=False)
    )
    importance: Mapped[str] = mapped_column(
        String(20), nullable=False, default="NORMAL", server_default="NORMAL"
    )
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status"),
        nullable=False,
        default=ProcessingStatus.PENDING,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    company: Mapped["Company"] = relationship()
    documents: Mapped[list["Document"]] = relationship(back_populates="event")
    references: Mapped[list["EventReference"]] = relationship(back_populates="event")


class EventReference(Base):
    """Every source observation, including duplicates merged into one event."""

    __tablename__ = "event_references"
    __table_args__ = (
        Index(
            "ux_event_references_source_external_id",
            "source",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
            sqlite_where=text("external_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    document_url: Mapped[str | None] = mapped_column(String(2048))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    data_origin: Mapped[DataOrigin] = mapped_column(
        Enum(DataOrigin, name="data_origin", create_type=False),
        nullable=False,
        default=DataOrigin.LIVE,
        server_default="LIVE",
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    event: Mapped[CompanyEvent] = relationship(back_populates="references")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_company_id", "company_id"),
        Index("ix_documents_sha256_hash", "sha256_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    document_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    document_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PDF"
    )
    mime_type: Mapped[str | None] = mapped_column(String(100))
    file_name: Mapped[str | None] = mapped_column(String(255))
    local_path: Mapped[str | None] = mapped_column(String(1024))
    sha256_hash: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus, name="processing_status", create_type=False),
        nullable=False,
        default=ProcessingStatus.PENDING,
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    event: Mapped[CompanyEvent] = relationship(back_populates="documents")
    pages: Mapped[list["DocumentPage"]] = relationship(back_populates="document")


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "page_number", name="uq_document_pages_document_page"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    document: Mapped[Document] = relationship(back_populates="pages")


class MonitoringState(Base):
    __tablename__ = "monitoring_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    last_successful_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_attempted_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_external_id: Mapped[str | None] = mapped_column(String(255))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CompanySource(Base):
    __tablename__ = "company_sources"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "source", name="uq_company_sources_company_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_symbol: Mapped[str | None] = mapped_column(String(100))
    source_company_id: Mapped[str | None] = mapped_column(String(255))
    ir_url: Mapped[str | None] = mapped_column(String(2048))
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
