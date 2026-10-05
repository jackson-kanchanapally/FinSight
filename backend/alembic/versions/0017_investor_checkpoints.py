"""Investor commitments and period-specific execution history."""
from alembic import op
import sqlalchemy as sa
revision = "0017_investor_checkpoints"
down_revision = "0016_commentary_types"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("investor_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_period_id", sa.Integer(), sa.ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("statement_id", sa.Integer(), sa.ForeignKey("management_statements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("guidance_id", sa.Integer(), sa.ForeignKey("management_guidance.id", ondelete="SET NULL")),
        sa.Column("definition_key", sa.String(160), nullable=False),
        sa.Column("topic", sa.String(50), nullable=False),
        sa.Column("commitment", sa.Text(), nullable=False),
        sa.Column("target_period", sa.String(30)), sa.Column("target_date", sa.Date()),
        sa.Column("metric", sa.String(50)), sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("unit", sa.String(30)), sa.Column("measurement_note", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("statement_id", "definition_key", name="uq_checkpoint_statement_definition"))
    op.create_table("checkpoint_evaluations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("checkpoint_id", sa.Integer(), sa.ForeignKey("investor_checkpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_period_id", sa.Integer(), sa.ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("actual_outcome", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("checkpoint_id", "review_period_id", name="uq_checkpoint_review"))


def downgrade():
    op.drop_table("checkpoint_evaluations")
    op.drop_table("investor_checkpoints")
