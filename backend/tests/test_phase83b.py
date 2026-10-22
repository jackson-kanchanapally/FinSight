"""Phase 8.3B: no synthetic observations ever enter the application database."""
import asyncio
from datetime import date, timedelta
from decimal import Decimal as D
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import select, func, inspect
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from app.models.financial import FinancialFactCorrection, FinancialUnit, MetricCode, FinancialFact
from app.models.expectations import ExpectationEvaluation, HistoricalExpectationRevision
from app.models.research import ResearchJob
from app.services import expectations
from app.services.forecast_accuracy import forecast_accuracy, comparisons
from app.services.forecast_scoring import point_errors, continuous_metrics, calibration, wilson
from app.schemas.forecast_accuracy import ForecastAccuracyRead, ComparisonPage
from tests.test_phase83 import db, clock, setup, data, CAPTURE, AFTER
from tests.test_phase71 import result_period
from tests.test_phase82 import fact


def save_ai(db, company, **changes):
    previous = result_period(db, company, date(2026, 3, 31), '2026-04-20', quarter=4)
    source = fact(db, previous, MetricCode.REVENUE_FROM_OPERATIONS, '80')
    db.flush()
    return expectations.save(db, company.id, data(source_type='AI', evidence_id=f'F{source.id}', **changes))


@pytest.mark.parametrize('predicted,actual,error,ape', [('120','100','20','20'), ('-20','-10','-10','100'),
    ('1','0','1',None), ('1','0.000001','0.999999',None), ('-1','1','-2','200')])
def test_point_math(predicted, actual, error, ape):
    row = point_errors(predicted, actual)
    assert D(row['signed_error']) == D(error)
    assert row['absolute_percentage_error'] is None if ape is None else D(row['absolute_percentage_error']) == D(ape)
    assert point_errors(predicted, actual, percentage_units=True)['absolute_percentage_error'] is None


def test_continuous_missing_negative_zero_outlier_and_directions():
    pairs = [('100','80',None,None), ('-30','-20','-40','-10'), (None,'5',None,None), ('10000','0',None,None)]
    row = continuous_metrics(pairs)
    assert row['n'] == 3 and row['missing_n'] == 1 and row['interval_n'] == 1
    assert D(row['mae']) == D(10030)/3 and D(row['bias']) == D(10010)/3
    assert D(row['wape_pct']) == 10030 and row['percentage_n'] == 1
    assert D(row['mape_pct']) == 25
    assert continuous_metrics([])['mae'] is None
    assert continuous_metrics([('1','0',None,None)])['wape_pct'] is None
    assert point_errors(None, '1')['absolute_error'] is None
    assert point_errors('11','12',baseline='10')['directional_correctness'] is True
    assert point_errors('10','12',baseline='10')['directional_correctness'] is False
    assert point_errors('10','12')['directional_correctness'] is None
    with pytest.raises(ValueError, match='NON_FINITE'):
        continuous_metrics([('NaN','1',None,None)])
    with pytest.raises(ValueError, match='UNORDERED'):
        continuous_metrics([('1','1','2','1')])


def test_known_interval_probability_label_buckets_and_uncertainty():
    rows = [dict(evaluation_id=i+1, probability='0.8', met=i<80, interval_probability='0.8',
                 low='0', high='10', actual='5' if i<80 else '15', confidence_label='HIGH') for i in range(100)]
    output = {r['kind']:r for r in calibration(rows, 20)}
    assert D(output['MET_PROBABILITY']['brier']) == D('0.16')
    assert output['CONFIDENCE_LABEL']['expected'] is None
    assert D(output['PREDICTION_INTERVAL']['observed']) == D('0.8')
    assert D(output['PREDICTION_INTERVAL']['mean_interval_width']) == 10
    bounds = output['PREDICTION_INTERVAL']['uncertainty_95']
    assert D(bounds['lower']) < D('.8') < D(bounds['upper'])
    assert all(r['status'] == 'INSUFFICIENT_DATA' for r in calibration(rows[:3],20))
    assert next(r for r in calibration(rows[:3],20) if r['kind']=='MET_PROBABILITY')['brier'] is None
    assert calibration([dict(evaluation_id=1, probability=None, confidence_label=None, interval_probability=None)],20) == []
    assert wilson(0,0) is None


def test_primary_actual_survives_corrections_and_source_invalidation(db, clock):
    company, period, revenue = setup(db)
    ledger, rev = save_ai(db, company, expected_value='110', confidence='0.8', confidence_event='MET_UNDER_FROZEN_TOLERANCE',
                          interval_lower='90', interval_upper='120', interval_probability='0.8')
    clock[0] = AFTER
    first = expectations.evaluate(db, company.id, ledger.id); db.commit()
    revenue.value = D('150')
    db.add(FinancialFactCorrection(fact_id=revenue.id,previous_value=100,corrected_value=150,reason='Re-extraction',corrected_at=AFTER))
    db.commit()
    second = expectations.evaluate(db,company.id,ledger.id); db.commit()
    assert first.id != second.id
    assert expectations.evaluate(db,company.id,ledger.id).id == second.id
    report = ForecastAccuracyRead.model_validate(forecast_accuracy(db,company.id,'CONSOLIDATED'))
    assert report.groups[0].mae == '10.000000'
    assert report.groups[0].evaluation_ids == [first.id]
    page = ComparisonPage.model_validate(comparisons(db,company.id,'CONSOLIDATED'))
    row = page.items[0]
    assert D(row.actual) == 100 and D(row.later_evaluations[0].actual) == 150
    assert row.evidence[f'F{revenue.id}']['document_id'] == period.source_document_id
    assert row.interval_probability == '0.8' and row.calculation_version == 'FORECAST_ACCURACY_V2'
    revenue.status = 'NEEDS_REVIEW'; db.commit()
    expectations.evaluate(db,company.id,ledger.id); db.commit()
    assert forecast_accuracy(db,company.id,'CONSOLIDATED')['groups'][0]['evaluation_ids'] == [first.id]
    assert len(comparisons(db,company.id,'CONSOLIDATED')['items'][0]['later_evaluations']) == 2


def test_corrected_before_first_score_does_not_masquerade_as_first_actual(db, clock):
    company, _, revenue = setup(db)
    ledger, _ = save_ai(db,company)
    revenue.value = D('150')
    db.add(FinancialFactCorrection(fact_id=revenue.id, previous_value=100,corrected_value=150,reason='Correction',corrected_at=AFTER))
    db.commit(); clock[0] = AFTER
    expectations.evaluate(db,company.id,ledger.id); db.commit()
    report = forecast_accuracy(db,company.id,'CONSOLIDATED')
    assert report['overview']['excluded'] == 1 and not report['groups']
    assert report['exclusions'][0]['reason_code'] == 'ORIGINAL_ACTUAL_SNAPSHOT_UNAVAILABLE'


@pytest.mark.parametrize('change,reason', [({'financial_scope':'STANDALONE'},'ACCOUNTING_SCOPE_MISMATCH'),
    ({'period_type':'ANNUAL','period':'2026-03-31'},'FISCAL_PERIOD_TYPE_MISMATCH'),
    ({'metric':'PAT'},'METRIC_ACTUAL_MISSING'), ({'period':'2025-12-31'},'ACTUAL_NOT_AVAILABLE')])
def test_explicit_matching_exclusions(db, clock, change, reason):
    company, _, _ = setup(db)
    # A same-scope verified source is required for AI input; use legacy USER
    # ledger to isolate matching diagnostics from forecast-generation validation.
    ledger, _ = expectations.save(db,company.id,data(**change))
    clock[0] = AFTER
    result = expectations.evaluate(db,company.id,ledger.id)
    if change.get('period_type') == 'ANNUAL':
        assert result.result['reason_code'] == 'ACTUAL_NOT_AVAILABLE'
    else:
        assert result.result['reason_code'] == reason


def test_score_queue_idempotence_failure_and_no_generation(db, clock, monkeypatch):
    from app.services.research_jobs import enqueue_accuracy, execute, finish
    from app.services import forecasting
    company,_,_ = setup(db)
    save_ai(db,company); db.commit(); clock[0] = AFTER
    monkeypatch.setattr(forecasting,'generate_company',lambda *a: pytest.fail('Scoring must not generate'))
    job = enqueue_accuracy(db,company.id); db.commit()
    assert enqueue_accuracy(db,company.id).id == job.id
    assert asyncio.run(execute(db,job))['processed'] == 1
    asyncio.run(execute(db,job))
    assert db.scalar(select(func.count()).select_from(ExpectationEvaluation)) == 1
    job.status='RETRY'; job.next_run_at=AFTER+timedelta(days=1); db.commit()
    assert enqueue_accuracy(db,company.id).next_run_at == job.next_run_at
    monkeypatch.setattr(expectations,'refresh_expectations',lambda *args: None)
    with pytest.raises(ValueError,match='RETRY_REQUIRED'):
        asyncio.run(execute(db,job))


def test_typed_api_pagination_filters_validation_queue_and_isolation(db, clock):
    from app.database import get_db
    from app.main import app
    company,_,_=setup(db)
    save_ai(db,company); clock[0]=AFTER
    expectations.refresh_expectations(db,company.id)
    app.dependency_overrides[get_db]=lambda:db
    try:
        with TestClient(app) as client:
            base='/api/companies/ACCURACY/forecasts'
            for endpoint in ('accuracy','comparisons'):
                response=client.get(f'{base}/{endpoint}?limit=1')
                assert response.status_code==200, response.text
                assert response.json()['total']==1
                assert client.get(f'{base}/{endpoint}?offset=1').json().get('items',[]) == []
                for params in ('offset=-1','limit=201','metric=FAKE','horizon_quarters=0','period_type=MONTHLY','date_from=2026-09-01&date_to=2026-08-01'):
                    assert client.get(f'{base}/{endpoint}?{params}').status_code==422
                assert client.get(f'{base}/{endpoint}?scope=STANDALONE').json()['total']==0
                assert client.get(f'{base}/{endpoint}?metric=PAT').json()['total']==0
                assert client.get(f'{base}/{endpoint}?date_from=2026-08-01').json()['total']==0
            assert client.get(base+'/comparisons?exclusions_only=true').json()['total']==0
            assert client.get('/api/companies/UNKNOWN/forecasts/accuracy').status_code==404
            first=client.post(base+'/evaluate'); second=client.post(base+'/evaluate')
            assert first.status_code==202 and first.json()['job_id']==second.json()['job_id']
    finally:
        app.dependency_overrides.clear()


def test_reversible_index_migration_preserves_history(db, clock):
    company,_,_=setup(db)
    ledger,_=save_ai(db,company); clock[0]=AFTER
    evaluation=expectations.evaluate(db,company.id,ledger.id); db.commit()
    spec=importlib.util.spec_from_file_location('phase83b',Path(__file__).parents[1]/'alembic/versions/0022_forecast_accuracy.py')
    migration=importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    with db.get_bind().begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade(); migration.upgrade()
    assert db.get(ExpectationEvaluation,evaluation.id).result['actual_value']=='100.000000'
    assert 'ix_expectation_evaluation_history' in {i['name'] for i in inspect(db.get_bind()).get_indexes('expectation_evaluations')}


def test_interval_validation_and_no_retrofitted_probability(db, clock):
    from pydantic import ValidationError
    for fields in (dict(interval_probability='.8'), dict(interval_lower='120',interval_upper='90',interval_probability='.8'),
                   dict(interval_lower='90',interval_upper='110',interval_probability='1')):
        with pytest.raises(ValidationError): data(**fields)
    company,_,_=setup(db)
    ledger,original=save_ai(db,company,confidence_label='HIGH')
    clock[0]=AFTER
    with pytest.raises(ValueError,match='PAST_CUTOFF'):
        expectations.save(db,company.id,data(expected_version=1,confidence='.9',confidence_event='MET_UNDER_FROZEN_TOLERANCE'),ledger.id)
    expectations.evaluate(db,company.id,ledger.id)
    group=forecast_accuracy(db,company.id,'CONSOLIDATED')['groups'][0]
    assert group['calibration'][0]['kind']=='CONFIDENCE_LABEL'
    assert group['calibration'][0]['expected'] is None
    assert original.definition['confidence'] is None


def test_walk_forward_excludes_inputs_after_simulated_capture():
    from app.services.forecasting import fit, next_quarter
    end=date(2023,3,31); rows=[]
    for i in range(16):
        rows.append(dict(period=end.isoformat(),value=str(100+i%4),published_at=f'{end+timedelta(days=30)}T00:00:00+00:00',
                         available_at=f'{next_quarter(end)+timedelta(days=10)}T00:00:00+00:00'))
        end=next_quarter(end)
    # Inputs are published before held-out outcomes, but after simulated capture.
    assert fit(rows,1) is None
    for row in rows: row['available_at']=row['published_at']
    assert fit(rows,1) is not None


def test_unit_currency_and_fiscal_boundaries_are_not_interchangeable(db, clock):
    from app.models.financial import PeriodType
    company,period,revenue=setup(db)
    ledger,_=save_ai(db,company)
    clock[0]=AFTER
    revenue.unit=FinancialUnit.INR_PER_SHARE; db.commit()
    result=expectations.evaluate(db,company.id,ledger.id)
    assert result.result['reason_code']=='UNIT_MISMATCH'
    assert not forecast_accuracy(db,company.id,'CONSOLIDATED')['groups']
    revenue.unit=FinancialUnit.INR_CRORE
    period.period_start=date(2026,1,1); db.commit()
    assert expectations.evaluate(db,company.id,ledger.id).result['reason_code']=='ACTUAL_SOURCE_OR_DEFINITION_INVALID'
    clock[0]=CAPTURE
    with pytest.raises(ValueError,match='CURRENCY_UNIT'):
        expectations.save(db,company.id,data(currency='NONE'))
    annual,_=expectations.save(db,company.id,data(period='2026-03-31',period_type='ANNUAL',source_reference='Annual only'))
    clock[0]=AFTER
    assert expectations.evaluate(db,company.id,annual.id).result['reason_code']=='FISCAL_PERIOD_TYPE_MISMATCH'


def test_generation_hook_and_horizon_metric_groups(db, monkeypatch):
    from tests.test_autonomous_research import forecast_history, AFTER as ORIGIN
    from app.services import forecasting
    company,_=forecast_history(db,monkeypatch)
    forecasting.generate_scope(db,company.id,'CONSOLIDATED'); db.commit()
    from datetime import datetime,timezone
    later=datetime(2026,11,1,tzinfo=timezone.utc)
    monkeypatch.setattr(expectations,'utcnow',lambda:later)
    period=result_period(db,company,date(2026,9,30),'2026-10-20',quarter=2)
    fact(db,period,MetricCode.REVENUE_FROM_OPERATIONS,'110')
    fact(db,period,MetricCode.PAT,'8'); db.commit()
    # This is the same refresh function used by committed financial evidence hooks.
    expectations.refresh_expectations(db,company.id)
    first_count=db.scalar(select(func.count()).select_from(ExpectationEvaluation))
    expectations.refresh_expectations(db,company.id)
    assert db.scalar(select(func.count()).select_from(ExpectationEvaluation))==first_count
    report=ForecastAccuracyRead.model_validate(forecast_accuracy(db,company.id,'CONSOLIDATED'))
    assert report.overview.evaluated==2
    assert {r.metric for r in report.groups}=={'PAT','REVENUE_FROM_OPERATIONS'}
    assert all(g.horizon_quarters==1 and g.n==1 and g.interval_n==1 and not g.calibration for g in report.groups)
    assert forecast_accuracy(db,company.id,'CONSOLIDATED',horizon_quarters=2)['groups']==[]
    assert len(report.groups[0].rolling_last_20)==1


def test_no_historical_ai_capture_is_scored(db,clock):
    company,_,_=setup(db)
    clock[0]=AFTER
    ledger,_=save_ai(db,company,historical_import=True,issued_at=CAPTURE)
    expectations.evaluate(db,company.id,ledger.id);db.commit()
    report=forecast_accuracy(db,company.id,'CONSOLIDATED')
    assert report['overview']['excluded']==1 and not report['groups']
    assert comparisons(db,company.id,'CONSOLIDATED')['items'][0]['reason_code']=='HISTORICAL_CAPTURE_UNVERIFIED'


def test_cutoff_after_publication_and_duplicate_results_do_not_enter_primary(db,clock):
    company,_,_=setup(db)
    ledger,_=save_ai(db,company,cutoff_at='2026-07-21T00:00:00Z')
    clock[0]=AFTER
    expectations.evaluate(db,company.id,ledger.id);db.commit()
    report=forecast_accuracy(db,company.id,'CONSOLIDATED')
    assert report['overview']['excluded']==1 and not report['groups']


def test_rolling_windows_and_calibration_are_publication_ordered(db,clock):
    from app.models.expectations import HistoricalExpectation
    from datetime import datetime,timezone
    company,_,_=setup(db)
    ledger,first=save_ai(db,company,confidence='0.8',confidence_event='MET_UNDER_FROZEN_TOLERANCE')
    # Additional deterministic snapshots in the disposable DB, with explicit
    # capture dates and different target periods. Never backfill production.
    clock[0]=AFTER
    expectations.evaluate(db,company.id,ledger.id)
    original=db.scalar(select(ExpectationEvaluation))
    for i in range(1,25):
        end=date(2027+i//4,[3,6,9,12][i%4],[31,30,30,31][i%4])
        owner=HistoricalExpectation(company_id=company.id,identity_key=f'synthetic-{i}',metric=ledger.metric,
            scope=ledger.scope,period_end=end,period_type='QUARTERLY',source_type='AI',created_at=CAPTURE)
        db.add(owner);db.flush()
        rev=HistoricalExpectationRevision(expectation_id=owner.id,version=1,cutoff_at=first.cutoff_at,
            definition={**first.definition,'period':str(end)},provenance=first.provenance,created_at=CAPTURE)
        db.add(rev);db.flush()
        db.add(ExpectationEvaluation(revision_id=rev.id,financial_period_id=original.financial_period_id,
            fingerprint=f'synthetic-{i}',outcome='MET',prospective_eligible=True,
            result={**original.result,'outcome_published_at':f'{end}T23:00:00+00:00'},created_at=AFTER))
    db.commit()
    group=forecast_accuracy(db,company.id,'CONSOLIDATED')['groups'][0]
    assert group['n']==25 and len(group['rolling_last_20'])==20
    assert group['rolling_last_20'][-1]['n']==20
    assert len(group['rolling_last_20'][-1]['evaluation_ids'])==20
    assert group['calibration'][0]['n']==25
    assert forecast_accuracy(db,company.id,'CONSOLIDATED',date_to=date(2026,12,31))['groups'][0]['n']==1


@pytest.mark.skipif(__import__('os').getenv('ALMONDS_PG_ACCURACY_TEST') != '1', reason='Opt-in disposable PostgreSQL schema')
def test_postgres_accuracy_index_upgrade_downgrade_preserves_records():
    from uuid import uuid4
    from sqlalchemy import create_engine, text
    from app.database import engine as local_engine
    schema='accuracy_index_test_'+uuid4().hex
    with local_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_engine(local_engine.url,connect_args={'options':f'-csearch_path={schema}'})
    try:
        spec=importlib.util.spec_from_file_location('pg83b',Path(__file__).parents[1]/'alembic/versions/0022_forecast_accuracy.py')
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        with engine.begin() as connection:
            connection.execute(text('CREATE TABLE historical_expectation_revisions (id integer PRIMARY KEY)'))
            connection.execute(text('CREATE TABLE expectation_evaluations (id integer PRIMARY KEY, revision_id integer REFERENCES historical_expectation_revisions(id), result json NOT NULL)'))
            connection.execute(text('INSERT INTO historical_expectation_revisions VALUES (1)'))
            connection.execute(text("INSERT INTO expectation_evaluations VALUES (1,1,'{\"actual_value\":\"100\"}')"))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert connection.scalar(text('SELECT count(*) FROM expectation_evaluations'))==1
                migration.downgrade()
                assert connection.scalar(text("SELECT result->>'actual_value' FROM expectation_evaluations"))=='100'
                migration.upgrade()
    finally:
        engine.dispose()
        with local_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


@pytest.mark.skipif(__import__('os').getenv('ALMONDS_LIVE_REGRESSION') != '1', reason='Opt-in existing PostgreSQL corpus, read only')
@pytest.mark.parametrize('symbol',['ESDS','ETERNAL'])
def test_real_company_accuracy_and_financial_apis_do_not_mutate_history(symbol):
    from app.database import SessionLocal,get_db
    from app.main import app
    from sqlalchemy import text
    from app.models.financial import FinancialFact,FinancialPeriod
    from app.services.thesis_monitoring_evidence import digest
    with SessionLocal() as session:
        session.execute(text('SET TRANSACTION READ ONLY'))
        def fingerprint():
            return digest({model.__tablename__:[dict(row) for row in session.execute(select(model.__table__).order_by(model.id)).mappings()]
                for model in (HistoricalExpectationRevision,ExpectationEvaluation,FinancialFact,FinancialPeriod)})
        before=fingerprint()
        app.dependency_overrides[get_db]=lambda:session
        try:
            with TestClient(app) as client:
                base=f'/api/companies/{symbol}'
                for scope in ('CONSOLIDATED','STANDALONE'):
                    result=client.get(base+f'/forecasts/accuracy?scope={scope}')
                    assert result.status_code==200,result.text
                    ForecastAccuracyRead.model_validate(result.json())
                    page=client.get(base+f'/forecasts/comparisons?scope={scope}')
                    assert page.status_code==200,page.text
                    ComparisonPage.model_validate(page.json())
                    assert client.get(base+f'/financials/latest?scope={scope}').status_code==200
                assert client.get(base+'/accuracy/guidance').status_code==200
            assert fingerprint()==before
        finally:
            app.dependency_overrides.clear()


@pytest.mark.parametrize('metric,unit,predicted,actual,mae',[
    ('DILUTED_EPS','INR_PER_SHARE','1.5','1.2','0.3'),
    ('PAT_MARGIN','PERCENT','15','12','3'),
    ('EBITDA','INR_CRORE','120','100','20')])
def test_other_financial_definitions_keep_their_own_units(db,clock,metric,unit,predicted,actual,mae):
    company,period,_=setup(db)
    value=fact(db,period,MetricCode(metric),actual);value.unit=FinancialUnit(unit);db.commit()
    ledger,_=save_ai(db,company,metric=metric,unit=unit,currency='NONE' if unit=='PERCENT' else 'INR',expected_value=predicted)
    clock[0]=AFTER
    expectations.evaluate(db,company.id,ledger.id);db.commit()
    group=forecast_accuracy(db,company.id,'CONSOLIDATED')['groups'][0]
    assert D(group['mae'])==D(mae) and group['unit']==unit
    assert group['wape_pct'] is None if unit=='PERCENT' else group['wape_pct'] is not None


def test_derived_metric_rejects_an_input_with_incompatible_unit(db,clock):
    from app.services.financials.service import calculate_company_metrics
    company,period,_=setup(db)
    for metric,value in [('PBT','20'),('FINANCE_COST','5'),('DEPRECIATION','5')]:
        fact(db,period,MetricCode(metric),value)
    db.commit();calculate_company_metrics(db,company.id);db.commit()
    ledger,_=save_ai(db,company,metric='EBITDA_MARGIN',unit='PERCENT',currency='NONE',expected_value='30')
    pbt=db.scalar(select(FinancialFact).where(
        FinancialFact.financial_period_id==period.id,
        FinancialFact.metric_code=='PBT'))
    pbt.unit=FinancialUnit.INR_PER_SHARE;db.commit();clock[0]=AFTER
    assert expectations.evaluate(db,company.id,ledger.id).outcome=='INSUFFICIENT_EVIDENCE'


def test_metadata_cannot_include_future_information(db,clock):
    company,_,_=setup(db)
    with pytest.raises(ValueError,match='INFORMATION_CUTOFF_IN_FUTURE'):
        expectations.save(db,company.id,data(),forecast_metadata={'information_cutoff':AFTER.isoformat()})
    with pytest.raises(ValueError,match='INPUT_AFTER_INFORMATION_CUTOFF'):
        expectations.save(db,company.id,data(),forecast_metadata={'information_cutoff':CAPTURE.isoformat(),
            'inputs':[{'available_at':AFTER.isoformat()}]})
