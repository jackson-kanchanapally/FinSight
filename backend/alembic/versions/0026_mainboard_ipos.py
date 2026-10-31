"""Independent IPO research and Telegram shortlist delivery."""
from alembic import op
import sqlalchemy as sa

revision = "0026_mainboard_ipos"
down_revision = "0025_telegram_queries"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ipo_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.String(100), nullable=False, unique=True),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("opens_on", sa.Date()), sa.Column("closes_on", sa.Date()),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False))
    op.create_index("ix_ipo_issues_opens_on", "ipo_issues", ["opens_on"])
    op.create_table("ipo_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("succeeded_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()), sa.Column("weekly_key", sa.String(20)))
    with op.batch_alter_table("telegram_deliveries") as batch:
        batch.alter_column("event_id", existing_type=sa.Integer(), nullable=True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT id FROM telegram_deliveries WHERE event_id IS NULL LIMIT 1")).first():
        raise RuntimeError("Cannot downgrade while IPO Telegram delivery history exists")
    with op.batch_alter_table("telegram_deliveries") as batch:
        batch.alter_column("event_id", existing_type=sa.Integer(), nullable=False)
    op.drop_table("ipo_checks")
    op.drop_index("ix_ipo_issues_opens_on", table_name="ipo_issues")
    op.drop_table("ipo_issues")
