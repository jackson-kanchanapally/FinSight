"""Local review/history and a preview that never queues or saves a forecast."""
from datetime import timedelta
from types import SimpleNamespace
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.market_brief import MarketBrief, MarketBriefState
from app.services import market_briefs
from app.services.market_brief_sources import MarketBriefProvider

router = APIRouter(prefix="/api/market-briefs", tags=["market briefs"])


@router.get("")
def status(db: Session = Depends(get_db)):
    return market_briefs.dashboard(db)


@router.get("/reports/{brief_id}")
def report(brief_id: int, db: Session = Depends(get_db)):
    row = db.get(MarketBrief, brief_id)
    if row is None:
        raise HTTPException(404, "Market review not found")
    return {"id": row.id, "slot": row.slot, "session_date": row.session_date, "created_at": row.created_at,
            "evidence": row.evidence, "analysis": row.analysis, "assessment": row.assessment,
            "message": market_briefs.format_message(row)}


@router.post("/preview")
async def preview(slot: Literal["morning", "midmorning", "noon", "close"], db: Session = Depends(get_db)):
    current = market_briefs.now()
    provider = MarketBriefProvider()
    try:
        calendar = await provider.calendar(current)
    except Exception:
        state = db.get(MarketBriefState, 1)
        if not state or not state.calendar_checked_at or current - market_briefs.utc(state.calendar_checked_at) > timedelta(days=7):
            raise HTTPException(503, "NSE calendar unavailable; preview cannot establish the session") from None
        calendar = state.calendar
    if not market_briefs.is_session(current.astimezone(market_briefs.IST).date(), calendar):
        raise HTTPException(409, "This is not a regular NSE trading day")
    evidence, analysis, assessment, morning, previous = await market_briefs.collect_review(db, slot, current, calendar, provider=provider)
    row = SimpleNamespace(slot=slot, created_at=current, evidence=evidence, analysis=analysis,
                          assessment=assessment, previous_close_id=previous.id if previous else None)
    return {"preview": True, "saved": False, "queued": False, "message": market_briefs.format_message(row, preview=True),
            "analysis": analysis, "assessment": assessment, "source_gaps": evidence["errors"]}
