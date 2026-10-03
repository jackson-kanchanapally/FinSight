"""Associate official commentary documents with analytical periods."""

from alembic import op
import sqlalchemy as sa

revision = "0013_commentary_documents"
down_revision = "0012_management_commentary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commentary_documents",
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
        sa.Column("fiscal_year", sa.String(10), nullable=False),
        sa.Column("quarter", sa.Integer()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("document_type", sa.String(40), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_priority", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "financial_period_id", "document_id", name="uq_commentary_document_period"
        ),
    )
    op.create_index(
        "ix_commentary_documents_period",
        "commentary_documents",
        ["financial_period_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_commentary_documents_period", table_name="commentary_documents")
    op.drop_table("commentary_documents")
