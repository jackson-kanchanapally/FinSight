"""Read-only ESDS integration audit; proposed forecasts are rolled back."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select, func
from app.database import SessionLocal
from app.models.company import Company
from app.models.expectations import HistoricalExpectationRevision
from app.services.forecasting import generate_company
from app.services.checkpoint_guidance import guidance_page
from app.services.autonomous_valuation import automated_valuation
from app.schemas.research import AutomatedValuationRead
from app.schemas.expectations import GuidancePage


def audit():
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.symbol == 'ESDS'))
        if not company:
            raise SystemExit('ESDS is not present; no audit data created.')
        before = db.scalar(select(func.count()).select_from(HistoricalExpectationRevision))
        forecast = generate_company(db, company.id)
        guidance = guidance_page(db, company.id, scope='CONSOLIDATED')
        GuidancePage.model_validate(guidance)
        value = automated_valuation(db, company.id, 'CONSOLIDATED')
        AutomatedValuationRead.model_validate(value)
        output = {'company': company.symbol, 'forecasts': forecast,
            'management': [{k: row[k] for k in ('id','metric','status','target_date','source_verified')} for row in guidance['checkpoint_items']],
            'valuation': {'status': value['status'], 'market': value['market'], 'forward': value['forward']},
            'standalone_guidance_count': guidance_page(db, company.id, scope='STANDALONE')['total']}
        db.rollback()
        assert db.scalar(select(func.count()).select_from(HistoricalExpectationRevision)) == before
        output['historical_predictions_unchanged'] = True
        print(json.dumps(output, default=str, indent=2))

if __name__ == '__main__':
    audit()
