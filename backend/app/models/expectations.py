"""Append-only forecasts and outcome snapshots; financial facts remain authoritative."""
from datetime import date, datetime
from sqlalchemy import Date, DateTime, DDL, ForeignKey, Index, JSON, String, UniqueConstraint, event, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class HistoricalExpectation(Base):
    __tablename__ = "historical_expectations"
    __table_args__ = (
        UniqueConstraint("company_id", "identity_key", name="uq_historical_expectation_identity"),
        Index("ix_historical_expectation_match", "company_id", "scope", "period_end", "metric"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"))
    identity_key: Mapped[str] = mapped_column(String(64))
    metric: Mapped[str] = mapped_column(String(60))
    scope: Mapped[str] = mapped_column(String(20))
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(20))
    source_type: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HistoricalExpectationRevision(Base):
    __tablename__ = "historical_expectation_revisions"
    __table_args__ = (UniqueConstraint("expectation_id", "version", name="uq_historical_expectation_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    expectation_id: Mapped[int] = mapped_column(ForeignKey("historical_expectations.id", ondelete="RESTRICT"), index=True)
    version: Mapped[int]
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition: Mapped[dict] = mapped_column(JSON)
    provenance: Mapped[dict] = mapped_column(JSON)
    statement_id: Mapped[int | None] = mapped_column(ForeignKey("management_statements.id", ondelete="RESTRICT"))
    guidance_id: Mapped[int | None] = mapped_column(ForeignKey("management_guidance.id", ondelete="RESTRICT"))
    checkpoint_id: Mapped[int | None] = mapped_column(ForeignKey("investor_checkpoints.id", ondelete="RESTRICT"))
    thesis_revision_id: Mapped[int | None] = mapped_column(ForeignKey("thesis_pillar_revisions.id", ondelete="RESTRICT"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExpectationEvaluation(Base):
    __tablename__ = "expectation_evaluations"
    __table_args__ = (
        UniqueConstraint("revision_id", "fingerprint", name="uq_expectation_evaluation_fingerprint"),
        Index("ix_expectation_evaluation_history", "revision_id", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("historical_expectation_revisions.id", ondelete="RESTRICT"), index=True)
    financial_period_id: Mapped[int | None] = mapped_column(ForeignKey("financial_periods.id", ondelete="RESTRICT"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(30))
    prospective_eligible: Mapped[bool]
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


HISTORY_MODELS = (HistoricalExpectation, HistoricalExpectationRevision, ExpectationEvaluation)


def immutable(*_args):
    raise ValueError("EXPECTATION_HISTORY_IS_IMMUTABLE")


for model in HISTORY_MODELS:
    event.listen(model, "before_update", immutable)
    event.listen(model, "before_delete", immutable)
    for action in ("UPDATE", "DELETE"):
        event.listen(model.__table__, "after_create", DDL(
            f"CREATE TRIGGER {model.__tablename__}_no_{action.lower()} BEFORE {action} "
            f"ON {model.__tablename__} BEGIN SELECT RAISE(ABORT, 'EXPECTATION_HISTORY_IS_IMMUTABLE'); END"
        ).execute_if(dialect="sqlite"))
