"""Attributed management commentary and immutable guidance history."""

from alembic import op
import sqlalchemy as sa

revision = "0012_management_commentary"
down_revision = "0011_market_ohlcv_reference"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "management_statements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "financial_period_id",
            sa.Integer(),
            sa.ForeignKey("financial_periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("company_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("speaker_name", sa.String(120)),
        sa.Column("speaker_role", sa.String(120)),
        sa.Column("statement_type", sa.String(40), nullable=False),
        sa.Column("topic", sa.String(40), nullable=False),
        sa.Column("raw_statement", sa.Text(), nullable=False),
        sa.Column("normalized_summary", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("section_type", sa.String(30), nullable=False),
        sa.Column("extraction_confidence", sa.String(12), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "financial_period_id",
            "document_id",
            "page_number",
            "raw_statement",
            name="uq_management_statement_source",
        ),
    )
    op.create_index(
        "ix_management_statements_period",
        "management_statements",
        ["financial_period_id"],
    )
    op.create_table(
        "management_guidance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "statement_id",
            sa.Integer(),
            sa.ForeignKey("management_statements.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "financial_period_id",
            sa.Integer(),
            sa.ForeignKey("financial_periods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("target_period", sa.String(30), nullable=False),
        sa.Column("lower_bound", sa.Numeric(18, 4), nullable=False),
        sa.Column("upper_bound", sa.Numeric(18, 4), nullable=False),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_management_guidance_period", "management_guidance", ["financial_period_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_management_guidance_period", table_name="management_guidance")
    op.drop_table("management_guidance")
    op.drop_index("ix_management_statements_period", table_name="management_statements")
    op.drop_table("management_statements")
