"""Store a sourced forward EPS growth estimate for PEG context."""

from alembic import op
import sqlalchemy as sa

revision = "0009_forward_eps_growth"
down_revision = "0008_market_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_snapshots", sa.Column("forward_eps_growth_pct", sa.Numeric(10, 4))
    )
    op.add_column("market_snapshots", sa.Column("growth_source_url", sa.String(2048)))


def downgrade() -> None:
    op.drop_column("market_snapshots", "growth_source_url")
    op.drop_column("market_snapshots", "forward_eps_growth_pct")
