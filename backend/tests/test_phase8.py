"""Phase 8 uses synthetic unit records only; live corpus regression is opt-in."""
import copy
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError
from tests.test_phase71 import db, issuer, result_period
from tests.test_phase72 import call
from tests.test_financials import financial_env
from tests.test_ai_analyst import setup as analysis_setup
from app.ai.analyst.context_builder import build_context, _fact_item
from app.ai.analyst.service import analyze_period
from app.models.analysis import AIAnalysis
from app.models.financial import FinancialFact, FinancialScope, FinancialUnit, MetricCode, FactMethod, FactStatus
from app.models.thesis import ThesisPillar, ThesisPillarRevision, ThesisPillarEvaluation, ThesisEvaluationRun, ThesisExpectationRevision
from app.schemas.thesis import PillarInput, ExpectationInput, PillarAssessment
from app.services.thesis import save_pillar, save_expectation, thesis_view, bootstrap
from app.services.thesis_evidence import analysis_evidence, expectation_gap
from app.services.thesis_evaluation import evaluate_thesis, validate_assessment, deterministic_assessment

D1 = date(2026,8,1)
D2 = date(2026,11,1)


def saved(db, company, period, value='100', *, registry=None, report=None, created=D1):
    fact = FinancialFact(financial_period_id=period.id, metric_code=MetricCode.REVENUE_FROM_OPERATIONS,
        value=Decimal(value), unit=FinancialUnit.INR_CRORE, source_document_id=period.source_document_id,
        source_page=3, source_label='Revenue', source_column_date=period.period_end,
        extraction_method=FactMethod.PDF_TABLE, status=FactStatus.VERIFIED_PDF)
    db.add(fact); db.flush()
    evidence = {f'F{fact.id}': _fact_item(fact,period), **(registry or {})}
    row = AIAnalysis(company_id=company.id,financial_period_id=period.id,model_provider='fixture',model_name='test',
        prompt_version='phase8-test',context_hash='x'*64,context_chars=100,evidence_registry=evidence,
        status='SUCCESS', structured_output={'investor_report': report or {}},
        created_at=datetime.combine(created,datetime.min.time(),tzinfo=timezone.utc))
    db.add(row); db.flush()
    return row, f'F{fact.id}'


@pytest.fixture
def env(db):
    company=issuer(db,'GENERIC')
    period=result_period(db,company,date(2026,6,30),'2026-07-20')
    analysis,key=saved(db,company,period)
    return db,company,period,analysis,key


def pillar(**kwargs):
    return PillarInput(name='Revenue execution', category='GROWTH', description='Revenue must meet the investment requirement.',
        importance='CORE', metric_codes=['REVENUE_FROM_OPERATIONS'], **kwargs)


def requirement(period, target='90'):
    return ExpectationInput(description='Verified revenue must meet the stated requirement.', expectation_type='METRIC',
        metric_code='REVENUE_FROM_OPERATIONS', operator='GTE', target_value=target, unit='INR_CRORE',
        target_period_end=period.period_end,period_type='QUARTERLY')


class Provider:
    name='fake'; model='test'
    def __init__(self, rows): self.rows=rows; self.calls=0
    def generate(self, system,user,schema):
        self.calls+=1
        assert 'private reasoning' in system and 'scope' in json.loads(user)
        return json.dumps({'evaluations':self.rows})


def proposed(pid,key,**kwargs):
    return {'pillar_id':pid,'proposed_status':'STRENGTHENING','summary':'Verified revenue meets the defined investment requirement.',
            'evidence_ids':[key],'basis':'INTERPRETATION','materiality':'MATERIAL',**kwargs}


def test_creation_provenance_importance_edits_and_historical_reconstruction(env):
    db,c,p,a,key=env
    item=save_pillar(db,c.id,pillar(),today=D1)
    assert thesis_view(db,c.id,as_of=date(2026,7,31))['pillars']==[]
    initial=thesis_view(db,c.id,as_of=D1)['pillars'][0]
    assert (initial['created_from'],initial['importance'],initial['status'])==('USER','CORE','UNPROVEN')
    revised=pillar().model_copy(update={'description':'The revised thesis requires durable cash-backed revenue.','expected_version':1,'is_active':False,'importance':'SECONDARY','reason':'Investor requirement changed'})
    save_pillar(db,c.id,revised,item.id,today=D2)
    assert thesis_view(db,c.id,as_of=D1)['pillars'][0]['description']==initial['description']
    latest=thesis_view(db,c.id,as_of=D2)
    assert not latest['pillars'][0]['is_active'] and latest['current_investment_case']=={}
    assert len(latest['history'])==2
    with pytest.raises(ValueError,match='VERSION_CONFLICT'): save_pillar(db,c.id,revised,item.id,today=D2)


def test_company_scope_isolation_and_inferred_evidence(env):
    db,c,p,a,key=env
    other=issuer(db,'OTHER')
    with pytest.raises(ValueError,match='REQUIRES_EVIDENCE'): save_pillar(db,c.id,pillar(created_from='SYSTEM_INFERRED'),today=D1)
    item=save_pillar(db,c.id,pillar(created_from='RESEARCH_DERIVED',source_analysis_id=a.id,evidence_ids=[key]),today=D1)
    assert thesis_view(db,c.id,as_of=D1)['pillars'][0]['source_evidence'][key]['page']==3
    assert thesis_view(db,other.id,as_of=D1)['pillars']==[]
    assert thesis_view(db,c.id,'STANDALONE',D1)['pillars']==[]
    with pytest.raises(ValueError,match='COMPANY_MISMATCH'): save_expectation(db,other.id,item.id,requirement(p),today=D1)
    with pytest.raises(ValueError,match='COMPANY_MISMATCH'): evaluate_thesis(db,other.id,a.id,today=D1,use_ai=False)
    with pytest.raises(ValueError,match='INVALID_THESIS_EVIDENCE'): save_pillar(db,c.id,pillar(scope='STANDALONE',source_analysis_id=a.id,evidence_ids=[key]),today=D1)


@pytest.mark.parametrize('mode',['orm','sql_update','sql_delete'])
def test_history_immutable_in_orm_and_database(env,mode):
    db,c,p,a,key=env
    item=save_pillar(db,c.id,pillar(),today=D1);db.commit()
    with pytest.raises((ValueError,DBAPIError),match='THESIS_HISTORY_IS_IMMUTABLE'):
        if mode=='orm':
            row=db.scalar(select(ThesisPillarRevision));row.reason='erase history';db.flush()
        else: db.execute(text('UPDATE thesis_pillar_revisions SET reason=\'erase\'' if mode=='sql_update' else 'DELETE FROM thesis_pillar_revisions'))
    db.rollback()
    assert thesis_view(db,c.id,as_of=D1)['pillars'][0]['status']=='UNPROVEN'


def test_verified_transition_persistence_idempotence_and_expectation_revision(env):
    db,c,p,a,key=env
    item=save_pillar(db,c.id,pillar(expectations=[requirement(p)]),today=D1)
    first=evaluate_thesis(db,c.id,a.id,today=D1,use_ai=False)
    assert first['impacts'][0]['new_status']=='STRENGTHENING'
    assert first['evidence'][key]['page']==3
    assert evaluate_thesis(db,c.id,a.id,today=D1,use_ai=False)['run_id']==first['run_id']
    view=thesis_view(db,c.id,as_of=D1); test=view['pillars'][0]['expectations'][0]
    changed=requirement(p,'120').model_copy(update={'expected_version':1,'reason':'Higher investor requirement'})
    save_expectation(db,c.id,item.id,changed,test['expectation_key'],today=D2)
    assert thesis_view(db,c.id,as_of=D2)['pillars'][0]['expectations'][0]['outcome'] is None
    second=evaluate_thesis(db,c.id,a.id,today=D2,use_ai=False)
    assert second['impacts'][0]['new_status']=='WEAKENING' and second['impacts'][0]['contradiction']
    assert thesis_view(db,c.id,as_of=D1)['pillars'][0]['status']=='STRENGTHENING'
    assert len([h for h in thesis_view(db,c.id,as_of=D2)['history'] if h['kind']=='EVALUATED'])==2


@pytest.mark.parametrize('change,reason',[
    ({'evidence_ids':['UNKNOWN']},'EVIDENCE_DOES_NOT_TEST'),
    ({'evidence_ids':[]},'at least 1'),
    ({'materiality':'NOT_MATERIAL'},'NO_MATERIAL_EVIDENCE'),
    ({'proposed_status':'BROKEN','contradiction':True},'DIRECT_CONTRADICTION'),
    ({'summary':'Revenue reached 9999 crore.'},'UNSUPPORTED_NUMBER'),
    ({'summary':'Buy the shares immediately.'},'RECOMMENDATION_FORBIDDEN'),
])
def test_invalid_model_rows_fall_back_without_losing_supported_rows(env,change,reason):
    db,c,p,a,key=env
    one=save_pillar(db,c.id,pillar(expectations=[requirement(p)]),today=D1)
    two=save_pillar(db,c.id,pillar(expectations=[requirement(p)]),today=D1)
    provider=Provider([proposed(one.id,key,**change),proposed(two.id,key)])
    result=evaluate_thesis(db,c.id,a.id,provider=provider,today=D1)
    assert result['impacts'][0]['method']=='DETERMINISTIC'
    assert result['impacts'][1]['method']=='QWEN_VALIDATED'
    assert any(reason in str(r) for r in result['diagnostics'])


def test_no_measurable_delivery_no_strengthening_and_no_unrelated_evaluation(env):
    db,c,p,a,key=env
    related=save_pillar(db,c.id,pillar(),today=D1)
    unrelated=save_pillar(db,c.id,PillarInput(name='Customer quality',category='BUSINESS_QUALITY',description='Customers must remain diversified.'),today=D1)
    result=evaluate_thesis(db,c.id,a.id,provider=Provider([proposed(related.id,key)]),today=D1)
    assert [i['pillar_id'] for i in result['impacts']]==[related.id]
    assert result['impacts'][0]['new_status']=='UNPROVEN'
    assert result['diagnostics'][0]['reason']=='VERIFIED_DELIVERY_REQUIRED'


def test_single_miss_not_broken_but_repeated_core_failures_can_be(env):
    db,c,p,a,key=env
    item=save_pillar(db,c.id,pillar(expectations=[requirement(p,'120')]),today=D1)
    first=evaluate_thesis(db,c.id,a.id,provider=Provider([proposed(item.id,key,proposed_status='BROKEN',contradiction=True,summary='Verified outcomes contradict the core investment requirement.')]),today=D1)
    assert first['impacts'][0]['new_status']=='WEAKENING'
    assert any('REPEATED_DIRECT_FAILURE' in str(d) for d in first['diagnostics'])
    nextp=result_period(db,c,date(2026,9,30),'2026-10-20',quarter=2)
    nexta,nextkey=saved(db,c,nextp,created=D2)
    save_expectation(db,c.id,item.id,requirement(nextp,'120'),today=D2)
    result=evaluate_thesis(db,c.id,nexta.id,provider=Provider([proposed(item.id,nextkey,proposed_status='BROKEN',contradiction=True,summary='Repeated verified outcomes contradict the core investment requirement.')]),today=D2)
    assert result['impacts'][0]['new_status']=='BROKEN'
    assert result['impacts'][0]['method']=='QWEN_VALIDATED'
    with pytest.raises(ValueError,match='OLDER_REVIEW'): evaluate_thesis(db,c.id,a.id,today=D2,use_ai=False)


@pytest.mark.parametrize('failures,expected',[(0,'ON_TRACK'),(1,'WEAKENING')])
def test_repeated_delays_affect_execution_not_first_delay(failures,expected):
    output=deterministic_assessment('ON_TRACK',[{'status':'DELAYED','evidence_ids':['K1']}],['K1'],'EXECUTION',{},failures)
    assert output['new_status']==expected


@pytest.mark.parametrize('required,observed,expected',[(15,20,'LOW'),(20,15,'MODERATE'),(30,15,'HIGH'),(50,15,'EXTREME'),(0,0,'LOW')])
def test_gap_uses_authoritative_same_horizon_cagr(required,observed,expected):
    report={'tested_pe':{'market_date':'2026-07-20','scenarios':[{'name':'BASE','years':3}],
        'reverse_expectations':{'required_eps_cagr_pct':required},
        'growth_comparisons':[{'metric':'EPS_CAGR','years':3,'value':str(observed),'evidence_ids':['F1']}]}}
    before=copy.deepcopy(report)
    gap=expectation_gap(report,{'A_BASE':{},'F1':{}},D1)
    assert gap['classification']==expected and report==before and gap['assumption_not_forecast']


@pytest.mark.parametrize('metric,years',[('EPS_YOY',3),('EPS_CAGR',1)])
def test_gap_horizon_mismatch(metric,years):
    report={'tested_pe':{'market_date':'2026-07-20','scenarios':[{'name':'BASE','years':3}],
        'reverse_expectations':{'required_eps_cagr_pct':'30'},
        'growth_comparisons':[{'metric':metric,'years':years,'value':'100','evidence_ids':['F1']}]}}
    result=expectation_gap(report,{'A_BASE':{},'F1':{}},D1)
    assert result['classification']=='INSUFFICIENT_EVIDENCE' and 'Horizon mismatch' in result['reason']


def test_price_scenario_alone_never_proves_thesis(env):
    db,c,p,a,key=env
    item=PillarAssessment.model_validate(proposed(1,'A_BASE'))
    with pytest.raises(ValueError,match='PRICE_OR_SCENARIO'):
        validate_assessment(item,pillar().model_dump(),'UNPROVEN',[],['A_BASE'],{'A_BASE':{'kind':'SCENARIO'}},p,0)


def test_future_foreign_scope_facts_and_memory_cannot_leak(env):
    db,c,p,a,key=env
    other=issuer(db,'SECOND')
    op=result_period(db,other,date(2026,6,30),'2026-07-20')
    oa,ok=saved(db,other,op)
    sp=result_period(db,c,date(2026,6,30),'2026-07-20',scope=FinancialScope.STANDALONE)
    sa,sk=saved(db,c,sp)
    future=result_period(db,c,date(2026,9,30),'2026-10-20',quarter=2)
    fa,fk=saved(db,c,future,created=D2)
    a.evidence_registry={**a.evidence_registry,**oa.evidence_registry,**sa.evidence_registry,**fa.evidence_registry,
        'M99':{'kind':'METRIC','period_id':p.id,'input_evidence_ids':[fk]},
        'C99':{'kind':'MEMORY_CHUNK','document_id':future.source_document_id,'published_at':'2026-07-01','scope':'CONSOLIDATED'}}
    db.flush()
    filtered=analysis_evidence(db,c.id,a.id,D1)[3]
    assert set(filtered)=={key}
    with pytest.raises(ValueError,match='NOT_AVAILABLE_AS_OF'):analysis_evidence(db,c.id,fa.id,D1)
    with pytest.raises(ValueError,match='NOT_AVAILABLE_AS_OF'):analysis_evidence(db,c.id,a.id,date(2026,7,1))


def test_checkpoint_bootstrap_claims_remain_unproven_with_research_gaps(db):
    c=issuer(db,'EXECUTION')
    p=result_period(db,c,date(2026,6,30),'2026-07-20')
    call(db,c,p,'CEO: We target 200 GPUs and will deploy those by Q3 FY27.\nCFO: We are targeting PAT margins in the range of 15% to 20%.')
    a,key=saved(db,c,p)
    context=build_context(db,c.name,c.symbol,p)
    a.evidence_registry=context.registry; a.structured_output={'investor_report':context.investor_report}; db.flush()
    result=bootstrap(db,c.id,a.id,today=D1)
    assert db.scalar(select(func.count()).select_from(ThesisPillar))==0
    assert len(result['proposals'])==2
    for proposal in result['proposals']:
        assert proposal['created_from']=='MANAGEMENT_DERIVED'
        assert proposal['expectations'][0]['checkpoint_id']
        save_pillar(db,c.id,PillarInput.model_validate(proposal),today=D1)
    evaluated=evaluate_thesis(db,c.id,a.id,use_ai=False,today=D1)
    assert all(row['new_status']=='NOT_YET_TESTABLE' for row in evaluated['impacts'])
    assert any(row['research_gaps'] for row in evaluated['impacts'])
    assert all(not row['contradiction'] for row in evaluated['impacts'])


def test_api_edits_scope_and_automatic_quarterly_impact(financial_env):
    client,db,c,p,context,key,yoy=analysis_setup(financial_env)
    data=pillar(expectations=[requirement(p,'1')]).model_dump(mode='json')
    created=client.post('/api/companies/ESDS/thesis/pillars',json=data)
    assert created.status_code==200,created.text
    response=client.get('/api/companies/ESDS/thesis').json()
    assert len(response['pillars'])==1
    assert client.get('/api/companies/ESDS/thesis?scope=WRONG').status_code==422
    record=analyze_period(db,c,p,deterministic_only=True,force=True)
    impact=client.get(f'/api/analysis/{record.id}/thesis-impact').json()
    assert impact['impacts'][0]['new_status']=='STRENGTHENING'
    edit={**data,'expectations':[],'expected_version':1,'is_active':False,'reason':'Requirement withdrawn'}
    assert client.put(f'/api/companies/ESDS/thesis/pillars/{created.json()["id"]}',json=edit).status_code==200
    assert client.put(f'/api/companies/ESDS/thesis/pillars/{created.json()["id"]}',json=edit).status_code==409
    assert len(client.get('/api/companies/ESDS/thesis').json()['history'])>=3
    deletion=client.delete('/api/companies/ESDS')
    assert deletion.status_code==409 and 'immutable thesis history' in deletion.json()['detail']
    assert client.get(f'/api/analysis/{record.id}/thesis-impact').json()['run_id']==impact['run_id']


@pytest.mark.skipif(os.getenv('ALMONDS_LIVE_REGRESSION')!='1',reason='Existing PostgreSQL corpus, explicitly opt in')
def test_live_esds_bootstrap_is_evidence_grounded_without_writes():
    from app.database import SessionLocal
    from app.models.company import Company
    with SessionLocal() as db:
        c=db.scalar(select(Company).where(Company.symbol=='ESDS'))
        a=db.scalar(select(AIAnalysis).where(AIAnalysis.company_id==c.id,AIAnalysis.status=='SUCCESS').order_by(AIAnalysis.id.desc()))
        result=bootstrap(db,c.id,a.id)
        assert len(result['proposals'])>=4
        for proposal in result['proposals']:
            assert all(k in result['evidence'] for k in proposal['evidence_ids'])
            save_pillar(db,c.id,PillarInput.model_validate(proposal))
        evaluation=evaluate_thesis(db,c.id,a.id,use_ai=False)
        assert evaluation['impacts'] and all(v['new_status'] in {'UNPROVEN','NOT_YET_TESTABLE','INSUFFICIENT_EVIDENCE'} for v in evaluation['impacts'])
        db.rollback()


def test_same_period_reanalysis_is_not_persistent_delivery(env):
    db,c,p,a,key=env
    item=save_pillar(db,c.id,pillar(expectations=[requirement(p)]),today=D1)
    evaluate_thesis(db,c.id,a.id,today=D1,use_ai=False)
    second=AIAnalysis(company_id=c.id,financial_period_id=p.id,model_provider='fixture',model_name='second',prompt_version='test',context_hash='b'*64,context_chars=1,evidence_registry=a.evidence_registry,status='SUCCESS',structured_output=a.structured_output,created_at=a.created_at)
    db.add(second);db.flush()
    result=evaluate_thesis(db,c.id,second.id,provider=Provider([proposed(item.id,key,proposed_status='ON_TRACK')]),today=D1)
    assert result['impacts'][0]['new_status']=='STRENGTHENING'
    assert any('REPEATED_REVIEW' in str(d) for d in result['diagnostics'])


def test_changed_requirement_cannot_borrow_old_failures_to_break_thesis(env):
    db,c,p,a,key=env
    item=save_pillar(db,c.id,pillar(expectations=[requirement(p,'120')]),today=D1)
    evaluate_thesis(db,c.id,a.id,today=D1,use_ai=False)
    nextp=result_period(db,c,date(2026,9,30),'2026-10-20',quarter=2)
    nexta,nextkey=saved(db,c,nextp,created=D2)
    save_expectation(db,c.id,item.id,requirement(nextp,'200'),today=D2)
    result=evaluate_thesis(db,c.id,nexta.id,provider=Provider([proposed(item.id,nextkey,proposed_status='BROKEN',contradiction=True,summary='Verified outcomes contradict the core investment requirement.')]),today=D2)
    assert result['impacts'][0]['new_status']=='WEAKENING'
    assert any('REPEATED_DIRECT_FAILURE' in str(d) for d in result['diagnostics'])


def test_provider_failure_preserves_deterministic_tests(env):
    db,c,p,a,key=env
    save_pillar(db,c.id,pillar(expectations=[requirement(p)]),today=D1)
    class Offline:
        def generate(self,*args): raise RuntimeError('unavailable')
    result=evaluate_thesis(db,c.id,a.id,provider=Offline(),today=D1)
    assert result['impacts'][0]['new_status']=='STRENGTHENING'
    assert result['diagnostics']==[{'reason':'THESIS_MODEL_UNAVAILABLE'}]


def test_future_and_missing_metric_targets_remain_missing(env):
    db,c,p,a,key=env
    future=requirement(p).model_copy(update={'target_period_end':date(2026,12,31)})
    missing=requirement(p).model_copy(update={'metric_code':'OPERATING_CASH_FLOW'})
    item=save_pillar(db,c.id,pillar(expectations=[future,missing]),today=D1)
    result=evaluate_thesis(db,c.id,a.id,today=D1,use_ai=False)
    outcomes=result['impacts'][0]['expectations']
    assert [o['status'] for o in outcomes]==['NOT_YET_TESTABLE','INSUFFICIENT_EVIDENCE']
    assert all(o['actual_value'] is None for o in outcomes)
    assert result['impacts'][0]['new_status']=='UNPROVEN'


def test_checkpoint_link_rejects_foreign_company_and_wrong_scope(db):
    from app.services.checkpoints import checkpoint_review
    c=issuer(db,'ONE'); other=issuer(db,'TWO')
    p=result_period(db,c,date(2026,6,30),'2026-07-20')
    call(db,c,p,'CEO: We target 200 GPUs and will deploy those by Q3 FY27.')
    cp=checkpoint_review(db,p)[0]
    foreign=save_pillar(db,other.id,pillar(),today=D1)
    standalone=save_pillar(db,c.id,pillar(scope='STANDALONE'),today=D1)
    linked=ExpectationInput(description='Require delivered deployment.',expectation_type='CHECKPOINT',checkpoint_id=cp['id'])
    for company_id,item in ((other.id,foreign),(c.id,standalone)):
        with pytest.raises(ValueError,match='CHECKPOINT_COMPANY_SCOPE_MISMATCH'):
            save_expectation(db,company_id,item.id,linked,today=D1)


def test_migration_schema_and_sql_immutability(tmp_path):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.database import Base
    spec=importlib.util.spec_from_file_location('phase8_migration',Path(__file__).parents[1]/'alembic/versions/0018_living_thesis.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=create_engine('sqlite://')
    # Existing tables only; migration must not depend on runtime thesis metadata.
    Base.metadata.create_all(engine,tables=[t for t in Base.metadata.sorted_tables if not t.name.startswith('thesis_')])
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            for name in module.TABLES:
                assert {c['name'] for c in inspect(connection).get_columns(name)}==set(Base.metadata.tables[name].c.keys())
            connection.execute(text("INSERT INTO thesis_pillars (company_id,scope) VALUES (1,'CONSOLIDATED')"))
            with pytest.raises(DBAPIError,match='THESIS_HISTORY_IS_IMMUTABLE'):
                connection.execute(text('DELETE FROM thesis_pillars'))
            module.downgrade()
            assert not any(name.startswith('thesis_') for name in inspect(connection).get_table_names())


def test_future_management_evidence_is_not_available_when_original_review_was_saved(db):
    from app.models.commentary import ManagementStatement
    c=issuer(db,'TIMING')
    p=result_period(db,c,date(2026,6,30),'2026-07-20')
    a,key=saved(db,c,p)
    call(db,c,p,'CEO: We target 200 GPUs and will deploy those by Q3 FY27.',published='2026-08-20')
    statement=db.scalar(select(ManagementStatement).where(ManagementStatement.company_id==c.id))
    assert statement is not None
    # A poisoned old snapshot must not gain access to a later publication.
    a.evidence_registry={**a.evidence_registry,statement.evidence_id:{'kind':'MANAGEMENT_STATEMENT','source_quote':statement.raw_statement}}
    db.flush()
    _,_,cutoff,evidence,_,_=analysis_evidence(db,c.id,a.id,D2)
    assert cutoff<=D1 and statement.evidence_id not in evidence


def test_management_claim_cannot_be_verified_delivery(db):
    c=issuer(db,'CLAIMS');p=result_period(db,c,date(2026,6,30),'2026-07-20')
    call(db,c,p,'CEO: We target 200 GPUs and will deploy those by Q3 FY27.')
    a,key=saved(db,c,p)
    context=build_context(db,c.name,c.symbol,p)
    a.evidence_registry=context.registry;a.structured_output={'investor_report':context.investor_report};db.flush()
    definition=PillarInput.model_validate(bootstrap(db,c.id,a.id,today=D1)['proposals'][0])
    item=save_pillar(db,c.id,definition,today=D1)
    statement=next(k for k in definition.evidence_ids if k.startswith('S'))
    quote=context.registry[statement]['source_quote']
    provider=Provider([proposed(item.id,statement,basis='VERIFIED_FACT',source_quote=quote,summary='Management expects deployment; this proves execution.')])
    result=evaluate_thesis(db,c.id,a.id,provider=provider,today=D1)
    assert result['impacts'][0]['new_status']=='NOT_YET_TESTABLE'
    assert any('MANAGEMENT_CLAIM_IS_NOT_FACT' in str(d) for d in result['diagnostics'])
