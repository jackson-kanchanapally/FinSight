"""Store dated user supplied price and valuation observations."""

from alembic import op
import sqlalchemy as sa

revision = "0008_market_snapshots"
down_revision = "0007_event_analyses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("market_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("close_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("pe_ratio", sa.Numeric(18, 4)),
        sa.Column("market_cap_crore", sa.Numeric(24, 4)),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def downgrade() -> None:
    op.drop_table("market_snapshots")
