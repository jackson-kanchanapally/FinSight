"""Source observations survive failed downloads; verification is append-only."""
from datetime import date, datetime
from sqlalchemy import Date, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ResultDiscovery(Base):
    __tablename__ = "result_discoveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    identity_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("company_events.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(String(2048))
    document_url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[date | None] = mapped_column(Date)
    scope: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="DETECTED")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResultSourceCheck(Base):
    __tablename__ = "result_source_checks"
    __table_args__ = (UniqueConstraint("company_id", "source", name="uq_result_source_company"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(50))
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(String(200))
    coverage: Mapped[str] = mapped_column(Text)


class ResultVerification(Base):
    __tablename__ = "result_verifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    period_id: Mapped[int] = mapped_column(ForeignKey("financial_periods.id", ondelete="CASCADE"), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(30))
    details: Mapped[dict] = mapped_column(JSON)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
