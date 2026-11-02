"""Gold monitoring and dated news, independent of portfolio companies."""
from alembic import op
import sqlalchemy as sa

revision = "0027_gold_alerts"
down_revision = "0026_mainboard_ipos"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("gold_monitor_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("move_anchor", sa.Numeric(20, 6)),
        sa.Column("move_anchor_at", sa.DateTime(timezone=True)),
        sa.Column("last_move_alert_at", sa.DateTime(timezone=True)))
    op.create_table("gold_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prices", sa.JSON(), nullable=False),
        sa.Column("analysis", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False))
    op.create_index("ix_gold_snapshots_fetched_at", "gold_snapshots", ["fetched_at"])
    op.create_table("gold_headlines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("identity", sa.String(64), nullable=False, unique=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("impact", sa.String(20), nullable=False),
        sa.Column("impact_reason", sa.String(500), nullable=False),
        sa.Column("important", sa.Boolean(), nullable=False),
        sa.Column("alerted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_gold_headlines_published_at", "gold_headlines", ["published_at"])


def downgrade():
    op.drop_index("ix_gold_headlines_published_at", table_name="gold_headlines")
    op.drop_table("gold_headlines")
    op.drop_index("ix_gold_snapshots_fetched_at", table_name="gold_snapshots")
    op.drop_table("gold_snapshots")
    op.drop_table("gold_monitor_state")
