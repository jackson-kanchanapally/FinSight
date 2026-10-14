"""Explicit forecast definitions: probability always means the frozen MET event."""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.models.financial import MetricCode, PeriodType

Number = Annotated[Decimal, Field(allow_inf_nan=False, max_digits=24, decimal_places=6)]
Scope = Literal["CONSOLIDATED", "STANDALONE"]
Source = Literal["USER", "ANALYST", "MANAGEMENT", "AI"]
Outcome = Literal["MET", "PARTIALLY_MET", "MISSED", "NOT_COMPARABLE", "INSUFFICIENT_EVIDENCE"]


class ExpectationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    metric: MetricCode | Literal["BUSINESS_MILESTONE", "THESIS_CLAIM"]
    financial_scope: Scope = "CONSOLIDATED"
    period: date
    period_type: PeriodType = PeriodType.QUARTERLY
    reporting_basis: Literal["REPORTED", "ADJUSTED"] = "REPORTED"
    expectation_type: Literal["EXACT", "RANGE", "DIRECTIONAL", "QUALITATIVE"] = "EXACT"
    expected_value: Number | None = None
    lower_value: Number | None = None
    upper_value: Number | None = None
    direction: Literal["UP", "DOWN", "UNCHANGED"] | None = None
    baseline_evidence_id: str | None = Field(default=None, pattern=r"^F[1-9][0-9]*$")
    unit: Literal["INR", "INR_LAKH", "INR_MILLION", "INR_CRORE", "INR_PER_SHARE", "PERCENT", "PERCENTAGE_POINTS", "BASIS_POINTS", "EVENT"]
    currency: Literal["INR", "NONE"] = "INR"
    description: str = Field(min_length=3, max_length=4000)
    cutoff_at: datetime
    source_type: Source = "USER"
    source_reference: str = Field(min_length=1, max_length=2000)
    evidence_id: str | None = Field(default=None, pattern=r"^[FS][1-9][0-9]*$")
    historical_import: bool = False
    issued_at: datetime | None = None
    confidence: Number | None = Field(default=None, ge=0, le=1)
    confidence_event: Literal["MET_UNDER_FROZEN_TOLERANCE"] | None = None
    confidence_label: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    guidance_kind: Literal["FIRM_TARGET", "ASPIRATION", "ESTIMATE", "CONDITIONAL"] | None = None
    guidance_id: int | None = Field(default=None, gt=0)
    checkpoint_id: int | None = Field(default=None, gt=0)
    thesis_revision_id: int | None = Field(default=None, gt=0)
    lifecycle: Literal["ACTIVE", "WITHDRAWN"] = "ACTIVE"
    expected_version: int | None = Field(default=None, ge=1)
    reason: str = Field(default="Original expectation", min_length=3, max_length=1000)

    @model_validator(mode="after")
    def validate_definition(self):
        for field in ("cutoff_at", "issued_at"):
            value = getattr(self, field)
            if value:
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError(f"{field} requires an explicit timezone")
                setattr(self, field, value.astimezone(timezone.utc))
        if (self.confidence is None) != (self.confidence_event is None):
            raise ValueError("Probability requires the explicitly defined confidence event")
        if self.expectation_type != "QUALITATIVE":
            from app.services.financials.period import identify_period
            identify_period(self.period, self.period_type)
        if self.expectation_type == "EXACT" and self.expected_value is None:
            raise ValueError("Exact expectations require expected_value")
        if self.expectation_type == "RANGE" and (self.lower_value is None or self.upper_value is None or self.lower_value > self.upper_value):
            raise ValueError("Range requires ordered lower_value and upper_value")
        if self.expectation_type == "DIRECTIONAL" and (not self.direction or not self.baseline_evidence_id):
            raise ValueError("Direction requires a verified pre-cutoff baseline")
        if self.expectation_type == "QUALITATIVE":
            if self.unit != "EVENT" or self.metric not in {"BUSINESS_MILESTONE", "THESIS_CLAIM"}:
                raise ValueError("Qualitative claims use EVENT units and a qualitative metric")
        elif self.metric in {"BUSINESS_MILESTONE", "THESIS_CLAIM"} or self.unit == "EVENT":
            raise ValueError("Numerical expectations require a financial metric and unit")
        if self.source_type == "MANAGEMENT" and (not self.evidence_id or not self.evidence_id.startswith("S") or not self.guidance_kind):
            raise ValueError("Management guidance requires an official statement and guidance kind")
        if self.source_type == "AI" and not self.evidence_id:
            raise ValueError("AI expectations require source evidence")
        if self.lifecycle == "WITHDRAWN" and not self.expected_version:
            raise ValueError("Withdrawal must append a revision")
        if self.issued_at and not self.historical_import:
            raise ValueError("A claimed historical issue date requires historical_import")
        return self


class EvaluationRead(BaseModel):
    id: int
    revision_id: int
    financial_period_id: int | None
    outcome: Outcome
    prospective_eligible: bool
    result: dict
    created_at: datetime


class ExpectationRead(BaseModel):
    id: int
    company_id: int
    revision_id: int
    version: int
    created_at: datetime
    cutoff_at: datetime
    definition: dict
    provenance: dict
    status: Literal["PENDING", "MATCHED", "EVALUATED", "UNVERIFIABLE", "SUPERSEDED"]
    evaluation: EvaluationRead | None


class ExpectationPage(BaseModel):
    items: list[ExpectationRead]
    total: int
    offset: int
    limit: int


class StatisticsRead(BaseModel):
    total: int
    evaluated: int
    met: int
    missed: int
    partially_met: int
    unresolved: int
    evidence_coverage: dict
    groups: list[dict]
    calibration: list[dict]
    policy: dict


class CalibrationRead(BaseModel):
    items: list[dict]
    minimum_n: int


class ThesisHistoryRead(BaseModel):
    items: list[dict]
    available_theses: list[dict]
    total: int
    offset: int
    limit: int
    pagination_unit: str


class RetryRead(BaseModel):
    processed: int
