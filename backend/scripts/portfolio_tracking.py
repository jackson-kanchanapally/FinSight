"""Activate or audit tracking for every registered portfolio entry.

Defaults to a read-only audit. --activate creates only missing queue records;
--score runs eligible accuracy jobs with normal leases/backoff and no generation.
"""
import argparse
import asyncio
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from app.config import settings
from app.database import SessionLocal
from app.models.company import Company
from app.models.research import ResearchJob
from app.services.expectation_evidence import utc, utcnow
from app.services.forecast_accuracy import forecast_accuracy
from app.services.research_jobs import ensure_jobs, run_due


def snapshot(db):
    now = utcnow()
    companies = list(db.scalars(select(Company).order_by(Company.symbol)))
    jobs = list(db.scalars(select(ResearchJob).order_by(ResearchJob.company_id, ResearchJob.kind)))
    records = []
    for company in companies:
        company_jobs = [job for job in jobs if job.company_id == company.id]
        required = {"ACCURACY", "RESEARCH", "MARKET", "COMMENTARY"}
        if "NSE" in company.exchange.upper():
            required.add("DISCLOSURES:NSE")
        missing = sorted(required - {job.kind for job in company_jobs})
        scopes = {
            scope: forecast_accuracy(db, company.id, scope)["overview"]
            for scope in ("CONSOLIDATED", "STANDALONE")
        }
        records.append({
            "symbol": company.symbol,
            "exchange": company.exchange,
            "tracking_registered": not missing,
            "missing_jobs": missing,
            "accuracy": scopes,
            "jobs": [{
                "kind": job.kind, "status": job.status,
                "last_success_at": job.last_success_at,
                "next_run_at": job.next_run_at,
                "failures": job.failures,
                "error": job.error,
            } for job in company_jobs],
        })
    return {
        "checked_at": now,
        "automation_enabled": settings.research_automation_enabled,
        "source_mode": settings.event_source_mode,
        "poll_seconds": settings.research_poll_seconds,
        "batch_size": settings.research_batch_size,
        "company_count": len(companies),
        "tracking_registered": sum(record["tracking_registered"] for record in records),
        "job_count": len(jobs),
        "job_states": dict(Counter(job.status for job in jobs)),
        "due_jobs": sum(utc(job.next_run_at) <= now for job in jobs),
        "companies": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--score", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.activate:
        with SessionLocal() as db:
            for company in db.scalars(select(Company).order_by(Company.id)):
                ensure_jobs(db, company)
            db.commit()
    processed = 0
    if args.score:
        with SessionLocal() as db:
            count = len(list(db.scalars(select(Company.id))))
        processed = asyncio.run(run_due(SessionLocal, limit=count, kinds=("ACCURACY",)))
    with SessionLocal() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))
        result = snapshot(db)
    result["accuracy_jobs_processed"] = processed
    rendered = json.dumps(result, default=str, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
        args.output.chmod(0o600)
        print(json.dumps({key: value for key, value in result.items() if key != "companies"}, default=str, indent=2))
        print(f"Detailed audit: {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
