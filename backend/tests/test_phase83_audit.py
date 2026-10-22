"""Audit regressions: real ingestion paths, synthetic test-only commitments."""
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest
from sqlalchemy import select, event, func
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_db
from app.models.checkpoint import InvestorCheckpoint
from app.models.commentary import ManagementStatement
from app.models.expectations import HistoricalExpectation, HistoricalExpectationRevision
from app.models.thesis import ThesisPillarRevision
from app.services.checkpoints import sync_checkpoints
from app.services.checkpoint_guidance import guidance_page
from app.services.expectation_statistics import calibration, statistics, thesis_history
from app.services.expectation_math import compare
from app.services import checkpoint_guidance, expectations as service
from app.services.thesis import save_pillar
from app.config import settings
from tests.test_phase83 import db, clock, setup, data, call, AFTER, math_definition, pillar

TEXT = ('CEO: We will see revenue coming in from the AI factory in Sydney from Q3 FY27.\n'
        'CEO: We target 1500 GPUs and will deploy those by Q4 FY27.\n'
        'CEO: We are targeting PAT margins in the range of 15% to 20%.\n'
        'CEO: The GPU revenue will start from Q3 FY27.\n'
        'CEO: We target additional INR 1500 crore of capex in FY27, but we do not have the numbers.')


def commitments(db, clock, monkeypatch):
    company, period, _ = setup(db)
    clock[0] = AFTER
    monkeypatch.setattr(checkpoint_guidance, 'utcnow', lambda: AFTER)
    call(db, company, period, TEXT)
    sync_checkpoints(db, period); db.commit()
    return company, period


def test_five_checkpoint_projection_preserves_evidence_and_is_idempotent(db, clock, monkeypatch):
    company, period = commitments(db, clock, monkeypatch)
    before = [(cp.id, cp.commitment, cp.target, cp.target_date) for cp in db.scalars(select(InvestorCheckpoint))]
    page = guidance_page(db, company.id, scope='CONSOLIDATED')
    assert page['total'] == page['checkpoint_total'] == 5 and page['ledger_total'] == 0
    assert not page['items']
    by_metric = {cp['metric']: cp for cp in page['checkpoint_items']}
    for cp in page['checkpoint_items']:
        assert cp['source_verified'] and cp['source']['source_quote'] == cp['quote']
        assert cp['source']['document_url'].endswith(f"#page={cp['source']['page']}")
        assert cp['published_at'].date() == date(2026, 7, 25)
        assert not cp['scoring_eligible']
    assert sum(cp['status'] == 'PENDING' for cp in page['checkpoint_items']) == 3
    assert by_metric['PAT_MARGIN']['target_date'] is None
    assert 'No explicit deadline' in by_metric['PAT_MARGIN']['reason']
    assert by_metric['CAPEX_SCOPE_UNCLEAR']['status'] == 'UNRESOLVED'
    revenue = [cp for cp in page['checkpoint_items'] if cp['metric'] == 'REVENUE_START']
    assert len(revenue) == 2 and revenue[0]['id'] in revenue[1]['possible_duplicate_ids']
    for _ in range(2):
        sync_checkpoints(db, period); service.refresh_expectations(db, company.id)
        assert guidance_page(db, company.id) == page
    assert [(cp.id, cp.commitment, cp.target, cp.target_date) for cp in db.scalars(select(InvestorCheckpoint))] == before
    assert statistics(db, company.id)['total'] == 0
    assert not guidance_page(db, company.id, scope='STANDALONE')['checkpoint_items']


def test_missing_provenance_is_visible_unresolved_not_backfilled(db, clock, monkeypatch):
    company, _ = commitments(db, clock, monkeypatch)
    statement = db.scalar(select(ManagementStatement))
    statement.extraction_confidence = 'LOW'; db.commit()
    cp = next(r for r in guidance_page(db, company.id)['checkpoint_items'] if r['statement_id'] == statement.id)
    assert cp['status'] == 'UNRESOLVED' and cp['source'] is None and not cp['source_verified']
    assert 'provenance' in cp['reason']
    assert db.scalar(select(func.count()).select_from(HistoricalExpectation)) == 0


def test_guidance_pagination_scope_schema_and_constant_query_count(db, clock, monkeypatch):
    company, _ = commitments(db, clock, monkeypatch)
    company_id = company.id
    queries = []
    def count(*args): queries.append(args[2])
    event.listen(db.get_bind(), 'before_cursor_execute', count)
    try:
        db.expunge_all(); guidance_page(db, company_id, limit=1)
        one = len(queries)
        queries.clear(); db.expunge_all(); guidance_page(db, company_id, limit=5)
        assert len(queries) == one
    finally:
        event.remove(db.get_bind(), 'before_cursor_execute', count)
    def override(): yield db
    app.dependency_overrides[get_db] = override
    try:
        with TestClient(app) as client:
            pages = [client.get(f'/api/companies/ACCURACY/accuracy/guidance?limit=2&offset={i}').json() for i in (0,2,4)]
            ids = [r['id'] for p in pages for r in p['checkpoint_items']]
            assert len(set(ids)) == len(ids) == 5 and pages[0]['total'] == 5
            assert client.get('/api/companies/ACCURACY/accuracy/guidance?scope=STANDALONE').json()['total'] == 0
            assert client.get('/api/companies/ACCURACY/accuracy/guidance?offset=-1').status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_registered_checkpoint_is_not_displayed_twice(db, clock):
    company, period, _ = setup(db); clock[0] = AFTER
    call(db, company, period, 'CEO: We target PAT margins of 15% to 20% for FY27.')
    page = guidance_page(db, company.id)
    assert page['total'] == page['ledger_total'] == 1 and page['checkpoint_total'] == 0
    assert page['items'][0]['definition']['checkpoint_id']


@pytest.mark.parametrize('expected,outcome', [('105','MET'),('105.000001','PARTIALLY_MET'),('110','PARTIALLY_MET'),('110.000001','MISSED')])
def test_exact_boundaries(expected, outcome):
    assert compare(math_definition(expected_value=expected), '100')['outcome'] == outcome


def test_frozen_policy_survives_configuration_change(db, clock, monkeypatch):
    company, _, _ = setup(db)
    ledger, revision = service.save(db, company.id, data(expected_value='108'))
    frozen = dict(revision.definition['tolerance'])
    monkeypatch.setattr(settings, 'expectation_met_tolerance_pct', 50)
    clock[0] = AFTER
    assert service.evaluate(db, company.id, ledger.id).outcome == 'PARTIALLY_MET'
    assert revision.definition['tolerance'] == frozen


def test_assessment_states_and_denominators(db, clock):
    company, _, _ = setup(db)
    pending, _ = service.save(db, company.id, data(period='2027-06-30', cutoff_at='2027-07-15T00:00:00Z'))
    missing, _ = service.save(db, company.id, data(metric='PAT'))
    met, _ = service.save(db, company.id, data())
    clock[0] = AFTER
    imported, _ = service.save(db, company.id, data(historical_import=True, source_reference='Imported note'))
    for ledger in (pending, missing, met, imported): service.evaluate(db, company.id, ledger.id)
    states = {r['id']: r['assessment_status'] for r in service.list_expectations(db, company.id)['items']}
    assert states == {pending.id:'PENDING', missing.id:'UNRESOLVED', met.id:'MET', imported.id:'EXCLUDED'}
    stats = statistics(db, company.id)
    assert stats['evaluated'] == 1 and stats['pending'] == stats['excluded'] == stats['unresolved_evidence'] == 1
    assert stats['policy']['minimum_n'] == 20 and stats['groups'][0]['met_rate'] is None


def test_calibration_defensively_excludes_ineligible_and_imported():
    def pair(probability, outcome, eligible=True, imported=False):
        return (SimpleNamespace(definition=dict(confidence=probability, confidence_event='MET_UNDER_FROZEN_TOLERANCE',historical_import=imported)),
                SimpleNamespace(outcome=outcome, prospective_eligible=eligible))
    valid = [pair('0.8','MET'), pair('0.4','MISSED')]
    extra = [pair('1','MET',False), pair('1','MET',True,True),pair('0.1','INSUFFICIENT_EVIDENCE')]
    result = calibration(valid + extra, 20)
    assert result['n'] == 2 and result['brier'] is None
    assert sum(b['n'] for b in result['buckets']) == 2
    assert Decimal(calibration(valid + extra, 2)['brier']) == Decimal('0.10')


def test_thesis_edit_does_not_relink_expectation(db, clock):
    company, _, _ = setup(db)
    save_pillar(db, company.id, pillar(), today=date(2026,7,1)); db.commit()
    thesis = db.scalar(select(ThesisPillarRevision))
    clock[0] = datetime.now(timezone.utc)
    ledger, revision = service.save(db, company.id, data(period='2027-06-30',cutoff_at='2027-07-15T00:00:00Z',thesis_revision_id=thesis.id))
    save_pillar(db, company.id, pillar(expected_version=1).model_copy(update={'name':'Changed thesis'}), pillar_id=thesis.pillar_id, today=date(2026,7,1))
    db.commit()
    assert db.get(HistoricalExpectationRevision, revision.id).thesis_revision_id == thesis.id
    assert thesis_history(db,company.id)['items'][0]['original_thesis']['name'] != 'Changed thesis'


def test_undated_qualitative_outcome_cannot_enter_accuracy(db, clock, monkeypatch):
    company, _ = commitments(db, clock, monkeypatch)
    cp = db.scalar(select(InvestorCheckpoint).where(InvestorCheckpoint.metric == 'GPU_DEPLOYMENT'))
    ledger, revision = service.save(db, company.id, data(metric='BUSINESS_MILESTONE', expectation_type='QUALITATIVE',
        period=cp.target_date, cutoff_at='2027-04-01T00:00:00Z', unit='EVENT',currency='NONE', expected_value=None,
        evidence_id=f'S{cp.statement_id}', checkpoint_id=cp.id))
    clock[0] = datetime(2027,5,1,tzinfo=timezone.utc)
    monkeypatch.setattr(service, 'collect_evidence', lambda *_: (None, None, {
        f'K{cp.id}': {'status':'DELIVERED','actual_outcome':{'evidence_ids':['M1']}},
        'M1': {'kind':'METRIC','value':'1500'}}))
    evaluation = service.evaluate(db,company.id,ledger.id)
    assert evaluation.outcome == 'INSUFFICIENT_EVIDENCE'
    assert statistics(db,company.id)['evaluated'] == 0


def test_mixed_guidance_pagination_and_overlap_flags(db, clock, monkeypatch):
    company, period = commitments(db, clock, monkeypatch)
    call(db,company,period,'CEO: We target PAT margins of 15% to 20% for FY27.',published='2026-07-26')
    call(db,company,period,'CEO: We target PAT margins of 20% to 25% for FY27.',published='2026-07-27')
    pages = [guidance_page(db,company.id,offset=i,limit=3) for i in (0,3,6)]
    assert all(page['total']==7 for page in pages)
    ids = [f"E{r['id']}" for page in pages for r in page['items']] + [r['id'] for page in pages for r in page['checkpoint_items']]
    assert len(ids)==len(set(ids))==7
    assert len(pages[0]['items'])==2 and len(pages[0]['checkpoint_items'])==1
    assert all(len(r['possible_duplicate_ids'])==1 for r in pages[0]['items'])


def test_thesis_history_query_count_does_not_grow_per_version(db, clock):
    company, _, _ = setup(db)
    for i in range(3):
        save_pillar(db,company.id,pillar(),today=date(2026,7,1)); db.flush()
        thesis = db.scalar(select(ThesisPillarRevision).order_by(ThesisPillarRevision.id.desc()))
        clock[0] = datetime.now(timezone.utc)
        service.save(db,company.id,data(period='2027-06-30',cutoff_at='2027-07-15T00:00:00Z',thesis_revision_id=thesis.id))
    db.commit(); company_id=company.id
    queries=[]
    def count(*args): queries.append(args[2])
    event.listen(db.get_bind(),'before_cursor_execute',count)
    try:
        db.expunge_all(); thesis_history(db,company_id,limit=1)
        one=len(queries)
        queries.clear(); db.expunge_all()
        assert len(thesis_history(db,company_id,limit=3)['items'])==3
        assert len(queries)==one
    finally:
        event.remove(db.get_bind(),'before_cursor_execute',count)
