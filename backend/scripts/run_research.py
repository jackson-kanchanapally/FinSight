"""Run bounded due work for one registered company, respecting persisted backoff."""
import argparse
import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from app.database import SessionLocal
from app.models.company import Company
from app.services.research_jobs import run_due, status

parser = argparse.ArgumentParser()
parser.add_argument('--symbol', required=True)
parser.add_argument('--limit', type=int, default=5, choices=range(1, 11))
args = parser.parse_args()
with SessionLocal() as db:
    company = db.scalar(select(Company).where(Company.symbol == args.symbol.strip().upper()))
    if not company:
        raise SystemExit('Company not found')
    company_id = company.id
count = asyncio.run(run_due(SessionLocal, args.limit, company_id=company_id))
with SessionLocal() as db:
    result = status(db, company_id)
    for job in result['jobs']:
        job.pop('result', None)
print(json.dumps({'processed': count, **result}, default=str, indent=2))
