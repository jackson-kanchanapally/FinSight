from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.analysis import _company
from app.database import get_db
from app.schemas.memory import MemoryQuery
from app.services.memory.service import index_company, memory_status, query_memory

router = APIRouter(prefix="/api/companies/{symbol}/memory", tags=["company memory"])


@router.get("")
def status(symbol: str, db: Session = Depends(get_db)):
    return memory_status(db, _company(db, symbol).id)


@router.post("/index")
def index(symbol: str, force: bool = False, db: Session = Depends(get_db)):
    return index_company(db, _company(db, symbol).id, force=force)


@router.post("/query")
def query(symbol: str, request: MemoryQuery, db: Session = Depends(get_db)):
    # The path company is mandatory; callers cannot override it in the request body.
    return query_memory(db, _company(db, symbol).id, request)
