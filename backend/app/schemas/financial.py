from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models.financial import (FactMethod, FactStatus, FinancialScope, FinancialStatus,
                                  FinancialUnit, MetricCode, OriginalUnit, PeriodType)


class FinancialFactRead(BaseModel):
    id: int
    metric_code: MetricCode
    value: Decimal
    unit: FinancialUnit
    original_value: Decimal | None
    original_unit: OriginalUnit | None
    source_document_id: int
    source_page: int | None
    source_label: str
    source_column_date: date
    extraction_method: FactMethod
    status: FactStatus
    document_url: str


class FinancialMetricRead(BaseModel):
    metric_code: MetricCode
    value: Decimal
    unit: FinancialUnit
    input_fact_ids: list[int]
    comparison_period_id: int | None
    formula: str
    inputs: list[FinancialFactRead]


class FinancialPeriodRead(BaseModel):
    id: int
    company_id: int
    fiscal_year: str
    quarter: int | None
    period_start: date
    period_end: date
    period_type: PeriodType
    scope: FinancialScope
    status: FinancialStatus
    status_note: str | None
    source_event_id: int
    source_document_id: int
    source_title: str
    source_name: str
    source_url: str
    document_url: str
    published_at: datetime
    facts: list[FinancialFactRead]
    metrics: list[FinancialMetricRead]


class CompanyFinancialsRead(BaseModel):
    symbol: str
    company_name: str
    periods: list[FinancialPeriodRead]


class FinancialProcessingRead(BaseModel):
    event_id: int
    document_id: int
    status: FinancialStatus
    periods_created: int
    periods: list[dict]
    note: str | None = None


FORMULAS: dict[MetricCode, str] = {
    MetricCode.EBITDA: "PBT + finance costs + depreciation and amortisation",
    MetricCode.EBITDA_MARGIN: "EBITDA / revenue from operations × 100",
    MetricCode.PAT_MARGIN: "PAT / revenue from operations × 100",
    MetricCode.REVENUE_YOY: "(revenue − same quarter last year) / abs(last year revenue) × 100",
    MetricCode.REVENUE_QOQ: "(revenue − previous quarter revenue) / abs(previous quarter revenue) × 100",
    MetricCode.PAT_YOY: "(PAT − same quarter last year PAT) / abs(last year PAT) × 100",
    MetricCode.PAT_QOQ: "(PAT − previous quarter PAT) / abs(previous quarter PAT) × 100",
    MetricCode.EBITDA_MARGIN_CHANGE_BPS: "(current EBITDA margin − last year EBITDA margin) × 100",
}
