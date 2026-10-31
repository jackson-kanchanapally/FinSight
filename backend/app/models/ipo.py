"""Mainboard research is independent of portfolio companies."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IPOIssue(Base):
    __tablename__ = "ipo_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    opens_on: Mapped[date | None] = mapped_column(Date, index=True)
    closes_on: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(String(2048))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column(JSON)


class IPOCheck(Base):
    __tablename__ = "ipo_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    weekly_key: Mapped[str | None] = mapped_column(String(20))
