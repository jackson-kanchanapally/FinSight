"""Source facts and deterministic metrics are stored separately."""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PeriodType(str, enum.Enum):
    QUARTERLY = "QUARTERLY"
    HALF_YEARLY = "HALF_YEARLY"
    NINE_MONTH = "NINE_MONTH"
    ANNUAL = "ANNUAL"


class FinancialScope(str, enum.Enum):
    STANDALONE = "STANDALONE"
    CONSOLIDATED = "CONSOLIDATED"


class FinancialStatus(str, enum.Enum):
    PENDING = "PENDING"
    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class MetricCode(str, enum.Enum):
    REVENUE_FROM_OPERATIONS = "REVENUE_FROM_OPERATIONS"
    TOTAL_INCOME = "TOTAL_INCOME"
    OTHER_INCOME = "OTHER_INCOME"
    EBITDA = "EBITDA"
    ADJUSTED_EBITDA = "ADJUSTED_EBITDA"
    EBITDA_MARGIN = "EBITDA_MARGIN"
    EBIT = "EBIT"
    PBT = "PBT"
    PAT = "PAT"
    PAT_MARGIN = "PAT_MARGIN"
    BASIC_EPS = "BASIC_EPS"
    DILUTED_EPS = "DILUTED_EPS"
    TOTAL_EXPENSES = "TOTAL_EXPENSES"
    FINANCE_COST = "FINANCE_COST"
    DEPRECIATION = "DEPRECIATION"
    EMPLOYEE_EXPENSE = "EMPLOYEE_EXPENSE"
    OTHER_EXPENSE = "OTHER_EXPENSE"
    TOTAL_TAX_EXPENSE = "TOTAL_TAX_EXPENSE"
    EXCEPTIONAL_ITEMS = "EXCEPTIONAL_ITEMS"
    PAT_ATTRIBUTABLE = "PAT_ATTRIBUTABLE"
    NON_CONTROLLING_INTEREST = "NON_CONTROLLING_INTEREST"
    REVENUE_YOY = "REVENUE_YOY"
    REVENUE_QOQ = "REVENUE_QOQ"
    PAT_YOY = "PAT_YOY"
    PAT_QOQ = "PAT_QOQ"
    EBITDA_MARGIN_CHANGE_BPS = "EBITDA_MARGIN_CHANGE_BPS"
    OPERATING_CASH_FLOW = "OPERATING_CASH_FLOW"
    CAPITAL_EXPENDITURE = "CAPITAL_EXPENDITURE"
    TOTAL_DEBT = "TOTAL_DEBT"
    CASH_AND_CASH_EQUIVALENTS = "CASH_AND_CASH_EQUIVALENTS"
    TOTAL_EQUITY = "TOTAL_EQUITY"


class FinancialUnit(str, enum.Enum):
    INR_CRORE = "INR_CRORE"
    INR_PER_SHARE = "INR_PER_SHARE"
    PERCENT = "PERCENT"
    BASIS_POINTS = "BASIS_POINTS"


class OriginalUnit(str, enum.Enum):
    INR = "INR"
    INR_LAKH = "INR_LAKH"
    INR_MILLION = "INR_MILLION"
    INR_CRORE = "INR_CRORE"
    INR_PER_SHARE = "INR_PER_SHARE"


class FactMethod(str, enum.Enum):
    PDF_TABLE = "PDF_TABLE"
    STRUCTURED = "STRUCTURED"
    CALCULATED = "CALCULATED"


class FactStatus(str, enum.Enum):
    VERIFIED_PDF = "VERIFIED_PDF"
    VERIFIED_STRUCTURED = "VERIFIED_STRUCTURED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    MANUALLY_VERIFIED = "MANUALLY_VERIFIED"


class FinancialPeriod(Base):
    __tablename__ = "financial_periods"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "period_end",
            "period_type",
            "scope",
            name="uq_financial_period_identity",
        ),
        Index("ix_financial_periods_company_end", "company_id", "period_end"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    fiscal_year: Mapped[str] = mapped_column(String(10), nullable=False)
    quarter: Mapped[int | None] = mapped_column(Integer)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_type: Mapped[PeriodType] = mapped_column(
        Enum(PeriodType, name="financial_period_type"), nullable=False
    )
    scope: Mapped[FinancialScope] = mapped_column(
        Enum(FinancialScope, name="financial_scope"), nullable=False
    )
    status: Mapped[FinancialStatus] = mapped_column(
        Enum(FinancialStatus, name="financial_status"), nullable=False
    )
    status_note: Mapped[str | None] = mapped_column(Text)
    source_event_id: Mapped[int] = mapped_column(
        ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    facts: Mapped[list["FinancialFact"]] = relationship(
        back_populates="period", cascade="all, delete-orphan"
    )
    metrics: Mapped[list["FinancialMetric"]] = relationship(
        back_populates="period",
        cascade="all, delete-orphan",
        foreign_keys="FinancialMetric.financial_period_id",
    )


class FinancialFact(Base):
    __tablename__ = "financial_facts"
    __table_args__ = (
        UniqueConstraint(
            "financial_period_id", "metric_code", name="uq_financial_fact_metric"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    financial_period_id: Mapped[int] = mapped_column(
        ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False
    )
    metric_code: Mapped[MetricCode] = mapped_column(
        Enum(MetricCode, name="financial_metric_code"), nullable=False
    )
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[FinancialUnit] = mapped_column(
        Enum(FinancialUnit, name="financial_unit"), nullable=False
    )
    original_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 6))
    original_unit: Mapped[OriginalUnit | None] = mapped_column(
        Enum(OriginalUnit, name="financial_original_unit")
    )
    rounding_tolerance: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_label: Mapped[str] = mapped_column(String(255), nullable=False)
    source_column_date: Mapped[date] = mapped_column(Date, nullable=False)
    extraction_method: Mapped[FactMethod] = mapped_column(
        Enum(FactMethod, name="financial_fact_method"), nullable=False
    )
    status: Mapped[FactStatus] = mapped_column(
        Enum(FactStatus, name="financial_fact_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    period: Mapped[FinancialPeriod] = relationship(back_populates="facts")
    corrections: Mapped[list["FinancialFactCorrection"]] = relationship(
        back_populates="fact"
    )


class FinancialFactCorrection(Base):
    __tablename__ = "financial_fact_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    fact_id: Mapped[int] = mapped_column(
        ForeignKey("financial_facts.id", ondelete="CASCADE"), nullable=False
    )
    previous_value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    corrected_value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    fact: Mapped[FinancialFact] = relationship(back_populates="corrections")


class FinancialMetric(Base):
    __tablename__ = "financial_metrics"
    __table_args__ = (
        UniqueConstraint(
            "financial_period_id", "metric_code", name="uq_financial_metric_period_code"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    financial_period_id: Mapped[int] = mapped_column(
        ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False
    )
    metric_code: Mapped[MetricCode] = mapped_column(
        Enum(MetricCode, name="financial_metric_code", create_type=False),
        nullable=False,
    )
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[FinancialUnit] = mapped_column(
        Enum(FinancialUnit, name="financial_unit", create_type=False), nullable=False
    )
    input_fact_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False)
    comparison_period_id: Mapped[int | None] = mapped_column(
        ForeignKey("financial_periods.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    period: Mapped[FinancialPeriod] = relationship(
        back_populates="metrics", foreign_keys=[financial_period_id]
    )


class FinancialProcessingRun(Base):
    __tablename__ = "financial_processing_runs"
    __table_args__ = (
        UniqueConstraint(
            "source_event_id",
            "source_document_id",
            name="uq_financial_processing_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_event_id: Mapped[int] = mapped_column(
        ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False
    )
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[FinancialStatus] = mapped_column(
        Enum(FinancialStatus, name="financial_status", create_type=False),
        nullable=False,
    )
    status_note: Mapped[str | None] = mapped_column(Text)
    last_attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
