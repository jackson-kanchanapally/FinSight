"""Disposable, synthetic history for the Phase 8.2 browser regression."""
from decimal import Decimal
from tests.thesis_browser_app import app, Session
from tests.test_phase82 import setup, monitor
from app.services.financials.service import calculate_company_metrics

with Session() as db:
    company, period, revenue = setup(db, history=True)
    monitor(db, company)
    revenue.value = Decimal("90")
    db.commit()
    calculate_company_metrics(db, company.id)
