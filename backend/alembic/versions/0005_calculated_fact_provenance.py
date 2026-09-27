"""Allow calculated facts to have no original PDF value or unit."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_calculated_fact_provenance"
down_revision = "0004_financial_reporting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("financial_facts", "original_value", existing_type=sa.Numeric(24, 6),
                    nullable=True)
    op.alter_column("financial_facts", "original_unit",
                    existing_type=postgresql.ENUM(name="financial_original_unit"),
                    nullable=True)
    op.execute("UPDATE financial_facts SET original_value = NULL, original_unit = NULL "
               "WHERE extraction_method = 'CALCULATED'")


def downgrade() -> None:
    missing = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM financial_facts WHERE original_value IS NULL OR original_unit IS NULL"
    )).scalar_one()
    if missing:
        raise RuntimeError("Cannot downgrade while calculated facts have no original PDF value")
    op.alter_column("financial_facts", "original_unit",
                    existing_type=postgresql.ENUM(name="financial_original_unit"),
                    nullable=False)
    op.alter_column("financial_facts", "original_value", existing_type=sa.Numeric(24, 6),
                    nullable=False)
