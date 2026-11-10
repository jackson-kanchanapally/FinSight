"""NSE corporate actions, sync windows, and event price reactions."""
from alembic import op
import sqlalchemy as sa

revision = "0029_market_events"
down_revision = "0028_market_briefs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "corporate_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("action_type", sa.String(20), nullable=False),
        sa.Column("purpose", sa.String(500), nullable=False),
        sa.Column("adjustment_factor", sa.Numeric(18, 10)),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", "ex_date", "action_type", "purpose", name="uq_corporate_action"),
    )
    op.create_index("ix_corporate_actions_company_ex_date", "corporate_actions", ["company_id", "ex_date"])
    op.create_table(
        "corporate_action_syncs",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("from_date", sa.Date()),
        sa.Column("to_date", sa.Date()),
        sa.Column("synced_at", sa.DateTime(timezone=True)),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(200)),
    )
    op.create_table(
        "event_price_reactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("company_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(200)),
        sa.Column("timing", sa.String(24)),
        sa.Column("base_date", sa.Date()),
        sa.Column("base_close", sa.Numeric(18, 4)),
        sa.Column("reaction_date", sa.Date()),
        sa.Column("benchmark", sa.String(40), nullable=False),
        sa.Column("windows", sa.JSON(), nullable=False),
        sa.Column("volume_ratio", sa.Numeric(12, 4)),
        sa.Column("adjustments", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_event_price_reaction_event"),
    )
    op.create_index("ix_event_price_reactions_company", "event_price_reactions", ["company_id"])


def downgrade():
    op.drop_index("ix_event_price_reactions_company", table_name="event_price_reactions")
    op.drop_table("event_price_reactions")
    op.drop_table("corporate_action_syncs")
    op.drop_index("ix_corporate_actions_company_ex_date", table_name="corporate_actions")
    op.drop_table("corporate_actions")
