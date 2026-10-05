"""Immutable living investment thesis definitions and evaluations."""
from alembic import op
import sqlalchemy as sa

revision = "0018_living_thesis"
down_revision = "0017_investor_checkpoints"
branch_labels = None
depends_on = None
TABLES = ("thesis_pillars", "thesis_pillar_revisions", "thesis_expectation_revisions",
          "thesis_evaluation_runs", "thesis_pillar_evaluations")


def column(name, kind, nullable=False):
    return sa.Column(name, kind, nullable=nullable)


def reference(name, table, nullable=False):
    return sa.Column(name, sa.Integer(), sa.ForeignKey(table + ".id", ondelete="RESTRICT"), nullable=nullable)


def create(name, *fields):
    return op.create_table(name, sa.Column("id", sa.Integer(), primary_key=True), *fields,
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))


def upgrade():
    create("thesis_pillars", reference("company_id", "companies"), column("scope", sa.String(20)))
    create("thesis_pillar_revisions", reference("pillar_id", "thesis_pillars"),
        column("version", sa.Integer()), column("effective_date", sa.Date()), column("definition", sa.JSON()),
        column("previous_status", sa.String(30), True), column("initial_status", sa.String(30)),
        column("reason", sa.Text()), sa.UniqueConstraint("pillar_id", "version", name="uq_thesis_pillar_version"))
    create("thesis_expectation_revisions", column("expectation_key", sa.String(36)),
        reference("pillar_id", "thesis_pillars"), column("version", sa.Integer()),
        column("effective_date", sa.Date()), reference("checkpoint_id", "investor_checkpoints", True),
        column("definition", sa.JSON()), column("reason", sa.Text()),
        sa.UniqueConstraint("expectation_key", "version", name="uq_thesis_expectation_version"))
    create("thesis_evaluation_runs", reference("company_id", "companies"), reference("analysis_id", "ai_analyses"),
        column("scope", sa.String(20)), column("as_of_date", sa.Date()), column("evidence_as_of", sa.Date()),
        column("fingerprint", sa.String(64)), column("provider", sa.String(160)),
        column("evidence_registry", sa.JSON()), column("expectation_gap", sa.JSON()), column("diagnostics", sa.JSON()),
        sa.UniqueConstraint("company_id", "fingerprint", name="uq_thesis_run_fingerprint"))
    create("thesis_pillar_evaluations", reference("run_id", "thesis_evaluation_runs"),
        reference("pillar_id", "thesis_pillars"), reference("revision_id", "thesis_pillar_revisions"),
        column("previous_status", sa.String(30)), column("new_status", sa.String(30)), column("reason", sa.Text()),
        column("evidence_ids", sa.JSON()), column("expectations", sa.JSON()), column("contradiction", sa.Boolean()),
        column("research_gaps", sa.JSON()), column("method", sa.String(30)),
        sa.UniqueConstraint("run_id", "pillar_id", name="uq_thesis_run_pillar"))
    for table, fields in ((TABLES[0], ["company_id"]), (TABLES[1], ["pillar_id"]),
                          (TABLES[2], ["expectation_key", "pillar_id"]), (TABLES[3], ["company_id"]),
                          (TABLES[4], ["pillar_id"])):
        for field in fields:
            op.create_index(f"ix_{table}_{field}", table, [field])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION reject_thesis_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'THESIS_HISTORY_IS_IMMUTABLE'; END;
            $$ LANGUAGE plpgsql""")
        for table in TABLES:
            op.execute(sa.text(f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
                              "FOR EACH ROW EXECUTE FUNCTION reject_thesis_mutation()"))
    elif op.get_bind().dialect.name == "sqlite":
        for table in TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(sa.text(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
                                  "BEGIN SELECT RAISE(ABORT, 'THESIS_HISTORY_IS_IMMUTABLE'); END"))


def downgrade():
    for name in reversed(TABLES):
        op.drop_table(name)
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION reject_thesis_mutation()")
