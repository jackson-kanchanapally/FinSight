from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import ipos

router = APIRouter(prefix="/api/ipos", tags=["ipos"])


@router.get("")
def list_ipos(db: Session = Depends(get_db)):
    return {"issues": ipos.list_issues(db, datetime.now(timezone.utc)), "status": ipos.status(db)}


@router.post("/refresh")
async def refresh_ipos(db: Session = Depends(get_db)):
    return await ipos.refresh(db)
