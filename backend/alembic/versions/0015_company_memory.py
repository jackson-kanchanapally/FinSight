"""Company document semantic-index manifests; structured facts stay in their tables."""
from alembic import op
import sqlalchemy as sa

revision = "0015_company_memory"
down_revision = "0014_investor_scenarios"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_memory_indexes",
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.String(64)),
        sa.Column("collection", sa.String(120), nullable=False),
        sa.Column("embedding_model", sa.String(120), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("last_error", sa.String(200)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("document_memory_indexes")
