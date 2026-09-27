"""financial reporting facts and metrics

Revision ID: 0004_financial_reporting
Revises: 0003_live_source_metadata
Create Date: 2026-09-27 21:37:09.776589
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '0004_financial_reporting'
down_revision: Union[str, None] = '0003_live_source_metadata'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TYPES = {
    "financial_period_type": ("QUARTERLY", "HALF_YEARLY", "NINE_MONTH", "ANNUAL"),
    "financial_scope": ("STANDALONE", "CONSOLIDATED"),
    "financial_status": ("PENDING", "EXTRACTED", "VALIDATED", "NEEDS_REVIEW", "FAILED"),
    "financial_metric_code": ("REVENUE_FROM_OPERATIONS", "TOTAL_INCOME", "OTHER_INCOME", "EBITDA",
        "ADJUSTED_EBITDA", "EBITDA_MARGIN", "EBIT", "PBT", "PAT", "PAT_MARGIN", "BASIC_EPS",
        "DILUTED_EPS", "TOTAL_EXPENSES", "FINANCE_COST", "DEPRECIATION", "EMPLOYEE_EXPENSE",
        "OTHER_EXPENSE", "TOTAL_TAX_EXPENSE", "REVENUE_YOY", "REVENUE_QOQ", "PAT_YOY",
        "PAT_QOQ", "EBITDA_MARGIN_CHANGE_BPS"),
    "financial_unit": ("INR_CRORE", "INR_PER_SHARE", "PERCENT", "BASIS_POINTS"),
    "financial_original_unit": ("INR", "INR_LAKH", "INR_MILLION", "INR_CRORE", "INR_PER_SHARE"),
    "financial_fact_method": ("PDF_TABLE", "STRUCTURED", "CALCULATED"),
    "financial_fact_status": ("VERIFIED_PDF", "VERIFIED_STRUCTURED", "NEEDS_REVIEW", "MANUALLY_VERIFIED"),
}


def upgrade() -> None:
    bind = op.get_bind()
    for name, values in TYPES.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)
    op.create_table('financial_periods',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('company_id', sa.Integer(), nullable=False),
    sa.Column('fiscal_year', sa.String(length=10), nullable=False),
    sa.Column('quarter', sa.Integer(), nullable=True),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('period_type', postgresql.ENUM('QUARTERLY', 'HALF_YEARLY', 'NINE_MONTH', 'ANNUAL', name='financial_period_type', create_type=False), nullable=False),
    sa.Column('scope', postgresql.ENUM('STANDALONE', 'CONSOLIDATED', name='financial_scope', create_type=False), nullable=False),
    sa.Column('status', postgresql.ENUM('PENDING', 'EXTRACTED', 'VALIDATED', 'NEEDS_REVIEW', 'FAILED', name='financial_status', create_type=False), nullable=False),
    sa.Column('status_note', sa.Text(), nullable=True),
    sa.Column('source_event_id', sa.Integer(), nullable=False),
    sa.Column('source_document_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_event_id'], ['company_events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('company_id', 'period_end', 'period_type', 'scope', name='uq_financial_period_identity')
    )
    op.create_index('ix_financial_periods_company_end', 'financial_periods', ['company_id', 'period_end'], unique=False)
    op.create_table('financial_processing_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_event_id', sa.Integer(), nullable=False),
    sa.Column('source_document_id', sa.Integer(), nullable=False),
    sa.Column('status', postgresql.ENUM('PENDING', 'EXTRACTED', 'VALIDATED', 'NEEDS_REVIEW', 'FAILED', name='financial_status', create_type=False), nullable=False),
    sa.Column('status_note', sa.Text(), nullable=True),
    sa.Column('last_attempted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['source_document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_event_id'], ['company_events.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_event_id', 'source_document_id', name='uq_financial_processing_source')
    )
    op.create_table('financial_facts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('financial_period_id', sa.Integer(), nullable=False),
    sa.Column('metric_code', postgresql.ENUM('REVENUE_FROM_OPERATIONS', 'TOTAL_INCOME', 'OTHER_INCOME', 'EBITDA', 'ADJUSTED_EBITDA', 'EBITDA_MARGIN', 'EBIT', 'PBT', 'PAT', 'PAT_MARGIN', 'BASIC_EPS', 'DILUTED_EPS', 'TOTAL_EXPENSES', 'FINANCE_COST', 'DEPRECIATION', 'EMPLOYEE_EXPENSE', 'OTHER_EXPENSE', 'TOTAL_TAX_EXPENSE', 'REVENUE_YOY', 'REVENUE_QOQ', 'PAT_YOY', 'PAT_QOQ', 'EBITDA_MARGIN_CHANGE_BPS', name='financial_metric_code', create_type=False), nullable=False),
    sa.Column('value', sa.Numeric(precision=24, scale=6), nullable=False),
    sa.Column('unit', postgresql.ENUM('INR_CRORE', 'INR_PER_SHARE', 'PERCENT', 'BASIS_POINTS', name='financial_unit', create_type=False), nullable=False),
    sa.Column('original_value', sa.Numeric(precision=24, scale=6), nullable=False),
    sa.Column('original_unit', postgresql.ENUM('INR', 'INR_LAKH', 'INR_MILLION', 'INR_CRORE', 'INR_PER_SHARE', name='financial_original_unit', create_type=False), nullable=False),
    sa.Column('source_document_id', sa.Integer(), nullable=False),
    sa.Column('source_page', sa.Integer(), nullable=True),
    sa.Column('source_label', sa.String(length=255), nullable=False),
    sa.Column('source_column_date', sa.Date(), nullable=False),
    sa.Column('extraction_method', postgresql.ENUM('PDF_TABLE', 'STRUCTURED', 'CALCULATED', name='financial_fact_method', create_type=False), nullable=False),
    sa.Column('status', postgresql.ENUM('VERIFIED_PDF', 'VERIFIED_STRUCTURED', 'NEEDS_REVIEW', 'MANUALLY_VERIFIED', name='financial_fact_status', create_type=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['financial_period_id'], ['financial_periods.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['source_document_id'], ['documents.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('financial_period_id', 'metric_code', name='uq_financial_fact_metric')
    )
    op.create_table('financial_metrics',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('financial_period_id', sa.Integer(), nullable=False),
    sa.Column('metric_code', postgresql.ENUM('REVENUE_FROM_OPERATIONS', 'TOTAL_INCOME', 'OTHER_INCOME', 'EBITDA', 'ADJUSTED_EBITDA', 'EBITDA_MARGIN', 'EBIT', 'PBT', 'PAT', 'PAT_MARGIN', 'BASIC_EPS', 'DILUTED_EPS', 'TOTAL_EXPENSES', 'FINANCE_COST', 'DEPRECIATION', 'EMPLOYEE_EXPENSE', 'OTHER_EXPENSE', 'TOTAL_TAX_EXPENSE', 'REVENUE_YOY', 'REVENUE_QOQ', 'PAT_YOY', 'PAT_QOQ', 'EBITDA_MARGIN_CHANGE_BPS', name='financial_metric_code', create_type=False), nullable=False),
    sa.Column('value', sa.Numeric(precision=24, scale=6), nullable=False),
    sa.Column('unit', postgresql.ENUM('INR_CRORE', 'INR_PER_SHARE', 'PERCENT', 'BASIS_POINTS', name='financial_unit', create_type=False), nullable=False),
    sa.Column('input_fact_ids', sa.JSON(), nullable=False),
    sa.Column('comparison_period_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['comparison_period_id'], ['financial_periods.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['financial_period_id'], ['financial_periods.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('financial_period_id', 'metric_code', name='uq_financial_metric_period_code')
    )
    op.create_table('financial_fact_corrections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fact_id', sa.Integer(), nullable=False),
    sa.Column('previous_value', sa.Numeric(precision=24, scale=6), nullable=False),
    sa.Column('corrected_value', sa.Numeric(precision=24, scale=6), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('corrected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['fact_id'], ['financial_facts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:

    op.drop_table('financial_fact_corrections')
    op.drop_table('financial_metrics')
    op.drop_table('financial_facts')
    op.drop_table('financial_processing_runs')
    op.drop_index('ix_financial_periods_company_end', table_name='financial_periods')
    op.drop_table('financial_periods')
    bind = op.get_bind()
    for name in reversed(tuple(TYPES)):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
