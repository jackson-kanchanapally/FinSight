from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.company import Company
from app.models.market import MarketSnapshot
from app.schemas.market import MarketSnapshotInput, MarketSnapshotRead

router = APIRouter(tags=["market snapshots"])


def _company(db: Session, symbol: str) -> Company:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


@router.get("/api/companies/{symbol}/market-snapshot", response_model=MarketSnapshotRead | None)
def market_snapshot(symbol: str, db: Session = Depends(get_db)) -> MarketSnapshot | None:
    company = _company(db, symbol)
    return db.scalar(select(MarketSnapshot).where(MarketSnapshot.company_id == company.id))


@router.put("/api/companies/{symbol}/market-snapshot", response_model=MarketSnapshotRead)
def save_market_snapshot(symbol: str, payload: MarketSnapshotInput,
                         db: Session = Depends(get_db)) -> MarketSnapshot:
    company = _company(db, symbol)
    record = db.scalar(select(MarketSnapshot).where(MarketSnapshot.company_id == company.id))
    if record is None:
        record = MarketSnapshot(company_id=company.id)
        db.add(record)
    for key, value in payload.model_dump().items():
        setattr(record, key, value)
    db.commit()
    db.refresh(record)
    return record
