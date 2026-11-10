"""Routes backed by NSE's official MCP servers (live quotes and Bhavcopy analytics)."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.market.nse_mcp import NSEMCPError
from app.services import nse_insights

router = APIRouter(prefix="/api/nse", tags=["NSE market data (MCP)"])


def _unavailable(exc: NSEMCPError) -> HTTPException:
    if exc.reason == "DISABLED":
        return HTTPException(status_code=503, detail="NSE MCP is disabled. Set NSE_MCP_ENABLED=true to use it.")
    if exc.reason == "SYMBOL_NOT_FOUND":
        return HTTPException(status_code=404, detail=exc.detail or "Symbol not found on NSE")
    return HTTPException(status_code=502, detail=f"NSE MCP unavailable ({exc.reason}). Try again shortly.")


@router.get("/status")
async def status() -> dict:
    """Which NSE MCP servers respond and the tools each one currently exposes."""
    return await nse_insights.server_status()


@router.get("/market-pulse")
async def market_pulse(sector_period: str = Query("1D", pattern="^(1D|1W|1M|3M|6M|1Y)$")) -> dict:
    """Key indices, breadth and VIX, Nifty valuation percentile, sector and stock movers."""
    try:
        result = await nse_insights.market_pulse(sector_period)
    except NSEMCPError as exc:
        raise _unavailable(exc) from exc
    if not any(result[key] for key in ("mood", "valuation", "sector_movers", "stock_movers")) and not result["indices"]["items"]:
        reasons = {e["reason"] for e in result["errors"]}
        if reasons == {"DISABLED"}:
            raise _unavailable(NSEMCPError("DISABLED"))
        raise HTTPException(status_code=502, detail="NSE MCP returned no market data. Try again shortly.")
    return result


@router.get("/portfolio")
async def portfolio(db: Session = Depends(get_db)) -> dict:
    """Mark open positions to market with NSE quotes; adds 1-year beta and return vs Nifty 50."""
    return await nse_insights.portfolio_live(db)


@router.get("/stocks/{symbol}")
async def stock(symbol: str) -> dict:
    """Live quote, 52-week range, moving averages, volume, relative strength, dividends and actions."""
    try:
        return await nse_insights.stock_insight(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NSEMCPError as exc:
        raise _unavailable(exc) from exc


@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=40)) -> dict:
    """Find NSE symbols by company name or partial symbol, with the latest close."""
    try:
        return await nse_insights.search_symbols(q)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except NSEMCPError as exc:
        raise _unavailable(exc) from exc
