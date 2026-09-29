from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.analyst.prompts import PROMPT_VERSION


class StrictText(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def safe_text(cls, value):
        if isinstance(value, str) and ("<" in value or ">" in value or "http://" in value.lower()
                                       or "https://" in value.lower()):
            raise ValueError("HTML or links are not allowed in analyst text")
        return value


class Claim(StrictText):
    title: str = Field(min_length=1, max_length=180)
    explanation: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class MaterialChange(StrictText):
    metric: str = Field(min_length=1, max_length=100)
    change: str = Field(min_length=1, max_length=220)
    interpretation: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class AnalystNote(StrictText):
    text: str = Field(min_length=1, max_length=900)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class ResultView(StrictText):
    sentiment: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"]
    reason: str = Field(min_length=1, max_length=900)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class ValuationView(StrictText):
    status: Literal["ASSESSED", "INSUFFICIENT_DATA"]
    verdict: Literal["SUPPORTIVE", "NOT_SUPPORTIVE", "INCONCLUSIVE"] = "INCONCLUSIVE"
    text: str = Field(min_length=1, max_length=900)
    evidence_ids: list[str] = Field(max_length=8)


class ResearchAction(StrictText):
    action: Literal["WATCH", "INVESTIGATE", "NO_ACTION", "INSUFFICIENT_EVIDENCE"]
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)


class QualityNote(StrictText):
    text: str = Field(min_length=1, max_length=900)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)


class SeasonalityAssessment(QualityNote):
    status: Literal["CONSISTENT_WITH_HISTORICAL_PATTERN", "LARGER_THAN_HISTORICAL_PATTERN",
                    "SMALLER_THAN_HISTORICAL_PATTERN", "NO_CLEAR_PATTERN", "INSUFFICIENT_HISTORY"]


class ResultsSupport(QualityNote):
    assessment: Literal["STRONG_SUPPORT", "MODERATE_SUPPORT", "MIXED_EVIDENCE",
                        "LIMITED_SUPPORT", "INSUFFICIENT_EVIDENCE"]


class ResearchGap(StrictText):
    type: Literal["MANAGEMENT_GUIDANCE", "SEASONALITY", "ORDER_BOOK", "MARGIN_GUIDANCE",
                  "CASH_CONVERSION", "CAPEX", "DEBT", "CUSTOMER_CONCENTRATION",
                  "HISTORICAL_VALUATION", "PEER_VALUATION", "FORWARD_GROWTH",
                  "MANAGEMENT_EXECUTION", "EARNINGS_DRIVERS"]
    question: str = Field(min_length=1, max_length=500)
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class InvestorQuality(StrictText):
    operating_performance: QualityNote
    seasonality_context: SeasonalityAssessment
    earnings_quality: QualityNote
    valuation_expectations: QualityNote
    results_vs_valuation: ResultsSupport
    strengthening_evidence: list[QualityNote] = Field(max_length=5)
    weakening_evidence: list[QualityNote] = Field(max_length=5)
    research_gaps: list[ResearchGap] = Field(max_length=12)
    research_priority: Literal["HIGH", "MEDIUM", "LOW"]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]


class AnalystOutput(StrictText):
    summary: str = Field(min_length=1, max_length=1500)
    summary_evidence_ids: list[str] = Field(min_length=1, max_length=8)
    positives: list[Claim] = Field(max_length=8)
    concerns: list[Claim] = Field(max_length=8)
    material_changes: list[MaterialChange] = Field(max_length=10)
    questions: list[str] = Field(max_length=8)
    data_quality_notes: list[str] = Field(max_length=8)
    management_commentary: AnalystNote | None = None
    what_might_happen: AnalystNote | None = None
    result_view: ResultView | None = None
    valuation_assessment: ValuationView | None = None
    research_action: ResearchAction | None = None
    quality_report: InvestorQuality | None = None

    @field_validator("questions", "data_quality_notes")
    @classmethod
    def bounded_notes(cls, values: list[str]) -> list[str]:
        if any(len(value) > 500 or "<" in value or ">" in value or "http://" in value.lower()
               or "https://" in value.lower() for value in values):
            raise ValueError("Invalid note text")
        return values


class GeneratedValuationView(ValuationView):
    verdict: Literal["SUPPORTIVE", "NOT_SUPPORTIVE", "INCONCLUSIVE"]


class GeneratedAnalystOutput(AnalystOutput):
    """Schema sent to the model; older stored analyses may omit these fields."""

    what_might_happen: AnalystNote
    result_view: ResultView
    valuation_assessment: GeneratedValuationView
    research_action: ResearchAction


class AnalysisRead(BaseModel):
    id: int
    company_id: int
    financial_period_id: int
    analysis_type: str
    model_provider: str
    model_name: str
    prompt_version: str
    current_prompt_version: str = PROMPT_VERSION
    context_hash: str
    context_chars: int
    evidence_registry: dict
    summary: str | None
    structured_output: AnalystOutput | None
    status: str
    failure_reason: str | None
    attempt_count: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
