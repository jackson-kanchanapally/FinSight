from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class IPOData(BaseModel):
    source_id: str
    name: str = Field(max_length=300)
    source_url: str
    segment: str = "MAINBOARD"
    opens_on: date | None = None
    closes_on: date | None = None
    lists_on: date | None = None
    sector: str | None = None
    price_low: Decimal | None = Field(default=None, gt=0)
    price_high: Decimal | None = Field(default=None, gt=0)
    issue_cr: Decimal | None = Field(default=None, gt=0)
    fresh_cr: Decimal | None = Field(default=None, ge=0)
    ofs_cr: Decimal | None = Field(default=None, ge=0)
    lot_size: int | None = Field(default=None, gt=0)
    gmp: Decimal | None = None
    gmp_at: datetime | None = None
    subscription: Decimal | None = Field(default=None, ge=0)
    business: str | None = None
    objects: list[str] = Field(default_factory=list)
    financials: list[dict] = Field(default_factory=list)
    annual_eps: Decimal | None = None
    annual_eps_period: date | None = None
    annual_roe: Decimal | None = None
    rhp_url: str | None = None
    detail_error: str | None = None
