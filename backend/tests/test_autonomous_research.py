"""Autonomous research uses synthetic evidence only in disposable test databases."""
from datetime import timedelta
from sqlalchemy import select
from app.models.research import ResearchJob
from app.models.company import Company
from app.services.research_jobs import ensure_jobs, claim, finish
from tests.test_phase83 import db, setup, CAPTURE


def test_company_addition_queues_sources_and_research(client):
    response = client.post('/api/companies',json={'symbol':'ESDS','name':'ESDS Software Solution Limited','exchange':'NSE'})
    assert response.status_code == 201
    from app.database import get_db
    from app.main import app
    with next(app.dependency_overrides[get_db]()) as session:
        company = session.get(Company,response.json()['id'])
        ensure_jobs(session,company); ensure_jobs(session,company)
        assert set(session.scalars(select(ResearchJob.kind))) == {'RESEARCH','MARKET','COMMENTARY','ACCURACY','DISCLOSURES:NSE','DISCLOSURES:ESDS_IR'}


def test_retry_lease_recovery_and_fencing(db):
    company,_,_=setup(db); ensure_jobs(db,company); db.commit()
    for j in db.scalars(select(ResearchJob)): j.next_run_at=CAPTURE
    db.commit()
    first=claim(db,CAPTURE)
    assert first
    job=db.get(ResearchJob,first[0]); job.next_run_at=CAPTURE; db.commit()
    recovered=claim(db,CAPTURE+timedelta(hours=2))
    assert recovered[0] == first[0] and recovered[1] != first[1]
    assert not finish(db,*first,result={'obsolete':True},now=CAPTURE+timedelta(hours=2))
    assert finish(db,*recovered,error='source unavailable',now=CAPTURE+timedelta(hours=2))
    db.expire_all(); job=db.get(ResearchJob,first[0])
    assert job.status=='RETRY' and job.failures==1
    assert job.next_run_at.replace(tzinfo=CAPTURE.tzinfo)==CAPTURE+timedelta(hours=2,minutes=5)

from datetime import date, datetime, timezone
from decimal import Decimal
from app.models.financial import MetricCode, FinancialUnit, FinancialPeriod
from app.models.expectations import HistoricalExpectationRevision, ExpectationEvaluation
from app.services import forecasting, expectations
from app.services.forecasting import next_quarter, fit
from tests.test_phase71 import issuer, result_period
from tests.test_phase82 import fact
from tests.test_phase83 import AFTER
import pytest


def forecast_history(db,monkeypatch,count=12,scope='CONSOLIDATED'):
    monkeypatch.setattr(forecasting,'utcnow',lambda:AFTER)
    monkeypatch.setattr(expectations,'utcnow',lambda:AFTER)
    company=issuer(db,'AUTO')
    ends=[]; end=date(2023,9,30)
    for i in range(count):
        ends.append(end)
        period=result_period(db,company,end,(end+timedelta(days=20)).isoformat(),fy=f'FY{end.year+int(end.month>=4)}',quarter={3:4,6:1,9:2,12:3}[end.month])
        period.scope=scope
        fact(db,period,MetricCode.REVENUE_FROM_OPERATIONS,str(100+(i%4)*10))
        fact(db,period,MetricCode.PAT,str(10+i%4))
        end=next_quarter(end)
    db.commit()
    return company,ends


def test_seasonal_forecast_and_immutable_revisions(db,monkeypatch):
    company,ends=forecast_history(db,monkeypatch)
    result=forecasting.generate_scope(db,company.id,'CONSOLIDATED'); db.commit()
    eligible=[r for r in result if r['status']=='MODEL_ESTIMATE']
    assert len(eligible)==8
    first=db.get(HistoricalExpectationRevision,eligible[0]['revision_id'])
    meta=first.definition['forecast']
    assert meta['model']=='SEASONAL_NAIVE' and meta['base']=='100.000000'
    assert meta['interval_probability'] is None and first.definition['confidence'] is None
    assert meta['information_cutoff']==AFTER.isoformat()
    original=dict(first.definition)
    again=forecasting.generate_scope(db,company.id,'CONSOLIDATED'); db.commit()
    assert all(r['unchanged'] for r in again if r['status']=='MODEL_ESTIMATE')
    from app.models.financial import FinancialFact, FinancialFactCorrection
    latest=db.scalar(select(FinancialFact).join(FinancialPeriod).where(FinancialPeriod.company_id==company.id,
        FinancialPeriod.period_end==ends[-1],FinancialFact.metric_code==MetricCode.REVENUE_FROM_OPERATIONS))
    latest.value=Decimal('140'); db.commit()
    revised=forecasting.generate_scope(db,company.id,'CONSOLIDATED'); db.commit()
    assert any(not r['unchanged'] for r in revised if r['status']=='MODEL_ESTIMATE')
    assert first.definition==original
    with pytest.raises(ValueError,match='IMMUTABLE'):
        first.definition={}; db.flush()
    db.rollback()


def test_insufficient_history_and_scope_isolation(db,monkeypatch):
    company,_=forecast_history(db,monkeypatch,count=3)
    output=forecasting.generate_scope(db,company.id,'CONSOLIDATED')
    assert all(r['status']=='INSUFFICIENT_EVIDENCE' for r in output)
    assert not list(db.scalars(select(HistoricalExpectationRevision)))
    assert all(r['verified_quarters']==0 for r in forecasting.generate_scope(db,company.id,'STANDALONE'))


def test_future_published_data_is_not_training_input(db,monkeypatch):
    company,_=forecast_history(db,monkeypatch)
    from app.models.event import CompanyEvent
    latest=db.scalar(select(FinancialPeriod).order_by(FinancialPeriod.period_end.desc()))
    event=db.get(CompanyEvent,latest.source_event_id)
    event.published_at=AFTER+timedelta(days=100); db.commit()
    results=forecasting.generate_scope(db,company.id,'CONSOLIDATED')
    assert all(r['status']=='INSUFFICIENT_EVIDENCE' for r in results)


def test_new_actual_evaluates_original_forecast(db,monkeypatch):
    company,_=forecast_history(db,monkeypatch)
    records=forecasting.generate_scope(db,company.id,'CONSOLIDATED'); db.commit()
    original=next(r for r in records if r.get('period')=='2026-09-30' and r['metric']=='REVENUE_FROM_OPERATIONS')
    period=result_period(db,company,date(2026,9,30),'2026-10-20',quarter=2)
    fact(db,period,MetricCode.REVENUE_FROM_OPERATIONS,'110'); db.commit()
    later=datetime(2026,11,1,tzinfo=timezone.utc)
    monkeypatch.setattr(expectations,'utcnow',lambda:later)
    evaluation=expectations.evaluate(db,company.id,original['expectation_id']); db.commit()
    assert evaluation.prospective_eligible and evaluation.result['actual_value']=='110.000000'
    assert expectations.evaluate(db,company.id,original['expectation_id']).id==evaluation.id


def test_continuous_errors_and_interval_coverage():
    from app.services.forecast_accuracy import continuous_metrics
    result=continuous_metrics([(Decimal('100'),Decimal('110'),Decimal('90'),Decimal('120')),
                               (Decimal('90'),Decimal('70'),Decimal('80'),Decimal('100'))])
    assert Decimal(result['mae'])==15 and Decimal(result['bias'])==5
    assert Decimal(result['rmse'])==Decimal(250).sqrt()
    assert Decimal(result['wape_pct'])==Decimal(30)/180*100
    assert Decimal(result['interval_coverage'])==Decimal('0.5')
    assert continuous_metrics([(Decimal(1),Decimal(0),Decimal(0),Decimal(2))])['wape_pct'] is None


def test_automated_valuation_missing_and_stale(db):
    from app.services.autonomous_valuation import automated_valuation
    from app.models.market import MarketSnapshot
    company,_,_=setup(db)
    assert automated_valuation(db,company.id,'CONSOLIDATED')['status']=='INSUFFICIENT_EVIDENCE'
    db.add(MarketSnapshot(company_id=company.id,close_price=Decimal(100),as_of_date=date(2026,7,25),
        provider='NSE_MCP',data_origin='EOD',source_url='https://example.org/quote')); db.commit()
    result=automated_valuation(db,company.id,'CONSOLIDATED',date(2026,10,1))
    assert result['status']=='STALE' and result['market']['date']=='2026-07-25'
    assert not result['scenarios'] and result['forward']['status']=='INSUFFICIENT_EVIDENCE'


def test_research_job_creates_forecasts_without_form(db,monkeypatch):
    import asyncio
    from app.services.research_jobs import execute
    company,_=forecast_history(db,monkeypatch)
    ensure_jobs(db,company); db.commit()
    job=db.scalar(select(ResearchJob).where(ResearchJob.kind=='RESEARCH'))
    result=asyncio.run(execute(db,job))
    assert any(r['status']=='MODEL_ESTIMATE' for r in result['scopes']['CONSOLIDATED'])
    assert all(r['status']=='INSUFFICIENT_EVIDENCE' for r in result['scopes']['STANDALONE'])


def test_research_cache_invalidates_when_source_changes(db, monkeypatch):
    import asyncio
    from app.services.research_jobs import execute
    from app.models.financial import FinancialFact
    company, _ = forecast_history(db, monkeypatch)
    ensure_jobs(db, company); db.commit()
    job = db.scalar(select(ResearchJob).where(ResearchJob.kind == 'RESEARCH'))
    first = asyncio.run(execute(db, job))
    job.result = first; db.commit()
    assert asyncio.run(execute(db, job))['unchanged']
    latest = db.scalar(select(FinancialFact).where(FinancialFact.metric_code == 'REVENUE_FROM_OPERATIONS').order_by(FinancialFact.id.desc()))
    latest.value += 1; db.commit()
    changed = asyncio.run(execute(db, job))
    assert not changed['unchanged'] and changed['input_hash'] != first['input_hash']
    assert changed['valuation']['CONSOLIDATED']['status'] == 'INSUFFICIENT_EVIDENCE'


def test_automatic_forward_valuation_uses_four_scoped_eps_forecasts(db, monkeypatch):
    from app.services.autonomous_valuation import automated_valuation
    from app.models.market import MarketSnapshot
    company, ends = forecast_history(db, monkeypatch)
    periods = list(db.scalars(select(FinancialPeriod).order_by(FinancialPeriod.period_end)))
    for i, period in enumerate(periods):
        row = fact(db, period, MetricCode.DILUTED_EPS, str(1 + (i % 4)/10))
        row.unit = FinancialUnit.INR_PER_SHARE
    db.add(MarketSnapshot(company_id=company.id, close_price=46, as_of_date=AFTER.date(),
        provider='NSE_MCP', data_origin='EOD', source_url='https://example.org/quote'))
    db.commit()
    forecasting.generate_scope(db, company.id, 'CONSOLIDATED'); db.commit()
    result = automated_valuation(db, company.id, 'CONSOLIDATED', AFTER.date())
    assert result['forward']['status'] == 'MODEL_ESTIMATE'
    assert Decimal(result['forward']['pe']) == 10
    assert len(result['forward']['forecast_revision_ids']) == 4
    assert not result['scenarios']  # No invented historical valuation range.
    assert automated_valuation(db, company.id, 'STANDALONE', AFTER.date())['forward']['status'] == 'INSUFFICIENT_EVIDENCE'


def test_forecast_api_scope_history_and_validation(db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db
    company, _ = forecast_history(db, monkeypatch)
    forecasting.generate_scope(db, company.id, 'CONSOLIDATED'); db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            base = '/api/companies/AUTO'
            result = client.get(base+'/forecasts?upcoming=true')
            assert result.status_code == 200, result.text
            assert result.json()['total'] == 8
            assert all(r['definition']['forecast']['classification'] == 'MODEL_ESTIMATE' for r in result.json()['items'])
            assert client.get(base+'/forecasts?scope=STANDALONE').json()['total'] == 0
            assert client.get(base+'/forecasts?scope=OTHER').status_code == 422
            assert client.get(base+'/forecasts?offset=-1').status_code == 422
            assert client.get(base+'/forecasts/accuracy').json()['minimum_n'] == 20
            assert client.get(base+'/valuation/automated').status_code == 200
            assert client.get(base+'/research/status').status_code == 200
            assert client.post(base+'/research/retry').status_code == 202
            assert client.get('/api/companies/MISSING/forecasts').status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_hindcast_rejects_late_corrections_and_short_horizons():
    rows = [{'value': str(100+i%4), 'available_at': '2026-01-01T00:00:00+00:00',
             'published_at': f'{2020+i//4}-01-01T00:00:00+00:00'} for i in range(12)]
    assert fit(rows, 1) is None
    with pytest.raises(ValueError, match='HORIZON'):
        fit(rows, 5)


def test_scheduler_persists_failures_and_restarts(db, monkeypatch):
    import asyncio
    from sqlalchemy.orm import sessionmaker
    from app.services import research_jobs
    company, _, _ = setup(db)
    ensure_jobs(db, company); db.commit()
    sessions = sessionmaker(bind=db.get_bind())
    async def fail(session, job):
        raise ValueError('PERMITTED_SOURCE_UNAVAILABLE')
    monkeypatch.setattr(research_jobs, 'execute', fail)
    assert asyncio.run(research_jobs.run_due(sessions, limit=1)) == 1
    db.expire_all()
    failed = db.scalar(select(ResearchJob).where(ResearchJob.status == 'RETRY'))
    assert failed and failed.error == 'PERMITTED_SOURCE_UNAVAILABLE'
    deadline = failed.next_run_at
    ensure_jobs(db, company); db.commit()
    assert db.get(ResearchJob, failed.id).next_run_at == deadline
    assert db.get(ResearchJob, failed.id).attempts == 1


@pytest.mark.parametrize('status,body,allowed', [
    (200, 'User-agent: *\nDisallow: /private', True),
    (200, 'User-agent: *\nDisallow: /', False),
    (403, '', False), (429, '', False), (503, '', False),
    (200, '<html>blocked</html>', False), (404, '', True),
])
def test_autonomous_access_policy_respects_robots(status, body, allowed):
    import asyncio
    import httpx
    from app.ingestion.access_policy import SourceAccessPolicy
    from app.ingestion.sources.base import SourceUnavailableError
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body))) as client:
            policy = SourceAccessPolicy(client)
            if allowed:
                await policy.check('https://example.org/filings.pdf')
            else:
                with pytest.raises(SourceUnavailableError):
                    await policy.check('https://example.org/filings.pdf')
    asyncio.run(run())


def test_disclosure_job_defers_commentary_and_retries_missing_documents(db, monkeypatch):
    import asyncio
    from app.config import settings
    from app.ingestion import monitor
    from app.ingestion.sources.base import SourceUnavailableError
    from app.services.research_jobs import execute
    company, _, _ = setup(db); ensure_jobs(db, company); db.commit()
    class FakeSource:
        name = 'NSE'
    monkeypatch.setattr(settings, 'event_source_mode', 'live')
    monkeypatch.setattr(monitor, 'configured_sources', lambda: [FakeSource()])
    async def collect(session, **kwargs):
        assert kwargs['defer_commentary'] and kwargs['symbols'] == {company.symbol}
        return monitor.CheckSummary(sources={'NSE': monitor.SourceRunSummary(status='success', document_failures=1)})
    monkeypatch.setattr(monitor, 'check_for_events', collect)
    job = db.scalar(select(ResearchJob).where(ResearchJob.kind == 'DISCLOSURES:NSE'))
    with pytest.raises(SourceUnavailableError, match='SOURCE_UNAVAILABLE'):
        asyncio.run(execute(db, job))
    assert db.scalar(select(ResearchJob).where(ResearchJob.kind == 'COMMENTARY')).status == 'PENDING'


def test_additive_job_migration_preserves_financial_rows(db):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    company, period, _ = setup(db); db.commit()
    ResearchJob.__table__.drop(db.get_bind())
    spec = importlib.util.spec_from_file_location('research_migration', Path(__file__).parents[1]/'alembic/versions/0021_research_jobs.py')
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    with db.get_bind().begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    assert db.get(FinancialPeriod, period.id).company_id == company.id
    ensure_jobs(db, company); db.commit()
    with db.get_bind().begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match='retained'):
                migration.downgrade()


@pytest.mark.skipif(__import__('os').getenv('ALMONDS_PG_RESEARCH_TEST') != '1', reason='Opt-in disposable PostgreSQL schema')
def test_postgres_job_migration_and_concurrent_claims():
    import importlib.util
    from pathlib import Path
    from uuid import uuid4
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.database import engine as local_engine
    schema = 'research_test_' + uuid4().hex
    with local_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(local_engine.url, connect_args={'options': f'-csearch_path={schema}'})
    try:
        Company.__table__.create(engine)
        spec = importlib.util.spec_from_file_location('pgresearch', Path(__file__).parents[1]/'alembic/versions/0021_research_jobs.py')
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        with Session(engine) as session:
            company = Company(symbol='JOB', name='Test only', exchange='NSE')
            session.add(company); session.flush()
            for kind in ('RESEARCH', 'COMMENTARY'):
                session.add(ResearchJob(company_id=company.id, kind=kind, next_run_at=CAPTURE))
            session.commit()
        barrier = Barrier(2)
        def run():
            with Session(engine) as session:
                barrier.wait(timeout=5)
                return claim(session, CAPTURE)
        with ThreadPoolExecutor(max_workers=2) as workers:
            tasks = [workers.submit(run) for _ in range(2)]
            a, b = [task.result(timeout=10) for task in tasks]
        assert a[0] != b[0]
        with Session(engine) as session:
            assert claim(session, CAPTURE) is None
            assert finish(session, *a, result={'done': True}, now=CAPTURE)
            assert not finish(session, *a, result={'late': True}, now=CAPTURE)
    finally:
        engine.dispose()
        with local_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


def test_forecast_evidence_prefetch_has_bounded_queries(db, monkeypatch):
    from sqlalchemy import event
    from app.services.expectation_evidence import financial_evidence_batch
    company, _ = forecast_history(db, monkeypatch)
    periods = list(db.scalars(select(FinancialPeriod).order_by(FinancialPeriod.period_end)))
    company_id = company.id
    queries = []
    def count(*args):
        queries.append(args[2])
    event.listen(db.get_bind(), 'before_cursor_execute', count)
    try:
        batch = financial_evidence_batch(db, periods)
        before = len(queries)
        for metric in forecasting.METRICS:
            forecasting.verified_history(db, company_id, 'CONSOLIDATED', metric, periods, AFTER, batch)
        assert len(queries) == before <= 6
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', count)


def test_provider_retry_after_is_persisted(db):
    from app.ingestion.retry_policy import retry_after_seconds
    company, _, _ = setup(db); ensure_jobs(db, company); db.commit()
    for job in db.scalars(select(ResearchJob)):
        job.next_run_at = CAPTURE
    db.commit()
    claimed = claim(db, CAPTURE)
    assert finish(db, *claimed, error='RATE_LIMITED', now=CAPTURE, retry_after_seconds=7200)
    db.expire_all()
    assert db.get(ResearchJob, claimed[0]).next_run_at.replace(tzinfo=timezone.utc) == CAPTURE+timedelta(hours=2)
    assert retry_after_seconds({'retry-after': '7200'}) == 7200
    assert retry_after_seconds({'retry-after': 'invalid'}) is None
    assert retry_after_seconds({'retry-after': 'Thu, 01 Jan 2026 01:00:00 GMT'}, datetime(2026,1,1,tzinfo=timezone.utc)) == 3600


def test_scheduler_startup_uses_one_persistent_worker(monkeypatch):
    import asyncio
    import app.main as main
    jobs = []
    class Scheduler:
        def __init__(self, **kwargs): pass
        def add_job(self, function, *args, **kwargs): jobs.append((function, kwargs))
        def start(self): pass
        def shutdown(self, **kwargs): pass
    monkeypatch.setattr(main, 'AsyncIOScheduler', Scheduler)
    monkeypatch.setattr(main.settings, 'research_automation_enabled', True)
    monkeypatch.setattr(main.settings, 'event_monitor_enabled', True)
    monkeypatch.setattr(main.settings, 'market_data_enabled', True)
    async def run():
        async with main.lifespan(main.app):
            assert len(jobs) == 1
            assert jobs[0][0] == main.scheduled_research
            assert jobs[0][1]['max_instances'] == 1
            assert jobs[0][1]['next_run_time'] is not None
    asyncio.run(run())
