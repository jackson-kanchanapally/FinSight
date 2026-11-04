"""Three market reviews per NSE session with frozen forecasts and evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0028_market_briefs"
down_revision = "0027_gold_alerts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("market_brief_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("calendar", sa.JSON(), nullable=False),
        sa.Column("calendar_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(200)))
    op.create_table("market_briefs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("slot", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("morning_id", sa.Integer(), sa.ForeignKey("market_briefs.id")),
        sa.Column("previous_close_id", sa.Integer(), sa.ForeignKey("market_briefs.id")),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("assessment", sa.JSON(), nullable=False),
        sa.UniqueConstraint("session_date", "slot", name="uq_market_brief_session_slot"))
    op.create_index("ix_market_briefs_session_date", "market_briefs", ["session_date"])


def downgrade():
    op.drop_index("ix_market_briefs_session_date", table_name="market_briefs")
    op.drop_table("market_briefs")
    op.drop_table("market_brief_state")
