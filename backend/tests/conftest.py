from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_company(client, symbol="ESDS"):
    response = client.post(
        "/api/companies",
        json={"symbol": symbol.lower(), "name": "ESDS", "exchange": "NSE"},
    )
    assert response.status_code == 201
    return response.json()


def post_tx(client, kind, qty, price, day, charges="0"):
    return client.post(
        "/api/transactions",
        json={
            "symbol": "ESDS",
            "transaction_type": kind,
            "quantity": str(qty),
            "price": str(price),
            "transaction_date": day,
            "charges": charges,
        },
    )


@pytest.fixture(autouse=True)
def disable_background_research(monkeypatch):
    # Tests explicitly exercise queue workers with disposable sessions; never use
    # the application database or network from an unrelated TestClient lifespan.
    from app.config import settings
    monkeypatch.setattr(settings, "research_automation_enabled", False)
    monkeypatch.setattr(settings, "telegram_enabled", False)
    monkeypatch.setattr(settings, "ipo_monitor_enabled", False)
