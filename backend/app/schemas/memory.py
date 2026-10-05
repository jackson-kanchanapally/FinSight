from datetime import date
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.models.financial import FinancialScope


class MemoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=3, max_length=700)
    as_of_date: date | None = None
    date_from: date | None = None
    document_type: str | None = Field(default=None, max_length=40)
    fiscal_year: str | None = Field(default=None, pattern=r"^FY20\d{2}$")
    quarter: int | None = Field(default=None, ge=1, le=4)
    topic: str | None = Field(default=None, max_length=40)
    scope: FinancialScope | None = None
    limit: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def dates(self):
        if self.date_from and self.as_of_date and self.date_from > self.as_of_date:
            raise ValueError("date_from must not be after as_of_date")
        return self
