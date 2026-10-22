"""Index append-only observation history; retain all forecasts and evaluations.

First-actual evidence and calibration inputs reuse existing immutable JSON
snapshots. No historical prediction or confidence is backfilled.
"""
from alembic import op

revision = "0022_forecast_accuracy"
down_revision = "0021_research_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_expectation_evaluation_history", "expectation_evaluations", ["revision_id", "id"])


def downgrade():
    op.drop_index("ix_expectation_evaluation_history", table_name="expectation_evaluations")
