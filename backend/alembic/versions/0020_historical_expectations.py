"""Historical expectations, immutable revisions and reproducible evaluations."""
from alembic import op
import sqlalchemy as sa

revision = "0020_historical_expectations"
down_revision = "0019_thesis_monitoring"
branch_labels = None
depends_on = None
TABLES = ("historical_expectations", "historical_expectation_revisions", "expectation_evaluations")


def ref(name, table, nullable=False):
    return sa.Column(name, sa.Integer(), sa.ForeignKey(table + ".id", ondelete="RESTRICT"), nullable=nullable)


def col(name, kind, nullable=False):
    return sa.Column(name, kind, nullable=nullable)


def create(name, *columns):
    op.create_table(name, sa.Column("id", sa.Integer(), primary_key=True), *columns,
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def upgrade():
    create(TABLES[0], ref("company_id", "companies"), col("identity_key", sa.String(64)),
        col("metric", sa.String(60)), col("scope", sa.String(20)), col("period_end", sa.Date()),
        col("period_type", sa.String(20)), col("source_type", sa.String(20)),
        sa.UniqueConstraint("company_id", "identity_key", name="uq_historical_expectation_identity"))
    op.create_index("ix_historical_expectation_match", TABLES[0], ["company_id", "scope", "period_end", "metric"])
    create(TABLES[1], ref("expectation_id", TABLES[0]), col("version", sa.Integer()),
        col("cutoff_at", sa.DateTime(timezone=True)), col("definition", sa.JSON()), col("provenance", sa.JSON()),
        ref("statement_id", "management_statements", True), ref("guidance_id", "management_guidance", True),
        ref("checkpoint_id", "investor_checkpoints", True), ref("thesis_revision_id", "thesis_pillar_revisions", True),
        ref("document_id", "documents", True),
        sa.UniqueConstraint("expectation_id", "version", name="uq_historical_expectation_version"))
    create(TABLES[2], ref("revision_id", TABLES[1]), ref("financial_period_id", "financial_periods", True),
        col("fingerprint", sa.String(64)), col("outcome", sa.String(30)),
        col("prospective_eligible", sa.Boolean()), col("result", sa.JSON()),
        sa.UniqueConstraint("revision_id", "fingerprint", name="uq_expectation_evaluation_fingerprint"))
    for table, field in ((TABLES[1], "expectation_id"), (TABLES[1], "thesis_revision_id"), (TABLES[2], "revision_id")):
        op.create_index(f"ix_{table}_{field}", table, [field])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION reject_expectation_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'EXPECTATION_HISTORY_IS_IMMUTABLE'; END;
            $$ LANGUAGE plpgsql""")
        for table in TABLES:
            op.execute(sa.text(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
                              "FOR EACH ROW EXECUTE FUNCTION reject_expectation_mutation()"))
    elif op.get_bind().dialect.name == "sqlite":
        for table in TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(sa.text(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'EXPECTATION_HISTORY_IS_IMMUTABLE'); END"))


def downgrade():
    if any(op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {table}")) for table in TABLES):
        raise RuntimeError("Historical expectations must be retained; lossy downgrade refused")
    for table in reversed(TABLES):
        op.drop_table(table)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION reject_expectation_mutation()")
