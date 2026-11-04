"""Immutable daily market reviews and their exchange-calendar cache."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarketBriefState(Base):
    __tablename__ = "market_brief_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    calendar: Mapped[dict] = mapped_column(JSON, default=dict)
    calendar_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))


class MarketBrief(Base):
    __tablename__ = "market_briefs"
    __table_args__ = (UniqueConstraint("session_date", "slot", name="uq_market_brief_session_slot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_date: Mapped[date] = mapped_column(Date, index=True)
    slot: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    morning_id: Mapped[int | None] = mapped_column(ForeignKey("market_briefs.id"))
    previous_close_id: Mapped[int | None] = mapped_column(ForeignKey("market_briefs.id"))
    evidence: Mapped[dict] = mapped_column(JSON)
    analysis: Mapped[dict] = mapped_column(JSON)
    assessment: Mapped[dict] = mapped_column(JSON)
