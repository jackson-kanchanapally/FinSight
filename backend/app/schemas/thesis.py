from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Category = Literal["GROWTH", "PROFITABILITY", "BUSINESS_QUALITY", "COMPETITIVE_POSITION",
                   "EXECUTION", "MANAGEMENT", "CAPITAL_ALLOCATION", "BALANCE_SHEET",
                   "CASH_GENERATION", "VALUATION", "RISKS"]
State = Literal["UNPROVEN", "STRENGTHENING", "ON_TRACK", "WEAKENING", "BROKEN",
                "NOT_YET_TESTABLE", "INSUFFICIENT_EVIDENCE"]
Origin = Literal["USER", "SYSTEM_INFERRED", "MANAGEMENT_DERIVED", "RESEARCH_DERIVED"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExpectationInput(Strict):
    description: str = Field(min_length=8, max_length=1200)
    expectation_type: Literal["CHECKPOINT", "METRIC", "QUALITATIVE"] = "QUALITATIVE"
    checkpoint_id: int | None = None
    metric_code: str | None = None
    operator: Literal["GTE", "LTE", "EQ", "BETWEEN"] | None = None
    target_value: Decimal | None = None
    upper_value: Decimal | None = None
    unit: str | None = None
    target_period_end: date | None = None
    period_type: Literal["QUARTERLY", "ANNUAL"] | None = None
    is_active: bool = True
    reason: str = Field(default="Investor thesis test", min_length=3, max_length=500)
    expected_version: int | None = None

    @model_validator(mode="after")
    def complete_test(self):
        if self.expectation_type == "CHECKPOINT":
            if self.checkpoint_id is None:
                raise ValueError("CHECKPOINT_REQUIRED")
            if any(v is not None for v in (self.metric_code, self.target_value, self.upper_value,
                    self.operator, self.unit, self.target_period_end, self.period_type)):
                raise ValueError("LINK_CHECKPOINT_WITHOUT_COPYING_ITS_TARGET")
        elif self.checkpoint_id is not None:
            raise ValueError("CHECKPOINT_TYPE_REQUIRED")
        if self.expectation_type == "METRIC":
            if any(v is None for v in (self.metric_code, self.operator, self.target_value,
                                      self.unit, self.target_period_end, self.period_type)):
                raise ValueError("EXACT_METRIC_UNIT_AND_PERIOD_REQUIRED")
            if not self.target_value.is_finite() or self.upper_value is not None and not self.upper_value.is_finite():
                raise ValueError("FINITE_TARGET_REQUIRED")
            if self.operator == "BETWEEN" and (self.upper_value is None or self.upper_value < self.target_value):
                raise ValueError("VALID_RANGE_REQUIRED")
        return self


class PillarInput(Strict):
    scope: Literal["CONSOLIDATED", "STANDALONE"] = "CONSOLIDATED"
    name: str = Field(min_length=3, max_length=120)
    category: Category
    description: str = Field(min_length=10, max_length=2000)
    importance: Literal["CORE", "IMPORTANT", "SECONDARY"] = "IMPORTANT"
    created_from: Origin = "USER"
    source_analysis_id: int | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)
    metric_codes: list[str] = Field(default_factory=list, max_length=12)
    is_active: bool = True
    reason: str = Field(default="Initial investment assumption", min_length=3, max_length=500)
    expected_version: int | None = None
    expectations: list[ExpectationInput] = Field(default_factory=list, max_length=12)


class EvaluateRequest(Strict):
    analysis_id: int
    use_ai: bool = True


class BootstrapRequest(Strict):
    analysis_id: int


class PillarAssessment(Strict):
    pillar_id: int
    proposed_status: State
    summary: str = Field(min_length=10, max_length=800)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    basis: Literal["VERIFIED_FACT", "MANAGEMENT_CLAIM", "INTERPRETATION"]
    source_quote: str | None = Field(default=None, max_length=600)
    causal_basis: Literal["ESTABLISHED_CAUSE", "MANAGEMENT_EXPLANATION",
                         "INVESTOR_HYPOTHESIS", "UNKNOWN_CAUSE"] | None = None
    materiality: Literal["MATERIAL", "NOT_MATERIAL"]
    contradiction: bool = False


class ThesisAssessments(Strict):
    evaluations: list[PillarAssessment] = Field(max_length=30)
