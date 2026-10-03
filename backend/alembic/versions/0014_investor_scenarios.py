"""Persist explicit valuation assumptions and analyst validation diagnostics."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_investor_scenarios"
down_revision = "0013_commentary_documents"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ai_analyses",
        sa.Column("validation_diagnostics", postgresql.JSONB(), nullable=True),
    )
    op.create_table(
        "valuation_assumptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("scenarios", sa.JSON(), nullable=False),
        sa.Column("near_range_pct", sa.Numeric(8, 3), nullable=False),
        sa.Column("extreme_gap_pct", sa.Numeric(8, 3), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "company_id", "scope", name="uq_valuation_assumptions_scope"
        ),
    )


def downgrade():
    op.drop_table("valuation_assumptions")
    op.drop_column("ai_analyses", "validation_diagnostics")
