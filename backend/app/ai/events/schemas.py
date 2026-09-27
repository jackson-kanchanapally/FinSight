from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field

from app.ai.analyst.schemas import StrictText


class EventClaim(StrictText):
    text: str = Field(min_length=1, max_length=350)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class EventRecommendation(StrictText):
    action: Literal["WATCH", "INVESTIGATE", "NO_ACTION", "INSUFFICIENT_EVIDENCE"]
    reason: str = Field(min_length=1, max_length=300)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class EventInsight(StrictText):
    summary: EventClaim
    why_it_matters: EventClaim
    recommendation: EventRecommendation
    what_next: EventClaim
    management_says: EventClaim | None = None


class EventAnalysisRead(StrictText):
    id: int
    event_id: int
    company_id: int
    model_provider: str
    model_name: str
    prompt_version: str
    context_hash: str
    evidence_registry: dict
    structured_output: EventInsight | None
    status: str
    failure_reason: str | None
    attempt_count: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
