from decimal import Decimal

from pydantic import BaseModel


class HoldingRead(BaseModel):
    symbol: str
    name: str
    quantity: Decimal
    average_purchase_price: Decimal
    cost_basis: Decimal
    realised_pnl: Decimal


class PortfolioRead(BaseModel):
    holdings: list[HoldingRead]
    realised_pnl: Decimal
