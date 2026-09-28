from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MarketSnapshotInput(BaseModel):
    close_price: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    pe_ratio: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=4)
    market_cap_crore: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=4)
    forward_eps_growth_pct: Decimal | None = Field(default=None, ge=-1000, le=1000, max_digits=10, decimal_places=4)
    growth_source_url: str | None = Field(default=None, min_length=12, max_length=2048, pattern=r"^https://")
    as_of_date: date
    source_url: str = Field(min_length=12, max_length=2048, pattern=r"^https://")

    @field_validator("as_of_date")
    @classmethod
    def not_future(cls, value: date) -> date:
        if value > datetime.now(ZoneInfo("Asia/Kolkata")).date():
            raise ValueError("Market date cannot be in the future")
        return value

    @model_validator(mode="after")
    def growth_has_source(self):
        if (self.forward_eps_growth_pct is None) != (self.growth_source_url is None):
            raise ValueError("Forward EPS growth and its source URL must be supplied together")
        return self


class MarketSnapshotRead(MarketSnapshotInput):
    id: int
    company_id: int
    updated_at: datetime
    peg_ratio: Decimal | None = None
    model_config = ConfigDict(from_attributes=True)
