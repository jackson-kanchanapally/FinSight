"""User supplied, dated market observations for valuation context."""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), unique=True, nullable=False)
    close_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    pe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    market_cap_crore: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    forward_eps_growth_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    growth_source_url: Mapped[str | None] = mapped_column(String(2048))
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                  onupdate=func.now(), nullable=False)

    @property
    def peg_ratio(self) -> Decimal | None:
        if (self.pe_ratio is None or self.forward_eps_growth_pct is None
                or self.forward_eps_growth_pct <= 0 or not self.growth_source_url):
            return None
        return (self.pe_ratio / self.forward_eps_growth_pct).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP)
