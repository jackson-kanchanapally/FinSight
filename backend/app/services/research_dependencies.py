"""Batch dependency hashes: unchanged source inputs do not rerun research.

Include dates because deadlines and freshness change even without new sources.
Exclude derived expectation/evaluation rows to avoid a self-invalidating cache.
"""
from sqlalchemy import select
from app.models.event import CompanyEvent, Document, DocumentPage, EventReference
from app.models.financial import FinancialPeriod, FinancialFact, FinancialMetric, FinancialFactCorrection
from app.models.commentary import ManagementStatement, ManagementGuidance
from app.models.checkpoint import InvestorCheckpoint
from app.models.market import MarketSnapshot
from app.services.expectation_evidence import utcnow
from app.services.thesis_monitoring_evidence import digest
from app.services.forecasting import VERSION
from app.services.forecast_scoring import VERSION as SCORING_VERSION


def input_hash(db, company_id):
    periods = select(FinancialPeriod.id).where(FinancialPeriod.company_id == company_id)
    documents = select(Document.id).where(Document.company_id == company_id)
    events = select(CompanyEvent.id).where(CompanyEvent.company_id == company_id)
    facts = select(FinancialFact.id).where(FinancialFact.financial_period_id.in_(periods))
    filters = [
        (FinancialPeriod, FinancialPeriod.company_id == company_id),
        (FinancialFact, FinancialFact.financial_period_id.in_(periods)),
        (FinancialMetric, FinancialMetric.financial_period_id.in_(periods)),
        (FinancialFactCorrection, FinancialFactCorrection.fact_id.in_(facts)),
        (CompanyEvent, CompanyEvent.company_id == company_id),
        (Document, Document.company_id == company_id),
        (DocumentPage, DocumentPage.document_id.in_(documents)),
        (EventReference, EventReference.event_id.in_(events)),
        (ManagementStatement, ManagementStatement.company_id == company_id),
        (ManagementGuidance, ManagementGuidance.company_id == company_id),
        (InvestorCheckpoint, InvestorCheckpoint.company_id == company_id),
        (MarketSnapshot, MarketSnapshot.company_id == company_id),
    ]
    content = {}
    for model, predicate in filters:
        # Hash each table separately; no ORM relationships or per-row queries.
        content[model.__tablename__] = digest([dict(row) for row in db.execute(
            select(model.__table__).where(predicate).order_by(model.id)).mappings()])
    return digest({'engine': VERSION, 'scoring': SCORING_VERSION, 'date': utcnow().date(), 'sources': content})
