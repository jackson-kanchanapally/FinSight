"""Dated market observations and deterministic valuation records."""

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import Date, DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"
    __table_args__ = (
        UniqueConstraint("company_id", "as_of_date", "provider", "data_origin", name="uq_market_observation"),
        Index("ix_market_snapshots_company_date", "company_id", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    close_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    open_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    high_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    low_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[int | None] = mapped_column(Numeric(24, 0))
    pe_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    market_cap_crore: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    forward_eps_growth_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    growth_source_url: Mapped[str | None] = mapped_column(String(2048))
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="MANUAL", server_default="MANUAL")
    provider_symbol: Mapped[str | None] = mapped_column(String(100))
    data_origin: Mapped[str] = mapped_column(String(12), nullable=False, default="MANUAL", server_default="MANUAL")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                    default=lambda: datetime.now(timezone.utc))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                   default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR", server_default="INR")
    shares_outstanding: Mapped[Decimal | None] = mapped_column(Numeric(24, 0))
    week_52_high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    week_52_low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    growth_source_type: Mapped[str | None] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                  onupdate=func.now(), nullable=False)

    @property
    def peg_ratio(self) -> Decimal | None:
        """Legacy reference PEG based on the entered third-party P/E."""
        if (self.pe_ratio is None or self.forward_eps_growth_pct is None
                or self.forward_eps_growth_pct <= 0 or not self.growth_source_url):
            return None
        return (self.pe_ratio / self.forward_eps_growth_pct).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP)


class ValuationSnapshot(Base):
    __tablename__ = "valuation_snapshots"
    __table_args__ = (
        UniqueConstraint("market_snapshot_id", "scope", name="uq_valuation_snapshot_market_scope"),
        Index("ix_valuation_snapshots_company_market", "company_id", "market_snapshot_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    market_snapshot_id: Mapped[int] = mapped_column(ForeignKey("market_snapshots.id", ondelete="CASCADE"), nullable=False)
    financial_period_id: Mapped[int | None] = mapped_column(ForeignKey("financial_periods.id", ondelete="SET NULL"))
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    ttm_revenue: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    ttm_ebitda: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    ttm_pat: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    ttm_eps: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    pe_ttm: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    ps_ttm: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    ev_sales_ttm: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    ev_ebitda_ttm: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    earnings_yield_pct: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    price_to_book: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    fcf_yield_pct: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    peg_forward: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    enterprise_value_crore: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    calculation_status: Mapped[str] = mapped_column(String(24), nullable=False)
    metric_details: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now(), nullable=False)


class ReferenceMetric(Base):
    __tablename__ = "reference_metrics"
    __table_args__ = (UniqueConstraint("company_id", "metric_code", "provider", "as_of_date",
                                      name="uq_reference_metric_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(40), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
