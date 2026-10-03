"""Record event origin, publication precision, results metadata, and attempts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_live_source_metadata"
down_revision = "0002_event_ingestion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    origin = postgresql.ENUM(
        "LIVE", "FIXTURE", "MANUAL", name="data_origin", create_type=False
    )
    precision = postgresql.ENUM(
        "TIMESTAMP", "DATE", name="publication_precision", create_type=False
    )
    basis = postgresql.ENUM(
        "STANDALONE", "CONSOLIDATED", "BOTH", name="reporting_basis", create_type=False
    )
    origin.create(bind, checkfirst=True)
    precision.create(bind, checkfirst=True)
    basis.create(bind, checkfirst=True)

    op.add_column(
        "company_events",
        sa.Column("data_origin", origin, server_default="LIVE", nullable=False),
    )
    op.add_column(
        "company_events",
        sa.Column(
            "publication_precision",
            precision,
            server_default="TIMESTAMP",
            nullable=False,
        ),
    )
    op.add_column("company_events", sa.Column("period_end_date", sa.Date()))
    op.add_column("company_events", sa.Column("fiscal_year", sa.String(10)))
    op.add_column("company_events", sa.Column("quarter", sa.Integer()))
    op.add_column("company_events", sa.Column("reporting_basis", basis))
    op.add_column(
        "event_references",
        sa.Column("data_origin", origin, server_default="LIVE", nullable=False),
    )
    op.add_column(
        "monitoring_states",
        sa.Column("last_attempted_check", sa.DateTime(timezone=True)),
    )
    op.execute(
        "UPDATE company_events SET data_origin = 'FIXTURE' WHERE source = 'FIXTURE'"
    )
    op.execute(
        "UPDATE event_references SET data_origin = 'FIXTURE' WHERE source = 'FIXTURE'"
    )


def downgrade() -> None:
    op.drop_column("monitoring_states", "last_attempted_check")
    op.drop_column("event_references", "data_origin")
    op.drop_column("company_events", "reporting_basis")
    op.drop_column("company_events", "quarter")
    op.drop_column("company_events", "fiscal_year")
    op.drop_column("company_events", "period_end_date")
    op.drop_column("company_events", "publication_precision")
    op.drop_column("company_events", "data_origin")
    bind = op.get_bind()
    postgresql.ENUM(name="reporting_basis").drop(bind, checkfirst=True)
    postgresql.ENUM(name="publication_precision").drop(bind, checkfirst=True)
    postgresql.ENUM(name="data_origin").drop(bind, checkfirst=True)
