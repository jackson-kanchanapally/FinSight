"""Reuse immutable evaluations for monitoring independent of AI reviews."""
from alembic import op
import sqlalchemy as sa

revision = "0019_thesis_monitoring"
down_revision = "0018_living_thesis"
branch_labels = None
depends_on = None


def guards():
    if op.get_bind().dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(sa.text(f"CREATE TRIGGER IF NOT EXISTS thesis_evaluation_runs_no_{action.lower()} "
                f"BEFORE {action} ON thesis_evaluation_runs "
                "BEGIN SELECT RAISE(ABORT, 'THESIS_HISTORY_IS_IMMUTABLE'); END"))


def upgrade():
    with op.batch_alter_table("thesis_evaluation_runs") as batch:
        batch.alter_column("analysis_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("financial_period_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("previous_run_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("assessment", sa.JSON(), nullable=True))
        batch.create_foreign_key("fk_thesis_run_period", "financial_periods", ["financial_period_id"], ["id"], ondelete="RESTRICT")
        batch.create_foreign_key("fk_thesis_run_previous", "thesis_evaluation_runs", ["previous_run_id"], ["id"], ondelete="RESTRICT")
    guards()


def downgrade():
    # Refuse a lossy downgrade: autonomous assessments cannot acquire a fake review.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM thesis_evaluation_runs WHERE analysis_id IS NULL")):
        raise RuntimeError("Autonomous thesis history must be retained; downgrade is unavailable.")
    with op.batch_alter_table("thesis_evaluation_runs") as batch:
        batch.drop_constraint("fk_thesis_run_previous", type_="foreignkey")
        batch.drop_constraint("fk_thesis_run_period", type_="foreignkey")
        batch.drop_column("assessment")
        batch.drop_column("previous_run_id")
        batch.drop_column("financial_period_id")
        batch.alter_column("analysis_id", existing_type=sa.Integer(), nullable=False)
    guards()
