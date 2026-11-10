"""Corporate actions (share basis) and measured price reactions to company events."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.company import Company
from app.models.event import CompanyEvent
from app.models.market_events import EventPriceReaction
from app.services import corporate_actions, price_reactions

router = APIRouter(tags=["corporate actions and price reactions"])


def _company(db: Session, symbol: str) -> Company:
    company = db.scalar(select(Company).where(Company.symbol == symbol.strip().upper()))
    if company is None:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _require_nse(company: Company) -> None:
    if not settings.nse_mcp_enabled:
        raise HTTPException(status_code=503, detail="NSE MCP is disabled. Set NSE_MCP_ENABLED=true to use it.")
    if company.exchange.strip().upper() != "NSE":
        raise HTTPException(status_code=422, detail="Corporate actions and reactions need an NSE-listed company")


@router.get("/api/companies/{symbol}/corporate-actions")
def corporate_actions_list(symbol: str, db: Session = Depends(get_db)) -> dict:
    """Stored NSE corporate actions and the confirmed split/bonus window used by valuation."""
    return corporate_actions.actions_read(db, _company(db, symbol).id)


@router.post("/api/companies/{symbol}/corporate-actions/refresh")
async def corporate_actions_refresh(symbol: str, db: Session = Depends(get_db)) -> dict:
    company = _company(db, symbol)
    _require_nse(company)
    await corporate_actions.sync_company(db, company, force=True)
    return corporate_actions.actions_read(db, company.id)


@router.get("/api/companies/{symbol}/price-reactions")
def company_price_reactions(symbol: str, db: Session = Depends(get_db)) -> dict:
    """1/5/20-session moves after each material event, with medians per event type."""
    return price_reactions.company_reactions(db, _company(db, symbol))


@router.post("/api/companies/{symbol}/price-reactions/refresh")
async def refresh_price_reactions(symbol: str, db: Session = Depends(get_db)) -> dict:
    company = _company(db, symbol)
    _require_nse(company)
    result = await price_reactions.refresh_company(db, company)
    return {**result, **price_reactions.company_reactions(db, company)}


@router.get("/api/events/{event_id}/price-reaction")
def event_price_reaction(event_id: int, db: Session = Depends(get_db)) -> dict | None:
    event = db.get(CompanyEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    row = db.scalar(select(EventPriceReaction).where(EventPriceReaction.event_id == event_id))
    return price_reactions.reaction_read(row, event) if row else None
