"""Classify official commentary without changing document provenance."""

from alembic import op

revision = "0016_commentary_types"
down_revision = "0015_company_memory"
branch_labels = None
depends_on = None


def upgrade():
    for label in ("EARNINGS_RELEASE", "CONCALL_TRANSCRIPT", "CONCALL_AUDIO"):
        op.execute(f"ALTER TYPE event_type ADD VALUE IF NOT EXISTS '{label}'")


def downgrade():
    raise RuntimeError(
        "event_type enum values may be in use; a data migration is required before downgrade"
    )
