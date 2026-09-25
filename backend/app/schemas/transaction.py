from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.transaction import TransactionType


class TransactionCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=20)
    transaction_type: TransactionType
    quantity: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    price: Decimal = Field(gt=0, max_digits=18, decimal_places=4)
    transaction_date: date
    charges: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=4)


class TransactionRead(BaseModel):
    id: int
    company_id: int
    transaction_type: TransactionType
    quantity: Decimal
    price: Decimal
    transaction_date: date
    charges: Decimal
    created_at: datetime
    symbol: str
    model_config = ConfigDict(from_attributes=True)
