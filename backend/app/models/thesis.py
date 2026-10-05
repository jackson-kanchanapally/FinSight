"""Append-only investment definitions and evidence snapshots."""
from datetime import date, datetime
from sqlalchemy import Date, DateTime, DDL, ForeignKey, JSON, String, Text, UniqueConstraint, event, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ThesisPillar(Base):
    __tablename__ = "thesis_pillars"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    scope: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThesisPillarRevision(Base):
    __tablename__ = "thesis_pillar_revisions"
    __table_args__ = (UniqueConstraint("pillar_id", "version", name="uq_thesis_pillar_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    pillar_id: Mapped[int] = mapped_column(ForeignKey("thesis_pillars.id", ondelete="RESTRICT"), index=True)
    version: Mapped[int]
    effective_date: Mapped[date] = mapped_column(Date)
    definition: Mapped[dict] = mapped_column(JSON)
    previous_status: Mapped[str | None] = mapped_column(String(30))
    initial_status: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThesisExpectationRevision(Base):
    __tablename__ = "thesis_expectation_revisions"
    __table_args__ = (UniqueConstraint("expectation_key", "version", name="uq_thesis_expectation_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    expectation_key: Mapped[str] = mapped_column(String(36), index=True)
    pillar_id: Mapped[int] = mapped_column(ForeignKey("thesis_pillars.id", ondelete="RESTRICT"), index=True)
    version: Mapped[int]
    effective_date: Mapped[date] = mapped_column(Date)
    checkpoint_id: Mapped[int | None] = mapped_column(ForeignKey("investor_checkpoints.id", ondelete="RESTRICT"))
    definition: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThesisEvaluationRun(Base):
    __tablename__ = "thesis_evaluation_runs"
    __table_args__ = (UniqueConstraint("company_id", "fingerprint", name="uq_thesis_run_fingerprint"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="RESTRICT"), index=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("ai_analyses.id", ondelete="RESTRICT"))
    scope: Mapped[str] = mapped_column(String(20))
    as_of_date: Mapped[date] = mapped_column(Date)
    evidence_as_of: Mapped[date] = mapped_column(Date)
    fingerprint: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(160))
    evidence_registry: Mapped[dict] = mapped_column(JSON)
    expectation_gap: Mapped[dict] = mapped_column(JSON)
    diagnostics: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThesisPillarEvaluation(Base):
    __tablename__ = "thesis_pillar_evaluations"
    __table_args__ = (UniqueConstraint("run_id", "pillar_id", name="uq_thesis_run_pillar"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("thesis_evaluation_runs.id", ondelete="RESTRICT"))
    pillar_id: Mapped[int] = mapped_column(ForeignKey("thesis_pillars.id", ondelete="RESTRICT"), index=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("thesis_pillar_revisions.id", ondelete="RESTRICT"))
    previous_status: Mapped[str] = mapped_column(String(30))
    new_status: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list] = mapped_column(JSON)
    expectations: Mapped[list] = mapped_column(JSON)
    contradiction: Mapped[bool]
    research_gaps: Mapped[list] = mapped_column(JSON)
    method: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


IMMUTABLE_MODELS = (ThesisPillar, ThesisPillarRevision, ThesisExpectationRevision,
                    ThesisEvaluationRun, ThesisPillarEvaluation)


def _immutable(*_args):
    raise ValueError("THESIS_HISTORY_IS_IMMUTABLE")


for model in IMMUTABLE_MODELS:
    event.listen(model, "before_update", _immutable)
    event.listen(model, "before_delete", _immutable)
    # Test/dev SQLite receives the same append-only enforcement as PostgreSQL.
    for operation in ("UPDATE", "DELETE"):
        event.listen(model.__table__, "after_create", DDL(
            f"CREATE TRIGGER {model.__tablename__}_no_{operation.lower()} BEFORE {operation} "
            f"ON {model.__tablename__} BEGIN SELECT RAISE(ABORT, 'THESIS_HISTORY_IS_IMMUTABLE'); END"
        ).execute_if(dialect="sqlite"))
