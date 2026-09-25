from fastapi import FastAPI

from app.api import companies, portfolio, transactions

app = FastAPI(title="PortfolioAI", version="0.1.0", description="Phase 1 portfolio transaction engine")
app.include_router(companies.router)
app.include_router(transactions.router)
app.include_router(portfolio.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
