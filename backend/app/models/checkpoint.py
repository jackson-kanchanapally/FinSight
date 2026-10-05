"""Source-backed commitments and evaluation snapshots by review period."""
from datetime import date, datetime
from sqlalchemy import Date, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class InvestorCheckpoint(Base):
    __tablename__ = "investor_checkpoints"
    __table_args__ = (UniqueConstraint("statement_id", "definition_key", name="uq_checkpoint_statement_definition"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    created_period_id: Mapped[int] = mapped_column(ForeignKey("financial_periods.id", ondelete="CASCADE"))
    statement_id: Mapped[int] = mapped_column(ForeignKey("management_statements.id", ondelete="CASCADE"))
    guidance_id: Mapped[int | None] = mapped_column(ForeignKey("management_guidance.id", ondelete="SET NULL"))
    definition_key: Mapped[str] = mapped_column(String(160))
    topic: Mapped[str] = mapped_column(String(50))
    commitment: Mapped[str] = mapped_column(Text)
    target_period: Mapped[str | None] = mapped_column(String(30))
    target_date: Mapped[date | None] = mapped_column(Date)
    metric: Mapped[str | None] = mapped_column(String(50))
    target: Mapped[dict] = mapped_column(JSON)
    unit: Mapped[str | None] = mapped_column(String(30))
    measurement_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CheckpointEvaluation(Base):
    __tablename__ = "checkpoint_evaluations"
    __table_args__ = (UniqueConstraint("checkpoint_id", "review_period_id", name="uq_checkpoint_review"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    checkpoint_id: Mapped[int] = mapped_column(ForeignKey("investor_checkpoints.id", ondelete="CASCADE"))
    review_period_id: Mapped[int] = mapped_column(ForeignKey("financial_periods.id", ondelete="CASCADE"))
    as_of_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(40))
    actual_outcome: Mapped[dict] = mapped_column(JSON)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
