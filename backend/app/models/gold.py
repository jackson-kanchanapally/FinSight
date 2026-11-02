"""Gold prices, dated news, and durable monitoring state."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class GoldState(Base):
    __tablename__ = "gold_monitor_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    errors: Mapped[list] = mapped_column(JSON, default=list)
    move_anchor: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    move_anchor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_move_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GoldSnapshot(Base):
    __tablename__ = "gold_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    prices: Mapped[dict] = mapped_column(JSON)
    analysis: Mapped[dict] = mapped_column(JSON)
    errors: Mapped[list] = mapped_column(JSON, default=list)


class GoldHeadline(Base):
    __tablename__ = "gold_headlines"

    id: Mapped[int] = mapped_column(primary_key=True)
    identity: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(80))
    url: Mapped[str] = mapped_column(String(2048))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    impact: Mapped[str] = mapped_column(String(20))
    impact_reason: Mapped[str] = mapped_column(String(500))
    important: Mapped[bool] = mapped_column(Boolean, default=False)
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
