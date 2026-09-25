from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Company, Transaction, TransactionType

ZERO = Decimal("0")


class UnknownCompanyError(Exception):
    pass


class OversellError(Exception):
    pass


@dataclass
class Position:
    company: Company
    quantity: Decimal = ZERO
    cost_basis: Decimal = ZERO
    realised_pnl: Decimal = ZERO

    @property
    def average_price(self) -> Decimal:
        return self.cost_basis / self.quantity if self.quantity else ZERO


def get_company(db: Session, symbol: str) -> Company | None:
    return db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))


def record_transaction(db: Session, *, symbol: str, transaction_type: TransactionType,
                       quantity: Decimal, price: Decimal, transaction_date,
                       charges: Decimal = ZERO) -> Transaction:
    """Persist a transaction after replaying dated history to validate SELL availability.

    BUY charges are added to cost basis; SELL charges reduce net proceeds. This keeps
    all figures expressed as net investment performance, including transaction costs.
    """
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()).with_for_update())
    if company is None:
        raise UnknownCompanyError(symbol)
    row = Transaction(company_id=company.id, transaction_type=transaction_type,
                      quantity=quantity, price=price, transaction_date=transaction_date,
                      charges=charges)
    db.add(row)
    # Replay including the candidate, in transaction date order. This correctly
    # validates imported backdated trades as well as trades entered chronologically.
    db.flush()
    try:
        calculate_position(db, company)
    except OversellError:
        db.rollback()
        raise
    db.commit()
    db.refresh(row)
    return row


def calculate_position(db: Session, company: Company) -> Position:
    rows = db.scalars(select(Transaction).where(Transaction.company_id == company.id)
                      .order_by(Transaction.transaction_date, Transaction.id)).all()
    position = Position(company=company)
    for row in rows:
        if row.transaction_type == TransactionType.BUY:
            position.cost_basis += row.quantity * row.price + row.charges
            position.quantity += row.quantity
        else:
            if row.quantity > position.quantity:
                raise OversellError(f"Recorded sale exceeds holdings for {company.symbol}")
            avg = position.average_price
            position.realised_pnl += row.quantity * (row.price - avg) - row.charges
            position.cost_basis -= row.quantity * avg
            position.quantity -= row.quantity
            if position.quantity == 0:
                position.cost_basis = ZERO
    return position


def get_positions(db: Session, symbol: str | None = None) -> list[Position]:
    stmt = select(Company).order_by(Company.symbol)
    if symbol:
        stmt = stmt.where(Company.symbol == symbol.strip().upper())
    companies = db.scalars(stmt).all()
    if symbol and not companies:
        raise UnknownCompanyError(symbol)
    return [p for company in companies if (p := calculate_position(db, company)).quantity > 0 or p.realised_pnl != 0]


def transaction_history(db: Session, symbol: str | None = None) -> list[Transaction]:
    stmt = select(Transaction).options(joinedload(Transaction.company)).order_by(Transaction.transaction_date, Transaction.id)
    if symbol:
        stmt = stmt.join(Company).where(Company.symbol == symbol.strip().upper())
    return list(db.scalars(stmt).all())
