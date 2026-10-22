"""Durable work queue driven by the existing APScheduler, with fenced leases."""
import asyncio
import logging
from datetime import timedelta
from uuid import uuid4
from sqlalchemy import select, update, or_, text
from sqlalchemy.orm import aliased
from app.config import settings
from app.models.company import Company
from app.models.event import CompanySource
from app.models.research import ResearchJob
from app.services.expectation_evidence import utc, utcnow
from app.services.thesis import lock_company

logger = logging.getLogger(__name__)
GAPS = [
    'Public analyst consensus: no permitted automatic provider configured; no estimates invented.',
    'Sector/peer benchmarks: no verified peer dataset configured; issuer sector metadata only.',
    'Independent news: official announcements are monitored; no general-news provider configured.',
    'Historical exchange RSS is bounded; missing older filings require a supported IR archive.',
    'Result calendar: no verified future announcement calendar; forecasts are captured before quarter-end.',
    'Models currently support quarterly history; annual-only histories remain insufficient evidence.',
]


def ensure_jobs(db, company):
    lock_company(db, company.id)
    kinds = ['RESEARCH', 'MARKET', 'COMMENTARY', 'ACCURACY']
    if 'NSE' in company.exchange.upper():
        kinds.append('DISCLOSURES:NSE')
    if company.symbol == 'ESDS' and company.name.upper().startswith('ESDS'):
        from app.ingestion.sources.esds_ir import IR_URL, IR_COMPANY_ID
        mapping = db.scalar(select(CompanySource).where(CompanySource.company_id == company.id, CompanySource.source == 'ESDS_IR'))
        if mapping is None:
            db.add(CompanySource(company_id=company.id, source='ESDS_IR', external_symbol=company.symbol,
                ir_url=IR_URL, source_company_id=IR_COMPANY_ID, enabled=True))
        kinds.append('DISCLOSURES:ESDS_IR')
    mappings = list(db.scalars(select(CompanySource).where(CompanySource.company_id == company.id)))
    kinds.extend('DISCLOSURES:' + m.source for m in mappings if m.source in {'NSE','BSE','ESDS_IR'})
    existing = {r.kind for r in db.scalars(select(ResearchJob).where(ResearchJob.company_id == company.id))}
    for kind in sorted(set(kinds) - existing):
        db.add(ResearchJob(company_id=company.id,kind=kind,status='PENDING',next_run_at=utcnow(),attempts=0,failures=0,result={}))
    db.flush()


def enqueue_research(db, company_id):
    """Evidence hooks coalesce repeated work; do not reset a running lease."""
    db.execute(update(ResearchJob).execution_options(synchronize_session="fetch").where(ResearchJob.company_id == company_id, ResearchJob.kind == 'RESEARCH',
        ResearchJob.status != 'RUNNING').values(next_run_at=utcnow(),status='PENDING'))


def enqueue_accuracy(db, company_id):
    """Coalesced deterministic scoring on the existing queue, without generation."""
    lock_company(db, company_id)
    job = db.scalar(select(ResearchJob).where(ResearchJob.company_id == company_id, ResearchJob.kind == 'ACCURACY'))
    if job is None:
        job = ResearchJob(company_id=company_id, kind='ACCURACY', status='PENDING', next_run_at=utcnow(),
                          attempts=0, failures=0, result={})
        db.add(job)
    elif job.status not in {'RUNNING', 'RETRY'}:
        job.status = 'PENDING'
        job.next_run_at = utcnow()
    db.flush()
    return job


def claim(db, now=None, company_id=None, kinds=None):
    now = now or utcnow()
    if db.get_bind().dialect.name == 'postgresql':
        # Serialize only the tiny claim transaction so separate workers cannot
        # race the provider cooldown predicate. Network work holds no such lock.
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext('almonds.research.claim'))"))
    available = (ResearchJob.next_run_at <= now) & or_(ResearchJob.lease_until.is_(None), ResearchJob.lease_until <= now)
    if company_id is not None:
        available &= ResearchJob.company_id == company_id
    if kinds is not None:
        available &= ResearchJob.kind.in_(kinds)
    peer = aliased(ResearchJob)
    provider_busy = select(peer.id).where(peer.kind == ResearchJob.kind, peer.id != ResearchJob.id,
        or_(peer.lease_until > now, peer.last_attempt_at > now-timedelta(seconds=30))).exists()
    available &= (~ResearchJob.kind.in_(['MARKET', 'DISCLOSURES:NSE', 'DISCLOSURES:ESDS_IR']) | ~provider_busy)
    job = db.scalar(select(ResearchJob).where(available).order_by(ResearchJob.next_run_at,ResearchJob.id).with_for_update(skip_locked=True).limit(1))
    if not job:
        db.rollback()
        return None
    token = uuid4().hex
    changed = db.execute(update(ResearchJob).execution_options(synchronize_session="fetch").where(ResearchJob.id == job.id, available).values(
        status='RUNNING',lease_token=token,lease_until=now+timedelta(hours=1),last_attempt_at=now,attempts=ResearchJob.attempts+1))
    job_id = job.id
    db.commit()
    return (job_id, token) if changed.rowcount == 1 else None


def finish(db, job_id, token, result=None, error=None, now=None, retry_after_seconds=None):
    now = now or utcnow()
    job = db.get(ResearchJob, job_id)
    if not job or job.lease_token != token:
        return False
    failures = job.failures + 1 if error else 0
    minutes = min(1440, 5 * 2 ** min(failures - 1, 8)) if error else (1440 if job.kind == 'MARKET' else 60)
    if error and retry_after_seconds:
        minutes = max(minutes, retry_after_seconds / 60)
    values = dict(status='RETRY' if error else 'SUCCEEDED',failures=failures,error=error,
        next_run_at=now+timedelta(minutes=minutes),lease_until=None,lease_token=None)
    if result is not None:
        values['result'] = result
    if not error:
        values['last_success_at'] = now
    changed = db.execute(update(ResearchJob).execution_options(synchronize_session="fetch").where(ResearchJob.id==job_id,ResearchJob.lease_token==token).values(**values))
    db.commit()
    return changed.rowcount == 1


async def execute(db, job):
    company = db.get(Company,job.company_id)
    if job.kind == 'ACCURACY':
        from app.services.expectations import refresh_expectations
        count = refresh_expectations(db, company.id)
        if count is None:
            raise ValueError('EXPECTATION_EVALUATION_RETRY_REQUIRED')
        return {'processed': count}
    if job.kind.startswith('DISCLOSURES:'):
        if settings.event_source_mode != 'live':
            raise ValueError('LIVE_SOURCE_MODE_DISABLED; synthetic source ingestion is never scheduled')
        from app.ingestion.monitor import check_for_events, configured_sources
        source = next((s for s in configured_sources() if s.name == job.kind.split(':',1)[1]),None)
        if not source:
            raise ValueError('SOURCE_DISABLED_OR_UNSUPPORTED')
        from app.ingestion.sources.esds_ir import ESDSIRSource
        if source.name == 'ESDS_IR' and not job.last_success_at:
            source = ESDSIRSource(bootstrap_since=utcnow()-timedelta(days=1095))
        from app.ingestion.access_policy import SourceAccessPolicy
        source.access_policy = SourceAccessPolicy()
        if hasattr(source, 'downloader'):
            source.downloader.access_policy = source.access_policy
        # Each company has its own cursor; a global source watermark must not skip
        # an issuer added later or let one issuer consume another issuer's history.
        since = utc(job.last_success_at)-timedelta(days=7) if job.last_success_at else utcnow()-timedelta(days=1095)
        result = (await check_for_events(db,sources=[source],symbols={company.symbol},since_override=since,
            defer_commentary=True)).model_dump()
        details = result['sources'].get(source.name,{})
        enqueue_research(db,company.id)
        db.execute(update(ResearchJob).where(ResearchJob.company_id == company.id,
            ResearchJob.kind == 'COMMENTARY', ResearchJob.status != 'RUNNING').values(next_run_at=utcnow(), status='PENDING'))
        db.commit()
        if result['errors'] or details.get('status') != 'success' or details.get('unresolved_symbols') or details.get('document_failures') or details.get('processing_failures'):
            from app.ingestion.sources.base import SourceUnavailableError
            raise SourceUnavailableError('SOURCE_UNAVAILABLE: ' + str(result), details.get('retry_after_seconds'))
        return result
    if job.kind == 'COMMENTARY':
        from app.services.commentary import refresh_commentary_for_company
        result = await asyncio.to_thread(refresh_commentary_for_company, db.get_bind(), company.id, index_memory=False)
        enqueue_research(db,company.id); db.commit()
        return result
    if job.kind == 'MARKET':
        from app.market.service import refresh_market
        from app.models.financial import FinancialScope
        from app.market.valuation import recalculate
        if settings.market_data_provider != 'nse_mcp':
            raise ValueError('FREE_AUTOMATIC_MARKET_PROVIDER_UNAVAILABLE; retained dated observations remain visible')
        records = refresh_market(db,company)
        for record in records[-1:]:
            for scope in FinancialScope:
                recalculate(db,record,scope)
        enqueue_research(db,company.id); db.commit()
        return {'observations':len(records),'latest_date':str(records[-1].as_of_date) if records else None}
    if job.kind == 'RESEARCH':
        from app.services.forecasting import generate_company
        from app.services.expectations import refresh_expectations
        from app.services.autonomous_valuation import automated_valuation
        from app.services.research_dependencies import input_hash
        fingerprint = input_hash(db, company.id)
        if (job.result or {}).get('input_hash') == fingerprint:
            return {**job.result, 'unchanged': True}
        result = {**generate_company(db,company.id), 'input_hash': fingerprint,
            'assessed_at': utcnow().isoformat(), 'unchanged': False}
        db.commit()
        if refresh_expectations(db,company.id) is None:
            raise ValueError('EXPECTATION_EVALUATION_RETRY_REQUIRED')
        result['valuation'] = {scope: automated_valuation(db, company.id, scope)
            for scope in ('CONSOLIDATED', 'STANDALONE')}
        return result
    raise ValueError('UNKNOWN_RESEARCH_JOB')


async def run_due(session_factory, limit=4, company_id=None, kinds=None):
    with session_factory() as db:
        query = select(Company).order_by(Company.id)
        if company_id is not None:
            query = query.where(Company.id == company_id)
        for company in db.scalars(query):
            ensure_jobs(db,company)
        db.commit()
    processed = 0
    for _ in range(limit):
        with session_factory() as db:
            claimed = claim(db, company_id=company_id, kinds=kinds)
        if not claimed:
            break
        job_id, token = claimed
        with session_factory() as db:
            try:
                result = await execute(db,db.get(ResearchJob,job_id))
                finish(db,job_id,token,result=result)
            except Exception as exc:
                db.rollback()
                finish(db,job_id,token,error=str(exc)[:2000] or type(exc).__name__,
                    retry_after_seconds=getattr(exc, 'retry_after_seconds', None))
                logger.warning('research.job_failed id=%s error=%s',job_id,type(exc).__name__)
        processed += 1
    return processed


def status(db, company_id):
    jobs = list(db.scalars(select(ResearchJob).where(ResearchJob.company_id==company_id).order_by(ResearchJob.kind)))
    return {'enabled':settings.research_automation_enabled,'poll_seconds':settings.research_poll_seconds,
        'gaps':GAPS,'jobs':[{'id':j.id,'kind':j.kind,'status':j.status,'next_run_at':j.next_run_at,
            'last_attempt_at':j.last_attempt_at,'last_success_at':j.last_success_at,'attempts':j.attempts,
            'failures':j.failures,'error':j.error,'result':j.result} for j in jobs]}
