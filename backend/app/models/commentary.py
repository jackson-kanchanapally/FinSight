"""Persistent, attributed management statements and versioned guidance."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CommentaryDocument(Base):
    """Association survives even when a document contains no extractable statement."""

    __tablename__ = "commentary_documents"
    __table_args__ = (
        UniqueConstraint(
            "financial_period_id", "document_id", name="uq_commentary_document_period"
        ),
        Index("ix_commentary_documents_period", "financial_period_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    financial_period_id: Mapped[int] = mapped_column(
        ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    fiscal_year: Mapped[str] = mapped_column(String(10), nullable=False)
    quarter: Mapped[int | None] = mapped_column(Integer)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_priority: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ManagementStatement(Base):
    __tablename__ = "management_statements"
    __table_args__ = (
        UniqueConstraint(
            "financial_period_id",
            "document_id",
            "page_number",
            "raw_statement",
            name="uq_management_statement_source",
        ),
        Index("ix_management_statements_period", "financial_period_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    financial_period_id: Mapped[int] = mapped_column(
        ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_name: Mapped[str | None] = mapped_column(String(120))
    speaker_role: Mapped[str | None] = mapped_column(String(120))
    statement_type: Mapped[str] = mapped_column(String(40), nullable=False)
    topic: Mapped[str] = mapped_column(String(40), nullable=False)
    raw_statement: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_summary: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    section_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="UNKNOWN"
    )
    extraction_confidence: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def evidence_id(self) -> str:
        return f"S{self.id}"


class ManagementGuidance(Base):
    __tablename__ = "management_guidance"
    __table_args__ = (
        UniqueConstraint("statement_id", name="uq_management_guidance_statement"),
        Index("ix_management_guidance_period", "financial_period_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(
        ForeignKey("management_statements.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    financial_period_id: Mapped[int] = mapped_column(
        ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    target_period: Mapped[str] = mapped_column(String(30), nullable=False)
    lower_bound: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    upper_bound: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
