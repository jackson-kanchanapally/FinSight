"""Persist local AI analyses without modifying financial records."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_local_ai_analyst"
down_revision = "0005_calculated_fact_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "financial_period_id",
            sa.Integer(),
            sa.ForeignKey("financial_periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("analysis_type", sa.String(40), nullable=False),
        sa.Column("model_provider", sa.String(40), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(60), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("context_chars", sa.Integer(), nullable=False),
        sa.Column("evidence_registry", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("structured_output", postgresql.JSONB()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure_reason", sa.String(80)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_ai_analyses_company_period",
        "ai_analyses",
        ["company_id", "financial_period_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_analyses_company_period", table_name="ai_analyses")
    op.drop_table("ai_analyses")
