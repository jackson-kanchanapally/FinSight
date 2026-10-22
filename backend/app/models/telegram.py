"""Durable event delivery; event and queue record commit together."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TelegramDelivery(Base):
    __tablename__ = "telegram_deliveries"
    __table_args__ = (UniqueConstraint("event_id", "chat_id", "version", name="uq_telegram_event_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[str] = mapped_column(String(80), default="automatic-v2", server_default="automatic-v2")
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    financial_analysis_id: Mapped[int | None] = mapped_column(ForeignKey("ai_analyses.id", ondelete="SET NULL"))
    financial_period_id: Mapped[int | None] = mapped_column(ForeignKey("financial_periods.id", ondelete="SET NULL"))
    notification: Mapped[dict | None] = mapped_column(JSON)
    message_parts: Mapped[list | None] = mapped_column(JSON)
    message_ids: Mapped[list | None] = mapped_column(JSON)
    sending_part: Mapped[int | None] = mapped_column(Integer)
    parse_mode: Mapped[str | None] = mapped_column(String(10))
    chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    message: Mapped[str | None] = mapped_column(Text)
    analysis_id: Mapped[int | None] = mapped_column(
        ForeignKey("event_analyses.id", ondelete="SET NULL"))
    ai_status: Mapped[str | None] = mapped_column(String(30))
    message_id: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TelegramInbox(Base):
    """Durable getUpdates cursor and a short cross-process polling lease."""
    __tablename__ = "telegram_inbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    next_update_id: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))


class TelegramQuery(Base):
    __tablename__ = "telegram_queries"
    id: Mapped[int] = mapped_column(primary_key=True)
    update_id: Mapped[int] = mapped_column(unique=True)
    user_id: Mapped[str] = mapped_column(String(100))
    chat_id: Mapped[str] = mapped_column(String(100))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_data_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_hash: Mapped[str | None] = mapped_column(String(64))
    model_route: Mapped[str | None] = mapped_column(String(200))
    evidence_registry: Mapped[dict | None] = mapped_column(JSON)
    structured_output: Mapped[dict | None] = mapped_column(JSON)
    source_references: Mapped[list | None] = mapped_column(JSON)
    message: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(default=0)
