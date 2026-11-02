from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import gold

router = APIRouter(prefix="/api/gold", tags=["gold"])


@router.get("")
def gold_dashboard(version: int = Query(default=1, ge=1, le=2), db: Session = Depends(get_db)):
    view = gold.dashboard(db)
    if version == 1:
        # Older open dashboards labelled prices.india as Hyderabad. Keep that
        # contract until they reload the frontend, rather than relabelling data.
        def legacy(prices):
            return {**prices, "national": prices["india"], "india": prices["hyderabad"]}
        view["prices"] = legacy(view["prices"])
        view["history"] = [{**row, "prices": legacy(row["prices"])} for row in view["history"]]
    view["api_version"] = version
    return view


@router.post("/refresh")
async def refresh_gold(db: Session = Depends(get_db)):
    # A dashboard refresh collects evidence. The scheduled monitor owns delivery.
    return await gold.refresh(db)
