"""Restart-safe autonomous research jobs; reuse immutable expectation history."""
from alembic import op
import sqlalchemy as sa
revision = '0021_research_jobs'
down_revision = '0020_historical_expectations'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('research_jobs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('company_id', sa.Integer(), sa.ForeignKey('companies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(60), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_until', sa.DateTime(timezone=True)),
        sa.Column('lease_token', sa.String(40)),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('failures', sa.Integer(), nullable=False),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True)),
        sa.Column('last_success_at', sa.DateTime(timezone=True)),
        sa.Column('error', sa.Text()), sa.Column('result', sa.JSON(), nullable=False),
        sa.UniqueConstraint('company_id','kind',name='uq_research_job'))
    op.create_index('ix_research_jobs_due','research_jobs',['next_run_at','lease_until'])


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM research_jobs')):
        raise RuntimeError('Research job audit state must be retained; nonempty downgrade refused')
    op.drop_table('research_jobs')
