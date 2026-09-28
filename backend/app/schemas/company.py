from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompanyCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.-]+$")
    name: str = Field(min_length=1, max_length=200)
    exchange: str = Field(min_length=1, max_length=50)
    isin: str | None = Field(default=None, min_length=12, max_length=12)
    bse_code: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    sector: str | None = Field(default=None, max_length=100)

    @field_validator("symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        return value.strip().upper()


class CompanyRead(BaseModel):
    id: int
    symbol: str
    name: str
    exchange: str
    isin: str | None
    bse_code: str | None
    sector: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
