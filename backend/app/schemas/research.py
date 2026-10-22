from datetime import datetime
from pydantic import BaseModel
from app.schemas.expectations import ExpectationPage
from app.schemas.forecast_accuracy import ForecastAccuracyRead


class ForecastPage(ExpectationPage):
    availability: list[dict]
    engine_version: str


class AutomationJobRead(BaseModel):
    id: int
    kind: str
    status: str
    next_run_at: datetime
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    attempts: int
    failures: int
    error: str | None
    result: dict


class AutomationRead(BaseModel):
    enabled: bool
    poll_seconds: int
    gaps: list[str]
    jobs: list[AutomationJobRead]


class AutomatedValuationRead(BaseModel):
    status: str
    reason: str
    market: dict | None
    metrics: dict
    forward: dict
    scenarios: list[dict]
    history: dict | None = None
    scenario_gap: str | None = None
    implied_growth_pct: str | None = None
    implied_growth_method: str | None = None
