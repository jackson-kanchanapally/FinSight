"""Typed forecast accuracy API, including explicit missing-data states."""
from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel
from app.schemas.expectations import Scope


class SampleMetrics(BaseModel):
    n: int
    missing_n: int
    mae: str | None
    rmse: str | None
    bias: str | None
    wape_pct: str | None
    mape_pct: str | None
    percentage_n: int
    interval_n: int
    interval_coverage: str | None
    mean_interval_width: str | None
    directional_n: int
    directional_accuracy: str | None


class Bounds(BaseModel):
    lower: str
    upper: str


class CalibrationBucket(BaseModel):
    kind: Literal["MET_PROBABILITY", "CONFIDENCE_LABEL", "PREDICTION_INTERVAL"]
    bucket: str
    n: int
    observed: str
    expected: str | None
    brier: str | None
    mean_interval_width: str | None
    uncertainty_95: Bounds | None
    status: Literal["INSUFFICIENT_DATA", "SUFFICIENT_DATA"]
    evaluation_ids: list[int]


class RollingMetrics(SampleMetrics):
    through_period: date
    evaluation_ids: list[int]
    calibration: list[CalibrationBucket]


class ErrorTrend(BaseModel):
    revision_id: int
    evaluation_id: int
    period: date
    published_at: datetime
    signed_error: str | None
    absolute_error: str | None
    directional_correctness: bool | None


class AccuracyGroup(SampleMetrics):
    metric: str
    engine_version: str
    model: str
    horizon_quarters: int | None
    period_type: str
    scope: Scope
    unit: str
    currency: str
    reporting_basis: str
    error_unit: str
    status: str
    interval_interpretation: str
    calibration: list[CalibrationBucket]
    rolling_last_20: list[RollingMetrics]
    evaluation_ids: list[int]
    error_trend: list[ErrorTrend]


class AccuracyOverview(BaseModel):
    total: int
    evaluated: int
    eligible_sample: int
    pending: int
    missing_actual: int
    excluded: int
    status: str


class ExclusionCount(BaseModel):
    reason_code: str
    n: int


class ForecastAccuracyRead(BaseModel):
    minimum_n: int
    scoring_version: str
    sample: str
    methodology: str
    calibration_note: str
    overview: AccuracyOverview
    exclusions: list[ExclusionCount]
    groups: list[AccuracyGroup]
    total: int
    offset: int
    limit: int


class SubsequentEvaluation(BaseModel):
    evaluation_id: int
    evaluated_at: datetime
    outcome: str
    actual: str | None
    absolute_error: str | None
    actual_published_at: datetime | None
    evidence: dict
    reason: str | None


class ForecastComparison(BaseModel):
    expectation_id: int
    revision_id: int
    metric: str
    metric_definition: str
    scope: Scope
    period_type: str
    period_start: date | None
    period_end: date
    fiscal_year: str | None
    reporting_basis: str
    unit: str
    currency: str
    predicted: str | None
    original_predicted: str | None
    original_unit: str
    actual: str | None
    created_at: datetime
    cutoff_at: datetime
    information_cutoff: datetime
    horizon_quarters: int | None
    horizon_days: int
    engine_version: str
    model: str
    status: Literal["EVALUATED", "PENDING", "MISSING_ACTUAL", "EXCLUDED"]
    reason_code: str
    reason: str
    evaluation_id: int | None
    evaluated_at: datetime | None
    actual_published_at: datetime | None
    signed_error: str | None
    absolute_error: str | None
    absolute_percentage_error: str | None
    directional_correctness: bool | None
    evidence: dict
    forecast_provenance: dict
    calculation_version: str
    latest_evaluation_id: int | None
    later_evaluations: list[SubsequentEvaluation]
    low: str | None
    high: str | None
    interval_probability: str | None
    probability: str | None
    confidence_label: str | None
    met: bool


class ComparisonPage(BaseModel):
    items: list[ForecastComparison]
    total: int
    offset: int
    limit: int


class ScoringJobRead(BaseModel):
    job_id: int
    status: str
