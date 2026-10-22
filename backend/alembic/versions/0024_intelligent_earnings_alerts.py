"""Persist result discovery, provenance and resumable earnings delivery."""
from alembic import op
import sqlalchemy as sa

revision = "0024_intelligent_earnings_alerts"
down_revision = "0023_telegram_deliveries"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("financial_facts", sa.Column("rounding_tolerance", sa.Numeric(24, 9)))
    if op.get_bind().dialect.name == "postgresql":
        for value in ("EXCEPTIONAL_ITEMS", "PAT_ATTRIBUTABLE", "NON_CONTROLLING_INTEREST"):
            op.execute(f"ALTER TYPE financial_metric_code ADD VALUE IF NOT EXISTS '{value}'")
    op.create_table("result_discoveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("identity_key", sa.String(64), nullable=False, unique=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("company_events.id", ondelete="SET NULL")),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("document_url", sa.String(2048)),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.Date()), sa.Column("scope", sa.String(20)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_result_discoveries_company_id", "result_discoveries", ["company_id"])
    op.create_table("result_source_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("succeeded_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error", sa.String(200)), sa.Column("coverage", sa.Text(), nullable=False),
        sa.UniqueConstraint("company_id", "source", name="uq_result_source_company"))
    op.create_table("result_verifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("period_id", sa.Integer(), sa.ForeignKey("financial_periods.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fingerprint", sa.String(64), unique=True, nullable=False),
        sa.Column("status", sa.String(30), nullable=False), sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_result_verifications_period_id", "result_verifications", ["period_id"])
    for column in [sa.Column("idempotency_key", sa.String(64)),
                   sa.Column("version", sa.String(80), nullable=False, server_default="automatic-v2"),
                   sa.Column("financial_analysis_id", sa.Integer()),
                   sa.Column("financial_period_id", sa.Integer()),
                   sa.Column("notification", sa.JSON()), sa.Column("message_parts", sa.JSON()),
                   sa.Column("message_ids", sa.JSON()), sa.Column("sending_part", sa.Integer()),
                   sa.Column("parse_mode", sa.String(10))]:
        op.add_column("telegram_deliveries", column)
    op.create_index("ux_telegram_delivery_idempotency", "telegram_deliveries", ["idempotency_key"], unique=True)
    with op.batch_alter_table("telegram_deliveries") as batch:
        batch.create_foreign_key("fk_telegram_financial_analysis", "ai_analyses", ["financial_analysis_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_telegram_financial_period", "financial_periods", ["financial_period_id"], ["id"], ondelete="SET NULL")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("telegram_deliveries_event_id_key", "telegram_deliveries", type_="unique")
    else:
        with op.batch_alter_table("telegram_deliveries", naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch:
            batch.drop_constraint("uq_telegram_deliveries_event_id", type_="unique")
    with op.batch_alter_table("telegram_deliveries") as batch:
        batch.create_unique_constraint("uq_telegram_event_version", ["event_id", "chat_id", "version"])
    op.execute("UPDATE telegram_deliveries SET status='UNCERTAIN', last_error='Legacy send was in progress during upgrade; verify Telegram before a deliberate resend' WHERE status='PROCESSING'")


def downgrade():
    # Deliberate versioned sends may share an event; discarding their history is unsafe.
    count = op.get_bind().execute(sa.text("SELECT event_id FROM telegram_deliveries GROUP BY event_id HAVING count(*) > 1")).first()
    if count:
        raise RuntimeError("Cannot downgrade while versioned Telegram delivery history exists")
    with op.batch_alter_table("telegram_deliveries") as batch:
        batch.drop_constraint("uq_telegram_event_version", type_="unique")
        batch.create_unique_constraint("telegram_deliveries_event_id_key", ["event_id"])
        batch.drop_constraint("fk_telegram_financial_analysis", type_="foreignkey")
        batch.drop_constraint("fk_telegram_financial_period", type_="foreignkey")
    op.drop_index("ux_telegram_delivery_idempotency", table_name="telegram_deliveries")
    for name in ("parse_mode", "sending_part", "message_ids", "message_parts", "notification", "financial_analysis_id", "financial_period_id", "version", "idempotency_key"):
        op.drop_column("telegram_deliveries", name)
    op.drop_table("result_verifications")
    op.drop_table("result_source_checks")
    op.drop_table("result_discoveries")
    op.drop_column("financial_facts", "rounding_tolerance")
