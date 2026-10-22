"""Phase 8.3B browser fixtures, confined to temporary SQLite."""
from decimal import Decimal
from sqlalchemy import select
from tests.accuracy_browser_app import app, Session
from tests.test_phase83 import data, CAPTURE, AFTER
from tests.test_phase83b import save_ai
from app.models.company import Company
from app.models.financial import FinancialFact, FinancialFactCorrection, FinancialPeriod
from app.services import expectations

with Session() as db:
    company=db.scalar(select(Company).where(Company.symbol=='ACCURACY'))
    revenue=db.scalar(select(FinancialFact).join(FinancialPeriod).where(FinancialPeriod.company_id==company.id,
        FinancialPeriod.period_end==__import__('datetime').date(2026,6,30),FinancialFact.metric_code=='REVENUE_FROM_OPERATIONS'))
    real_clock=expectations.utcnow
    try:
        expectations.utcnow=lambda:CAPTURE
        ledger,revision=save_ai(db,company,expected_value='110',confidence='0.8',confidence_event='MET_UNDER_FROZEN_TOLERANCE',
                               interval_lower='90',interval_upper='120',interval_probability='0.8')
        evidence=revision.definition['evidence_id']
        expectations.save(db,company.id,data(source_type='AI',evidence_id=evidence,metric='PAT',source_reference='Missing PAT'))
        expectations.save(db,company.id,data(source_type='AI',evidence_id=evidence,period='2027-06-30',
            cutoff_at='2027-06-30T23:59:59Z',source_reference='Pending future forecast'))
        expectations.utcnow=lambda:AFTER
        expectations.refresh_expectations(db,company.id)
        revenue.value=Decimal('150')
        db.add(FinancialFactCorrection(fact_id=revenue.id,previous_value=100,corrected_value=150,reason='Test correction',corrected_at=AFTER))
        db.commit()
        expectations.evaluate(db,company.id,ledger.id);db.commit()
    finally:
        expectations.utcnow=real_clock
