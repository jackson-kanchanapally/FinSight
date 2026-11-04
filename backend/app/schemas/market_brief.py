"""Evidence-linked, bounded narrative; index moves and forecast scores are code-owned."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class MarketClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=430)
    evidence_ids: list[str] = Field(min_length=1, max_length=6)


class MarketDriver(MarketClaim):
    basis: Literal["REPORTED", "POSSIBLE"]


class MarketNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: Literal["UP", "DOWN", "RANGE", "UNCLEAR"]
    confidence: Literal["LOW", "MEDIUM"]
    summary: MarketClaim
    drivers: list[MarketDriver] = Field(max_length=3)
    review_of_morning: MarketClaim | None = None
    next_session: MarketClaim
    longer_term: MarketClaim
    invalidation: MarketClaim
