"""Event ingestion, provenance, documents, and monitoring state."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_event_ingestion"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

EVENT_TYPES = (
    "FINANCIAL_RESULTS",
    "BOARD_MEETING",
    "EARNINGS_CALL",
    "INVESTOR_PRESENTATION",
    "ANNUAL_REPORT",
    "ORDER_WIN",
    "ORDER_CANCELLATION",
    "PARTNERSHIP",
    "ACQUISITION",
    "DIVESTMENT",
    "CAPEX",
    "NEW_PRODUCT",
    "MANAGEMENT_CHANGE",
    "AUDITOR_CHANGE",
    "DIVIDEND",
    "BONUS",
    "STOCK_SPLIT",
    "BUYBACK",
    "FUND_RAISE",
    "CREDIT_RATING",
    "SHAREHOLDING",
    "PROMOTER_TRANSACTION",
    "PLEDGE",
    "REGULATORY",
    "LITIGATION",
    "COMPLIANCE",
    "OTHER",
)


def upgrade() -> None:
    event_type = postgresql.ENUM(*EVENT_TYPES, name="event_type", create_type=False)
    processing_status = postgresql.ENUM(
        "PENDING",
        "PROCESSED",
        "NO_TEXT",
        "FAILED",
        name="processing_status",
        create_type=False,
    )
    event_type.create(op.get_bind(), checkfirst=True)
    processing_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "company_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("document_url", sa.String(2048)),
        sa.Column("importance", sa.String(20), server_default="NORMAL", nullable=False),
        sa.Column("processing_status", processing_status, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_company_events_company_published",
        "company_events",
        ["company_id", "published_at"],
    )
    op.create_index(
        "ix_company_events_published_at", "company_events", ["published_at"]
    )
    op.create_index("ix_company_events_event_type", "company_events", ["event_type"])
    op.create_table(
        "event_references",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("company_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("document_url", sa.String(2048)),
        sa.Column("raw_payload", sa.JSON()),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_event_references_event_id", "event_references", ["event_id"])
    op.create_index(
        "ux_event_references_source_external_id",
        "event_references",
        ["source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("company_events.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("document_url", sa.String(2048), nullable=False),
        sa.Column("document_type", sa.String(30), nullable=False),
        sa.Column("mime_type", sa.String(100)),
        sa.Column("file_name", sa.String(255)),
        sa.Column("local_path", sa.String(1024)),
        sa.Column("sha256_hash", sa.String(64)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("downloaded_at", sa.DateTime(timezone=True)),
        sa.Column("processing_status", processing_status, nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_documents_company_id", "documents", ["company_id"])
    op.create_index("ix_documents_event_id", "documents", ["event_id"])
    op.create_index("ix_documents_sha256_hash", "documents", ["sha256_hash"])
    op.create_table(
        "document_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "document_id", "page_number", name="uq_document_pages_document_page"
        ),
    )
    op.create_table(
        "monitoring_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False, unique=True),
        sa.Column("last_successful_check", sa.DateTime(timezone=True)),
        sa.Column("last_external_id", sa.String(255)),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "company_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_symbol", sa.String(100)),
        sa.Column("source_company_id", sa.String(255)),
        sa.Column("ir_url", sa.String(2048)),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.UniqueConstraint(
            "company_id", "source", name="uq_company_sources_company_source"
        ),
    )


def downgrade() -> None:
    op.drop_table("company_sources")
    op.drop_table("monitoring_states")
    op.drop_table("document_pages")
    op.drop_index("ix_documents_sha256_hash", table_name="documents")
    op.drop_index("ix_documents_event_id", table_name="documents")
    op.drop_index("ix_documents_company_id", table_name="documents")
    op.drop_table("documents")
    op.drop_index(
        "ux_event_references_source_external_id", table_name="event_references"
    )
    op.drop_index("ix_event_references_event_id", table_name="event_references")
    op.drop_table("event_references")
    op.drop_index("ix_company_events_event_type", table_name="company_events")
    op.drop_index("ix_company_events_published_at", table_name="company_events")
    op.drop_index("ix_company_events_company_published", table_name="company_events")
    op.drop_table("company_events")
    postgresql.ENUM(name="processing_status").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="event_type").drop(op.get_bind(), checkfirst=True)
