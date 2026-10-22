"""Read-only ESDS/Eternal audit. No refresh, generation, network or synthetic data."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func, text
from app.database import SessionLocal
from app.models.company import Company
from app.models.financial import FinancialPeriod
from app.models.expectations import HistoricalExpectation, HistoricalExpectationRevision, ExpectationEvaluation
from app.models.thesis import ThesisPillar, ThesisExpectationRevision
from app.models.market import ValuationAssumptions
from app.services.forecast_accuracy import forecast_accuracy, comparisons
from app.services.forecasting import verified_history, METRICS
from app.services.expectation_evidence import financial_evidence_batch, utcnow
from app.services.checkpoint_guidance import guidance_page
from app.schemas.forecast_accuracy import ForecastAccuracyRead, ComparisonPage
from app.schemas.expectations import GuidancePage
from app.services.thesis_monitoring_evidence import digest


def audit():
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        output={'migration':db.scalar(text('SELECT version_num FROM alembic_version')), 'companies':[]}
        def fingerprints():
            return {m.__tablename__:digest([dict(row) for row in db.execute(select(m.__table__).order_by(m.id)).mappings()])
                    for m in (FinancialPeriod,HistoricalExpectation,HistoricalExpectationRevision,ExpectationEvaluation)}
        before=fingerprints()
        for symbol in ('ESDS','ETERNAL'):
            company=db.scalar(select(Company).where(Company.symbol==symbol))
            if not company:
                output['companies'].append({'symbol':symbol,'status':'COMPANY_NOT_PRESENT'});continue
            legacy=db.scalar(select(func.count()).select_from(ThesisExpectationRevision).join(ThesisPillar).where(ThesisPillar.company_id==company.id))
            scenarios=db.scalar(select(func.count()).select_from(ValuationAssumptions).where(ValuationAssumptions.company_id==company.id))
            count=db.scalar(select(func.count()).select_from(HistoricalExpectation).where(HistoricalExpectation.company_id==company.id))
            item={'symbol':symbol,'historical_forecasts':count,'legacy_thesis_expectation_revisions':legacy,
                  'valuation_assumption_sets':scenarios,'scopes':{}}
            for scope in ('CONSOLIDATED','STANDALONE'):
                report=ForecastAccuracyRead.model_validate(forecast_accuracy(db,company.id,scope))
                page=ComparisonPage.model_validate(comparisons(db,company.id,scope))
                guidance=GuidancePage.model_validate(guidance_page(db,company.id,scope=scope))
                periods=list(db.scalars(select(FinancialPeriod).where(FinancialPeriod.company_id==company.id,
                    FinancialPeriod.scope==scope,FinancialPeriod.period_type=='QUARTERLY',FinancialPeriod.status=='VALIDATED')
                    .order_by(FinancialPeriod.period_end)))
                batch=financial_evidence_batch(db,periods)
                readiness={metric:len(verified_history(db,company.id,scope,metric,periods,utcnow(),batch)) for metric in METRICS}
                item['scopes'][scope]={'overview':report.overview.model_dump(),'comparisons':page.total,
                    'consecutive_verified_quarters':readiness,'guidance_records':guidance.total,
                    'scorable_pairs':report.overview.eligible_sample}
            output['companies'].append(item)
        assert before==fingerprints()
        output['history_unchanged']=True
        print(json.dumps(output,default=str,indent=2))

if __name__=='__main__':
    audit()
