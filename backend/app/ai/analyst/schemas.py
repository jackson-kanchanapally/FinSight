from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.ai.analyst.prompts import PROMPT_VERSION


class StrictText(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def safe_text(cls, value):
        if isinstance(value, str) and (
            "<" in value
            or ">" in value
            or "http://" in value.lower()
            or "https://" in value.lower()
        ):
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


class HistoricalNote(AnalystNote):
    source_quote: str = Field(min_length=15, max_length=500)


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
    status: Literal[
        "CONSISTENT_WITH_HISTORICAL_PATTERN",
        "LARGER_THAN_HISTORICAL_PATTERN",
        "SMALLER_THAN_HISTORICAL_PATTERN",
        "NO_CLEAR_PATTERN",
        "INSUFFICIENT_HISTORY",
    ]


class ResultsSupport(QualityNote):
    assessment: Literal[
        "STRONG_SUPPORT",
        "MODERATE_SUPPORT",
        "MIXED_EVIDENCE",
        "LIMITED_SUPPORT",
        "INSUFFICIENT_EVIDENCE",
    ]


class ResearchGap(StrictText):
    type: Literal[
        "MANAGEMENT_GUIDANCE",
        "SEASONALITY",
        "ORDER_BOOK",
        "MARGIN_GUIDANCE",
        "CASH_CONVERSION",
        "CAPEX",
        "DEBT",
        "CUSTOMER_CONCENTRATION",
        "HISTORICAL_VALUATION",
        "PEER_VALUATION",
        "FORWARD_GROWTH",
        "MANAGEMENT_EXECUTION",
        "EARNINGS_DRIVERS",
    ]
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


class DeterministicResults(StrictText):
    verdict: Literal[
        "VERY_POSITIVE",
        "POSITIVE",
        "NEUTRAL",
        "NEGATIVE",
        "VERY_NEGATIVE",
        "INSUFFICIENT_EVIDENCE",
    ]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reasons: list[str]
    evidence_ids: list[str]
    signals: dict[str, str]
    methodology: str


class DeterministicValuation(StrictText):
    verdict: Literal[
        "EXTREMELY_UNDERVALUED",
        "UNDERVALUED",
        "FAIRLY_VALUED",
        "OVERVALUED",
        "EXTREMELY_OVERVALUED",
        "INSUFFICIENT_EVIDENCE",
    ]
    confidence: Literal["HIGH", "MEDIUM", "LOW", "NOT_ASSESSED"]
    reason: str


class ManagementStatementRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    statement_type: str
    topic: str
    raw_statement: str
    speaker_name: str | None
    speaker_role: str | None
    section_type: str
    source_type: str
    published_at: str
    document_id: int
    page_number: int
    source_url: str | None
    document_url: str
    extraction_confidence: str


class CommentaryDocumentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: int
    event_id: int
    document_type: str
    source: str
    published_at: str
    source_url: str | None
    document_url: str
    source_priority: int
    source_references: list[dict[str, str]] = Field(default_factory=list)


class GuidanceRead(StrictText):
    id: int
    statement_evidence_id: str
    metric: str
    target_period: str
    lower_bound: str
    upper_bound: str
    unit: str
    published_at: str
    change: str
    actual_comparison: str
    execution_outcome: str = "INSUFFICIENT_DATA"


class GuidanceChangeRead(StrictText):
    status: str
    statement_evidence_id: str
    topic: str
    published_at: str


class ClaimComparisonRead(StrictText):
    statement_evidence_id: str
    status: str
    financial_evidence_ids: list[str]
    note: str


class EvidenceNoteRead(StrictText):
    text: str
    evidence_ids: list[str]


class ReviewItem(StrictText):
    text: str = Field(min_length=1, max_length=800)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    basis: Literal["VERIFIED_FACT", "MANAGEMENT_CLAIM", "INTERPRETATION"] = "INTERPRETATION"
    source_quote: str | None = Field(default=None, max_length=600)
    signal: Literal["POSITIVE", "NEGATIVE", "MIXED", "WATCH"] = "WATCH"
    causal_basis: Literal["ESTABLISHED_CAUSE", "MANAGEMENT_EXPLANATION", "INVESTOR_HYPOTHESIS", "UNKNOWN_CAUSE"] | None = None


class ReviewValuation(ReviewItem):
    status: Literal["MORE_CREDIBLE", "UNCHANGED", "LESS_CREDIBLE", "INSUFFICIENT_EVIDENCE"]


class QuarterlyInvestorReview(StrictText):
    quarterly_investor_view: list[ReviewItem] = Field(min_length=2, max_length=4)
    key_developments: list[ReviewItem] = Field(max_length=7)
    management_signals: list[ReviewItem] = Field(max_length=5)
    what_changed: list[ReviewItem] = Field(max_length=6)
    valuation_implication: ReviewValuation
    key_risks: list[ReviewItem] = Field(max_length=5)
    watch_next_quarter: list[ReviewItem] = Field(max_length=6)
    investor_conclusion: ReviewItem


class GeneratedQuarterlyReview(StrictText):
    quarterly_review: QuarterlyInvestorReview


class InvestorReport(StrictText):
    quarterly_review: QuarterlyInvestorReview | None = None
    checkpoints: list[dict] = Field(default_factory=list)
    review_packet: dict = Field(default_factory=dict)
    execution_summary: str = ""
    history_note: str = ""
    material_statement_count: int = 0
    full_statement_count: int = 0
    results: DeterministicResults
    valuation: DeterministicValuation
    source_documents: list[CommentaryDocumentRead] = Field(default_factory=list)
    valuation_support: list[EvidenceNoteRead] = Field(default_factory=list)
    valuation_challenges: list[EvidenceNoteRead] = Field(default_factory=list)
    management_statements: list[ManagementStatementRead]
    management_guidance: list[GuidanceRead]
    guidance_changes: list[GuidanceChangeRead] = Field(default_factory=list)
    management_execution_history: list[dict] = Field(default_factory=list)
    commentary_availability: dict = Field(default_factory=dict)
    claim_comparisons: list[ClaimComparisonRead]
    investor_stance: str = "INSUFFICIENT_EVIDENCE"
    investor_conclusion: EvidenceNoteRead | None = None
    tested_pe: dict = Field(default_factory=dict)
    earnings_quality: dict = Field(default_factory=dict)
    research_gaps: list[str] = Field(default_factory=list)
    strengthen_case: list[str] = Field(default_factory=list)
    weaken_case: list[str] = Field(default_factory=list)


class AnalystOutput(StrictText):
    quarterly_review: QuarterlyInvestorReview | None = None
    review_trace: dict | None = None
    historical_context: list[HistoricalNote] = Field(default_factory=list, max_length=4)
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
    investor_report: InvestorReport | None = None

    @field_validator("questions", "data_quality_notes")
    @classmethod
    def bounded_notes(cls, values: list[str]) -> list[str]:
        if any(
            len(value) > 500
            or "<" in value
            or ">" in value
            or "http://" in value.lower()
            or "https://" in value.lower()
            for value in values
        ):
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
    quarterly_review: QuarterlyInvestorReview


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
    validation_diagnostics: list[dict] | None = None
    attempt_count: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
