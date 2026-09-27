"""Store source-linked short event analyses."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_event_analyses"
down_revision = "0006_local_ai_analyst"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("event_analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_provider", sa.String(40), nullable=False),
        sa.Column("model_name", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(60), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("evidence_registry", postgresql.JSONB(), nullable=False),
        sa.Column("structured_output", postgresql.JSONB()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("failure_reason", sa.String(80)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_event_analyses_event_created", "event_analyses", ["event_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_event_analyses_event_created", table_name="event_analyses")
    op.drop_table("event_analyses")
