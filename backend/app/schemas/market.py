from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MarketSnapshotInput(BaseModel):
    close_price: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    pe_ratio: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=4)
    market_cap_crore: Decimal | None = Field(default=None, gt=0, max_digits=24, decimal_places=4)
    as_of_date: date
    source_url: str = Field(min_length=12, max_length=2048, pattern=r"^https://")

    @field_validator("as_of_date")
    @classmethod
    def not_future(cls, value: date) -> date:
        if value > datetime.now(ZoneInfo("Asia/Kolkata")).date():
            raise ValueError("Market date cannot be in the future")
        return value


class MarketSnapshotRead(MarketSnapshotInput):
    id: int
    company_id: int
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
