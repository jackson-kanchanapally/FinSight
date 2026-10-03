from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.transaction import TransactionCreate, TransactionRead
from app.services.portfolio import (
    OversellError,
    UnknownCompanyError,
    record_transaction,
    transaction_history,
)

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def as_read(row) -> TransactionRead:
    return TransactionRead(
        id=row.id,
        company_id=row.company_id,
        transaction_type=row.transaction_type,
        quantity=row.quantity,
        price=row.price,
        transaction_date=row.transaction_date,
        charges=row.charges,
        created_at=row.created_at,
        symbol=row.company.symbol,
    )


@router.post("", response_model=TransactionRead, status_code=201)
def create_transaction(
    payload: TransactionCreate, db: Session = Depends(get_db)
) -> TransactionRead:
    try:
        row = record_transaction(db, **payload.model_dump())
    except UnknownCompanyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc
    except OversellError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return as_read(row)


@router.get("", response_model=list[TransactionRead])
def list_transactions(db: Session = Depends(get_db)) -> list[TransactionRead]:
    return [as_read(row) for row in transaction_history(db)]


@router.get("/{symbol}", response_model=list[TransactionRead])
def transactions_for_symbol(
    symbol: str, db: Session = Depends(get_db)
) -> list[TransactionRead]:
    rows = transaction_history(db, symbol)
    if not rows:
        # Distinguish a known company with no history from an unknown ticker.
        from app.services.portfolio import get_company

        if get_company(db, symbol) is None:
            raise HTTPException(status_code=404, detail="Company not found")
    return [as_read(row) for row in rows]
