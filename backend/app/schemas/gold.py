from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class GlobalGoldQuote(BaseModel):
    usd_per_oz: Decimal = Field(gt=0, allow_inf_nan=False)
    as_of: datetime
    fetched_at: datetime
    source: str = "Gold API"
    source_url: str = "https://gold-api.com/"
    unit: Literal["USD/troy oz"] = "USD/troy oz"


class DollarRupeeQuote(BaseModel):
    usd_inr_rate: Decimal = Field(gt=0, allow_inf_nan=False)
    quoted_gold_inr_per_oz: Decimal = Field(gt=0, allow_inf_nan=False)
    as_of: datetime
    fetched_at: datetime
    source: str = "Gold API"
    source_url: str = "https://gold-api.com/"
    unit: Literal["INR/USD"] = "INR/USD"


class IndiaGoldQuote(BaseModel):
    city: Literal["India", "Hyderabad"] = "Hyderabad"
    inr_22k_per_g: Decimal = Field(gt=0, allow_inf_nan=False)
    inr_24k_per_g: Decimal = Field(gt=0, allow_inf_nan=False)
    change_22k_pct: Decimal | None = Field(default=None, allow_inf_nan=False)
    change_24k_pct: Decimal | None = Field(default=None, allow_inf_nan=False)
    rate_date: date
    fetched_at: datetime
    source: str = "Groww"
    source_url: str = "https://groww.in/gold-rates/gold-rate-today-in-hyderabad"
    unit: Literal["INR/gram"] = "INR/gram"
    basis: str = "Published retail reference; excludes GST and making charges"


class GoldNewsData(BaseModel):
    identity: str
    title: str = Field(max_length=500)
    summary: str = Field(max_length=1200)
    source: str
    url: str
    published_at: datetime
    impact: Literal["POSITIVE", "NEGATIVE", "MIXED", "UNCLEAR"] = "UNCLEAR"
    impact_reason: str
    important: bool = False
