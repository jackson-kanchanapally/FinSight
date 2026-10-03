"""Initial companies and transactions tables."""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("exchange", sa.String(length=50), nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.Column("sector", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_companies_symbol", "companies", ["symbol"], unique=True)
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column(
            "transaction_type",
            sa.Enum("BUY", "SELL", name="transaction_type"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("charges", sa.Numeric(18, 4), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0", name="ck_transactions_quantity_positive"),
        sa.CheckConstraint("price > 0", name="ck_transactions_price_positive"),
        sa.CheckConstraint("charges >= 0", name="ck_transactions_charges_nonnegative"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_transactions_company_id", "transactions", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_transactions_company_id", table_name="transactions")
    op.drop_table("transactions")
    op.drop_index("ix_companies_symbol", table_name="companies")
    op.drop_table("companies")
