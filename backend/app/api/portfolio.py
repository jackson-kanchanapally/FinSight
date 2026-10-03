from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.portfolio import HoldingRead, PortfolioRead
from app.services.portfolio import UnknownCompanyError, get_positions

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def render(db: Session, symbol: str | None = None) -> PortfolioRead:
    try:
        positions = get_positions(db, symbol)
    except UnknownCompanyError as exc:
        raise HTTPException(status_code=404, detail="Company not found") from exc
    holdings = [
        HoldingRead(
            symbol=p.company.symbol,
            name=p.company.name,
            quantity=p.quantity,
            average_purchase_price=p.average_price,
            cost_basis=p.cost_basis,
            realised_pnl=p.realised_pnl,
        )
        for p in positions
        if p.quantity > 0
    ]
    return PortfolioRead(
        holdings=holdings,
        realised_pnl=sum((p.realised_pnl for p in positions), start=0),
    )


@router.get("", response_model=PortfolioRead)
def portfolio(db: Session = Depends(get_db)) -> PortfolioRead:
    return render(db)


@router.get("/{symbol}", response_model=HoldingRead)
def portfolio_for_symbol(symbol: str, db: Session = Depends(get_db)) -> HoldingRead:
    result = render(db, symbol)
    if not result.holdings:
        # A closed or never-traded company still has a valid per-symbol result.
        from app.services.portfolio import get_company, calculate_position

        company = get_company(db, symbol)
        if company is None:
            raise HTTPException(status_code=404, detail="Company not found")
        p = calculate_position(db, company)
        return HoldingRead(
            symbol=company.symbol,
            name=company.name,
            quantity=p.quantity,
            average_purchase_price=p.average_price,
            cost_basis=p.cost_basis,
            realised_pnl=p.realised_pnl,
        )
    return result.holdings[0]
