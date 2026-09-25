from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Company
from app.schemas.company import CompanyCreate, CompanyRead

router = APIRouter(prefix="/api/companies", tags=["companies"])


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
def create_company(payload: CompanyCreate, db: Session = Depends(get_db)) -> Company:
    company = Company(**payload.model_dump())
    db.add(company)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Company symbol already exists") from exc
    db.refresh(company)
    return company


@router.get("", response_model=list[CompanyRead])
def list_companies(db: Session = Depends(get_db)) -> list[Company]:
    return list(db.scalars(select(Company).order_by(Company.symbol)).all())


@router.get("/{symbol}", response_model=CompanyRead)
def company_by_symbol(symbol: str, db: Session = Depends(get_db)) -> Company:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company
