"""Store raw NSE OHLCV and separately imported reference ratios."""

from alembic import op
import sqlalchemy as sa

revision = "0011_market_ohlcv_reference"
down_revision = "0010_market_valuation_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("open_price", "high_price", "low_price"):
        op.add_column("market_snapshots", sa.Column(name, sa.Numeric(18, 4)))
    op.add_column("market_snapshots", sa.Column("volume", sa.Numeric(24, 0)))
    op.create_table("reference_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("metric_code", sa.String(40), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("company_id", "metric_code", "provider", "as_of_date",
                            name="uq_reference_metric_date"))


def downgrade() -> None:
    op.drop_table("reference_metrics")
    for name in ("volume", "low_price", "high_price", "open_price"):
        op.drop_column("market_snapshots", name)
