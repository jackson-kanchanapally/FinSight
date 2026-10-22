"""Durable owner-only Telegram inbox and on-demand request history."""
from alembic import op
import sqlalchemy as sa

revision = "0025_telegram_queries"
down_revision = "0024_intelligent_earnings_alerts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("telegram_inbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_update_id", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)))
    op.create_table("telegram_queries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("update_id", sa.Integer(), nullable=False, unique=True),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("chat_id", sa.String(100), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE")),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("analyzed_at", sa.DateTime(timezone=True)),
        sa.Column("latest_data_at", sa.DateTime(timezone=True)),
        sa.Column("context_hash", sa.String(64)),
        sa.Column("model_route", sa.String(200)),
        sa.Column("evidence_registry", sa.JSON()),
        sa.Column("structured_output", sa.JSON()),
        sa.Column("source_references", sa.JSON()),
        sa.Column("message", sa.Text()),
        sa.Column("message_id", sa.Integer()),
        sa.Column("error", sa.String(100)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False))
    op.create_index("ix_telegram_queries_company_id", "telegram_queries", ["company_id"])
    op.create_index("ix_telegram_queries_status", "telegram_queries", ["status"])


def downgrade():
    op.drop_table("telegram_queries")
    op.drop_table("telegram_inbox")
