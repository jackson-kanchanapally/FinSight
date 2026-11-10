"""Exchange corporate actions and measured market reactions to company events."""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CorporateAction(Base):
    """One NSE corporate action. ``adjustment_factor`` multiplies pre-ex-date prices
    onto the post-ex-date share basis (0.5 for a 1:1 bonus, 0.1 for a 10:1 split)."""

    __tablename__ = "corporate_actions"
    __table_args__ = (
        UniqueConstraint("company_id", "ex_date", "action_type", "purpose", name="uq_corporate_action"),
        Index("ix_corporate_actions_company_ex_date", "company_id", "ex_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    action_type: Mapped[str] = mapped_column(String(20), nullable=False)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    adjustment_factor: Mapped[Decimal | None] = mapped_column(Numeric(18, 10))
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="NSE_MCP")
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class CorporateActionSync(Base):
    """Which window of corporate actions was last confirmed for a company.

    Lets valuation distinguish "checked, no split" from "never checked"."""

    __tablename__ = "corporate_action_syncs"

    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True
    )
    from_date: Mapped[date | None] = mapped_column(Date)
    to_date: Mapped[date | None] = mapped_column(Date)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    complete: Mapped[bool] = mapped_column(nullable=False, default=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))


class EventPriceReaction(Base):
    """Share-price move after a company event, measured against the Nifty 50."""

    __tablename__ = "event_price_reactions"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_event_price_reaction_event"),
        Index("ix_event_price_reactions_company", "company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # PENDING | COMPLETE | UNAVAILABLE
    reason: Mapped[str | None] = mapped_column(String(200))
    timing: Mapped[str | None] = mapped_column(String(24))
    base_date: Mapped[date | None] = mapped_column(Date)
    base_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    reaction_date: Mapped[date | None] = mapped_column(Date)
    benchmark: Mapped[str] = mapped_column(String(40), nullable=False, default="Nifty 50")
    windows: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    volume_ratio: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    adjustments: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
