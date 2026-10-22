"""Portfolio-wide job enrollment and isolated deterministic scoring."""
import asyncio
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models.company import Company
from app.models.research import ResearchJob
from app.services import research_jobs
from tests.test_phase83 import db, CAPTURE


def test_all_registered_companies_and_later_additions_are_enrolled(db, monkeypatch):
    # More than the old market-refresh cap and worker batch. No trades required.
    db.add_all([Company(symbol=f"PORT{i}", name=f"Issuer {i}", exchange="NSE") for i in range(54)])
    db.commit()
    sessions = sessionmaker(bind=db.get_bind())
    seen = []

    async def score(session, job):
        assert job.kind == "ACCURACY"
        seen.append(job.company_id)
        return {"processed": 0}

    monkeypatch.setattr(research_jobs, "execute", score)
    assert asyncio.run(research_jobs.run_due(sessions, limit=100, kinds=("ACCURACY",))) == 54
    assert len(set(seen)) == 54
    db.expire_all()
    jobs = list(db.scalars(select(ResearchJob)))
    assert len(jobs) == 54 * 5
    assert all(job.status == "PENDING" for job in jobs if job.kind != "ACCURACY")
    assert asyncio.run(research_jobs.run_due(sessions, limit=100, kinds=("ACCURACY",))) == 0
    db.add(Company(symbol="LATER", name="New entry", exchange="NSE"))
    db.commit()
    assert asyncio.run(research_jobs.run_due(sessions, limit=100, kinds=("ACCURACY",))) == 1
    assert len(set(seen)) == 55


def test_enrollment_retains_accuracy_retry_and_active_lease(db):
    company = Company(symbol="PORT", name="Issuer", exchange="NSE")
    db.add(company)
    db.flush()
    research_jobs.ensure_jobs(db, company)
    job = db.scalar(select(ResearchJob).where(ResearchJob.kind == "ACCURACY"))
    job.status = "RETRY"
    job.next_run_at = CAPTURE + timedelta(hours=2)
    job.failures = 3
    db.commit()
    research_jobs.ensure_jobs(db, company)
    assert job.status == "RETRY" and job.failures == 3
    assert research_jobs.claim(db, now=CAPTURE, kinds=("ACCURACY",)) is None
    claimed = research_jobs.claim(db, now=CAPTURE + timedelta(hours=3), kinds=("ACCURACY",))
    assert claimed
    research_jobs.ensure_jobs(db, company)
    assert research_jobs.claim(db, now=CAPTURE + timedelta(hours=3), kinds=("ACCURACY",)) is None


def test_scheduler_uses_configured_portfolio_batch(monkeypatch):
    from app import main
    from app.config import settings

    calls = []

    async def run_due(factory, limit):
        calls.append(limit)

    monkeypatch.setattr(research_jobs, "run_due", run_due)
    monkeypatch.setattr(settings, "research_batch_size", 12)
    asyncio.run(main.scheduled_research())
    assert calls == [12]
