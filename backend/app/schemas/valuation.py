from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.market import MarketSnapshotRead


class ValuationRead(BaseModel):
    id: int
    company_id: int
    market_snapshot_id: int
    financial_period_id: int | None
    scope: str
    market_date: date
    market: MarketSnapshotRead
    calculation_status: str
    metric_details: dict
    ttm_revenue: Decimal | None
    ttm_ebitda: Decimal | None
    ttm_pat: Decimal | None
    ttm_eps: Decimal | None
    pe_ttm: Decimal | None
    ps_ttm: Decimal | None
    ev_sales_ttm: Decimal | None
    ev_ebitda_ttm: Decimal | None
    earnings_yield_pct: Decimal | None
    price_to_book: Decimal | None
    fcf_yield_pct: Decimal | None
    peg_forward: Decimal | None
    enterprise_value_crore: Decimal | None
    updated_at: datetime


class ScenarioAssumptions(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    eps_growth_pct: Decimal = Field(gt=-100, le=1000)
    terminal_pe: Decimal = Field(gt=0, le=1000)
    years: int = Field(ge=1, le=10)


class ScenarioRequest(BaseModel):
    scope: Literal["CONSOLIDATED", "STANDALONE"] = "CONSOLIDATED"
    assumptions: list[ScenarioAssumptions] = Field(min_length=1, max_length=3)


class ScenarioResult(BaseModel):
    name: str
    eps_growth_pct: Decimal
    terminal_pe: Decimal
    years: int
    current_eps: Decimal
    current_price: Decimal
    future_eps: Decimal
    implied_future_price: Decimal
    total_return_pct: Decimal
    annualized_return_pct: Decimal
    label: str = "SCENARIO ANALYSIS — user assumptions, not a forecast"


class ReverseRequest(BaseModel):
    scope: Literal["CONSOLIDATED", "STANDALONE"] = "CONSOLIDATED"
    terminal_pe: Decimal = Field(gt=0, le=1000)
    years: int = Field(ge=1, le=10)
    target_annualized_return_pct: Decimal = Field(gt=-100, le=1000)


class ReverseResult(BaseModel):
    current_eps: Decimal
    current_price: Decimal
    terminal_pe: Decimal
    years: int
    target_annualized_return_pct: Decimal
    target_future_price: Decimal
    required_future_eps: Decimal
    required_eps_cagr_pct: Decimal
    label: str
    observed_eps_growth: dict | None = None
    growth_gap: dict | None = None
