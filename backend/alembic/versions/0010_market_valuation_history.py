"""Preserve dated market observations and calculated valuation history."""

from alembic import op
import sqlalchemy as sa

revision = "0010_market_valuation_history"
down_revision = "0009_forward_eps_growth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("bse_code", sa.String(6)))
    op.drop_constraint("market_snapshots_company_id_key", "market_snapshots", type_="unique")
    op.add_column("market_snapshots", sa.Column("provider", sa.String(40), server_default="MANUAL", nullable=False))
    op.add_column("market_snapshots", sa.Column("provider_symbol", sa.String(100)))
    op.add_column("market_snapshots", sa.Column("data_origin", sa.String(12), server_default="MANUAL", nullable=False))
    op.add_column("market_snapshots", sa.Column("observed_at", sa.DateTime(timezone=True)))
    op.add_column("market_snapshots", sa.Column("fetched_at", sa.DateTime(timezone=True)))
    op.add_column("market_snapshots", sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.add_column("market_snapshots", sa.Column("currency", sa.String(3), server_default="INR", nullable=False))
    op.add_column("market_snapshots", sa.Column("shares_outstanding", sa.Numeric(24, 0)))
    op.add_column("market_snapshots", sa.Column("week_52_high", sa.Numeric(18, 4)))
    op.add_column("market_snapshots", sa.Column("week_52_low", sa.Numeric(18, 4)))
    op.add_column("market_snapshots", sa.Column("growth_source_type", sa.String(40)))
    op.add_column("market_snapshots", sa.Column("note", sa.Text()))
    op.execute("UPDATE market_snapshots SET observed_at = updated_at, fetched_at = updated_at, "
               "created_at = updated_at, growth_source_type = CASE "
               "WHEN forward_eps_growth_pct IS NOT NULL THEN 'USER_ASSUMPTION' ELSE NULL END")
    op.alter_column("market_snapshots", "observed_at", nullable=False)
    op.alter_column("market_snapshots", "fetched_at", nullable=False)
    op.create_index("ix_market_snapshots_company_date", "market_snapshots", ["company_id", "as_of_date"])
    op.create_unique_constraint("uq_market_observation", "market_snapshots",
                                ["company_id", "as_of_date", "provider", "data_origin"])

    for value in ("OPERATING_CASH_FLOW", "CAPITAL_EXPENDITURE", "TOTAL_DEBT",
                  "CASH_AND_CASH_EQUIVALENTS", "TOTAL_EQUITY"):
        op.execute(f"ALTER TYPE financial_metric_code ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table("valuation_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("market_snapshot_id", sa.Integer(), sa.ForeignKey("market_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("financial_period_id", sa.Integer(), sa.ForeignKey("financial_periods.id", ondelete="SET NULL")),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("ttm_revenue", sa.Numeric(24, 6)),
        sa.Column("ttm_ebitda", sa.Numeric(24, 6)),
        sa.Column("ttm_pat", sa.Numeric(24, 6)),
        sa.Column("ttm_eps", sa.Numeric(24, 6)),
        sa.Column("pe_ttm", sa.Numeric(24, 6)),
        sa.Column("ps_ttm", sa.Numeric(24, 6)),
        sa.Column("ev_sales_ttm", sa.Numeric(24, 6)),
        sa.Column("ev_ebitda_ttm", sa.Numeric(24, 6)),
        sa.Column("earnings_yield_pct", sa.Numeric(24, 6)),
        sa.Column("price_to_book", sa.Numeric(24, 6)),
        sa.Column("fcf_yield_pct", sa.Numeric(24, 6)),
        sa.Column("peg_forward", sa.Numeric(24, 6)),
        sa.Column("enterprise_value_crore", sa.Numeric(24, 6)),
        sa.Column("calculation_status", sa.String(24), nullable=False),
        sa.Column("metric_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("market_snapshot_id", "scope", name="uq_valuation_snapshot_market_scope"))
    op.create_index("ix_valuation_snapshots_company_market", "valuation_snapshots",
                    ["company_id", "market_snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_valuation_snapshots_company_market", table_name="valuation_snapshots")
    op.drop_table("valuation_snapshots")
    op.drop_constraint("uq_market_observation", "market_snapshots", type_="unique")
    op.drop_index("ix_market_snapshots_company_date", table_name="market_snapshots")
    for column in ("note", "growth_source_type", "week_52_low", "week_52_high",
                   "shares_outstanding", "currency", "created_at", "fetched_at",
                   "observed_at", "data_origin", "provider_symbol", "provider"):
        op.drop_column("market_snapshots", column)
    op.create_unique_constraint("market_snapshots_company_id_key", "market_snapshots", ["company_id"])
    op.drop_column("companies", "bse_code")
