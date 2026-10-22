"""Queue Telegram event alerts without backfilling existing events."""
from alembic import op
import sqlalchemy as sa

revision = "0023_telegram_deliveries"
down_revision = "0022_forecast_accuracy"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "telegram_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("chat_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("message", sa.Text()),
        sa.Column("analysis_id", sa.Integer(), sa.ForeignKey("event_analyses.id", ondelete="SET NULL")),
        sa.Column("ai_status", sa.String(30)),
        sa.Column("message_id", sa.Integer()),
        sa.Column("last_error", sa.String(200)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_telegram_deliveries_status", "telegram_deliveries", ["status"])


def downgrade():
    op.drop_table("telegram_deliveries")
