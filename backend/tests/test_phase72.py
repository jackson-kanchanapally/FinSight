"""Senior quarterly review tests use synthetic unit data; no production fixtures."""
import copy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import pytest
from sqlalchemy import select, func
from tests.test_phase71 import db, issuer, result_period, event_doc
from app.models.commentary import ManagementStatement
from app.models.checkpoint import InvestorCheckpoint, CheckpointEvaluation
from app.models.event import EventType
from app.models.financial import (FinancialFact, FinancialMetric, FinancialScope, PeriodType,
    MetricCode, FinancialUnit, FactMethod, FactStatus)
from app.services.commentary import ingest_commentary
from app.services.materiality import materiality, select_material, short_quote
from app.services.checkpoints import checkpoint_definition, checkpoint_review
from app.services.quarterly_review import build_review
from app.ai.analyst.schemas import QuarterlyInvestorReview
from app.ai.analyst.validator import InvalidAnalysis, validate_quarterly_review
from tests.test_financials import financial_env
from tests.test_ai_analyst import setup as analysis_setup
from app.ai.providers.base import LLMProvider
from app.ai.analyst.service import analyze_period
from app.models.analysis import AIAnalysis


@pytest.mark.parametrize('text', [
    'Good morning everyone. Thank you for joining the call today.',
    'Our revenue from operations came in at INR 100 crore, while PAT grew by 20%.',
    'We remain very optimistic and confident about our wonderful future.',
    'The company undertakes no obligation to publicly update forward-looking statements, actual results may differ.',
])
def test_transcript_noise_excluded(text):
    assert materiality(text) is None


@pytest.mark.parametrize('text', [
    'We target deployment of 200 GPUs by Q3 FY27 to support the new service.',
    'The delivery timeline has been delayed because equipment is not available.',
    'We cannot give any forward-looking numbers for the full year at present.',
    'Capex will be funded through customer advances and partial debt.',
    'Our domestic order book has a conversion cycle extending over several quarters.',
])
def test_material_evidence_included(text):
    assert materiality(text) is not None


def call(db, company, period, text, published='2026-07-25'):
    event_doc(db, company, EventType.CONCALL_TRANSCRIPT,
        f'Q{period.quarter} {period.fiscal_year} Earnings Call Transcript', published, text=text)
    db.commit()
    ingest_commentary(db, period)


def test_checkpoint_creation_idempotence_and_untimed_target(db):
    company = issuer(db, 'GENERIC')
    period = result_period(db, company, date(2026,6,30), '2026-07-20')
    call(db, company, period,
         'CEO: We are targeting PAT margins in the range of 15% to 20%.\n'
         'CEO: We expect a wonderful future and strong demand.\n'
         'CEO: We target 200 GPUs and will deploy those by Q3 FY27.')
    rows = checkpoint_review(db, period)
    assert len(rows) == 2
    assert {r['metric'] for r in rows} == {'PAT_MARGIN', 'GPU_DEPLOYMENT'}
    assert next(r for r in rows if r['metric']=='PAT_MARGIN')['status'] == 'NOT_YET_ASSESSABLE'
    assert next(r for r in rows if r['metric']=='GPU_DEPLOYMENT')['target_period'] == 'Q3FY27'
    checkpoint_review(db, period)
    assert db.scalar(select(func.count()).select_from(InvestorCheckpoint)) == 2
    assert db.scalar(select(func.count()).select_from(CheckpointEvaluation)) == 2


@pytest.mark.parametrize(('actual_value','expected'), [(24,'DELIVERED'), (10,'MISSED')])
def test_verified_checkpoint_outcomes_preserve_prior_review(db, actual_value, expected):
    company = issuer(db, 'EXECUTION')
    quarter = result_period(db, company, date(2026,6,30), '2026-07-20')
    call(db, company, quarter, 'CFO: We expect FY27 revenue growth of 20–25%.')
    first = checkpoint_review(db, quarter)
    assert first[0]['status']=='PENDING'
    prior = result_period(db, company, date(2026,3,31), '2026-04-20', kind=PeriodType.ANNUAL, quarter=None, fy='FY2026')
    actual = result_period(db, company, date(2027,3,31), '2027-04-20', kind=PeriodType.ANNUAL, quarter=None)
    facts=[]
    for p,value in [(prior,100),(actual,100+actual_value)]:
        f=FinancialFact(financial_period_id=p.id, metric_code=MetricCode.REVENUE_FROM_OPERATIONS,
            value=Decimal(value),unit=FinancialUnit.INR_CRORE,source_document_id=p.source_document_id,
            source_page=3,source_label='Revenue',source_column_date=p.period_end,
            extraction_method=FactMethod.PDF_TABLE,status=FactStatus.VERIFIED_PDF)
        db.add(f); facts.append(f)
    db.flush()
    db.add(FinancialMetric(financial_period_id=actual.id,metric_code=MetricCode.REVENUE_YOY,
        value=Decimal(actual_value),unit=FinancialUnit.PERCENT,input_fact_ids=[f.id for f in facts],comparison_period_id=prior.id))
    db.flush()
    outcome=checkpoint_review(db,actual)[0]
    assert outcome['status']==expected
    assert outcome['actual_outcome']['actual_sources'][0]['page']==3
    old=db.scalar(select(CheckpointEvaluation).where(CheckpointEvaluation.review_period_id==quarter.id))
    assert old.status=='PENDING'
    assert checkpoint_review(db,quarter)[0]['status']=='PENDING'  # no look-ahead


@pytest.mark.parametrize(('revision','expected'), [
    ('We postponed our previous PAT margin target to FY28.', 'DELAYED'),
    ('We have withdrawn our previous PAT margin guidance.', 'WITHDRAWN'),
])
def test_explicit_revision_and_original_preserved(db, revision, expected):
    company=issuer(db,'REVISIONS')
    first=result_period(db,company,date(2026,6,30),'2026-07-20')
    call(db,company,first,'CFO: We target FY27 PAT margin of 15–20%.')
    checkpoint_review(db,first)
    second=result_period(db,company,date(2026,9,30),'2026-10-20',quarter=2)
    call(db,company,second,'CFO: '+revision,published='2026-10-25')
    assert checkpoint_review(db,second)[0]['status']==expected
    assert checkpoint_review(db,first)[0]['status']=='PENDING'


def test_company_scope_isolation_and_guidance_changes(db):
    a,b=issuer(db,'ONE'),issuer(db,'TWO')
    first=result_period(db,a,date(2026,6,30),'2026-07-20')
    call(db,a,first,'CEO: We expect FY27 revenue growth of 20–25%.')
    later=result_period(db,a,date(2026,9,30),'2026-10-20',quarter=2)
    call(db,a,later,'CEO: We now target FY27 revenue growth of 25–30%.',published='2026-10-25')
    records=checkpoint_review(db,later)
    assert any(r['actual_outcome'].get('change')=='GUIDANCE_RAISED' for r in records)
    other=result_period(db,b,date(2026,9,30),'2026-10-20',quarter=2)
    standalone=result_period(db,a,date(2026,9,30),'2026-10-20',quarter=2,scope=FinancialScope.STANDALONE)
    assert checkpoint_review(db,other)==[]
    assert checkpoint_review(db,standalone)==[]


def review_fixture():
    item={'text':'The operating evidence remains mixed.', 'evidence_ids':['M1'], 'basis':'INTERPRETATION'}
    review={'quarterly_investor_view':[item,item], 'key_developments':[], 'management_signals':[],
        'what_changed':[], 'valuation_implication':{**item,'evidence_ids':['I_REVIEW'],'status':'INSUFFICIENT_EVIDENCE'},
        'key_risks':[], 'watch_next_quarter':[item], 'investor_conclusion':item}
    registry={'M1':{'kind':'METRIC','metric_code':'REVENUE_YOY','value':'10','unit':'PERCENT','input_evidence_ids':[]},
        'I_REVIEW':{'kind':'REVIEW_CONTEXT','valuation_implication_status':'INSUFFICIENT_EVIDENCE','changes':[]},
        'S1':{'kind':'MANAGEMENT_STATEMENT','source_quote':'We expect stronger customer demand next year.',
              'excerpt':'We expect stronger customer demand next year.'}}
    return review,registry


def test_review_validation_preserves_deterministic_valuation():
    review, registry=review_fixture()
    validate_quarterly_review(QuarterlyInvestorReview.model_validate(review),registry,2026)
    review['valuation_implication']['status']='MORE_CREDIBLE'
    with pytest.raises(InvalidAnalysis,match='DETERMINISTIC_FIELD_MISMATCH'):
        validate_quarterly_review(QuarterlyInvestorReview.model_validate(review),registry,2026)


@pytest.mark.parametrize(('change','reason'), [
    ({'text':'Revenue increased 999%.'},'UNSUPPORTED_NUMBER'),
    ({'evidence_ids':['UNKNOWN']},'UNKNOWN_EVIDENCE_ID'),
    ({'text':'Management expects demand.', 'evidence_ids':['S1'], 'basis':'VERIFIED_FACT'},'MANAGEMENT_CLAIM_IS_NOT_FACT'),
    ({'text':'Management expects demand.', 'evidence_ids':['S1'], 'basis':'MANAGEMENT_CLAIM','source_quote':'We won an enormous contract.'},'MANAGEMENT_QUOTE_NOT_IN_SOURCE'),
    ({'text':'Management delivered the target.'},'CHECKPOINT_OUTCOME_UNSUPPORTED'),
    ({'text':'The shares are undervalued.'},'UNSUPPORTED_VALUATION_JUDGMENT'),
    ({'text':'Operational efficiency improved.'},'UNSUPPORTED_CAUSAL_EXPLANATION'),
    ({'source_quote':'Management promised record revenue.'},'QUOTE_NOT_IN_CITED_EVIDENCE'),
])
def test_review_rejects_unsupported_claims(change,reason):
    review,registry=review_fixture()
    review['key_developments']=[{**review['investor_conclusion'],**change}]
    with pytest.raises(InvalidAnalysis,match=reason):
        validate_quarterly_review(QuarterlyInvestorReview.model_validate(review),registry,2026)


def test_no_historical_change_from_one_statement():
    review,registry=review_fixture()
    review['what_changed']=[review['investor_conclusion']]
    with pytest.raises(InvalidAnalysis,match='HISTORICAL_CHANGE_UNSUPPORTED'):
        validate_quarterly_review(QuarterlyInvestorReview.model_validate(review),registry,2026)


def test_review_bound_and_no_deterministic_override_fields():
    review,_=review_fixture()
    review['key_developments']=[review['investor_conclusion']]*8
    with pytest.raises(ValueError): QuarterlyInvestorReview.model_validate(review)
    review,_=review_fixture()
    review['management_execution']=[{'status':'DELIVERED'}]
    with pytest.raises(ValueError): QuarterlyInvestorReview.model_validate(review)


def test_short_quotes_preserve_targets_and_seasonality():
    text = 'We are targeting PAT margins in the range of 15% to 20%.'
    assert short_quote(text) == text
    seasonal = 'Yes, there is seasonality. Q4 is always a stronger quarter for us. ' + (
        'As capacity grows, we expect the distribution of revenue to change over time. ' * 5)
    assert 'seasonality' in short_quote(seasonal)
    assert 'Q4 is always a stronger quarter' in short_quote(seasonal)


def test_quarterly_provider_contract_and_cache(financial_env):
    _, db, company, period, context, revenue, yoy = analysis_setup(financial_env)
    assert not context.investor_report['checkpoints']
    assert 'No material management commentary' in context.investor_report['quarterly_review']['quarterly_investor_view'][1]['text']
    draft, _ = review_fixture()
    for section in ('quarterly_investor_view', 'watch_next_quarter'):
        for item in draft[section]:
            item['evidence_ids'] = [yoy]
    draft['investor_conclusion']['evidence_ids'] = [yoy]
    draft['valuation_implication']['evidence_ids'] = ['I_REVIEW']
    draft['quarterly_investor_view'][1] = {**draft['quarterly_investor_view'][1],
        'text': 'The next report will test whether the observed trend persists.'}
    draft['watch_next_quarter'] = [{**draft['watch_next_quarter'][0],
        'text': 'Watch whether the reported revenue trend holds next quarter.'}]
    draft['valuation_implication'] = {**draft['valuation_implication'],
        'text': 'The evidence is insufficient to reassess the growth assumptions.'}
    draft['investor_conclusion'] = {**draft['investor_conclusion'],
        'text': 'The observed result warrants another quarter of evidence.'}

    class ReviewProvider(LLMProvider):
        supports_quarterly_review = True
        name, model = 'review-test', 'fixture'
        calls = 0

        def generate(self, system, user, schema):
            self.calls += 1
            assert schema['required'] == ['quarterly_review']
            assert 'Include a summary' not in user
            assert 'Do not calculate values' in system
            assert {'text', 'evidence_ids', 'basis', 'source_quote'}.issubset(schema['$defs']['ReviewItem']['required'])
            assert schema['$defs']['ReviewItem']['properties']['evidence_ids']['minItems'] == 1
            return json.dumps({'quarterly_review': draft})

    provider = ReviewProvider()
    result = analyze_period(db, company, period, provider)
    assert result.status == 'SUCCESS', result.validation_diagnostics
    assert result.structured_output['investor_report']['tested_pe'] == context.investor_report['tested_pe']
    assert result.structured_output['quarterly_review']['valuation_implication']['status'] == 'INSUFFICIENT_EVIDENCE'
    assert analyze_period(db, company, period, provider).id == result.id
    assert provider.calls == 1


def test_automatic_review_refresh_and_concise_fallback(financial_env):
    from app.services.quarterly_review import refresh_quarterly_reviews
    _, db, company, period, _, _, _ = analysis_setup(financial_env)
    call(db, company, period, 'CEO: We target 200 GPUs and will deploy those by Q3 FY27.')
    refresh_quarterly_reviews(db, company.id)
    rows = list(db.scalars(select(AIAnalysis).where(AIAnalysis.model_provider == 'deterministic')))
    assert rows
    review = next(r for r in rows if r.financial_period_id == period.id).structured_output['investor_report']
    assert review['checkpoints'][0]['status'] == 'PENDING'
    changes = review['quarterly_review']['what_changed']
    assert len(changes) == 2
    assert all('QoQ' in item['text'] for item in changes)  # YoY is not last-quarter change.
    assert len(review['quarterly_review']['key_developments']) <= 7
    assert len(review['quarterly_review']['watch_next_quarter']) <= 6
    before = len(rows)
    refresh_quarterly_reviews(db, company.id)
    assert db.scalar(select(func.count()).select_from(AIAnalysis).where(AIAnalysis.model_provider == 'deterministic')) == before


def test_capex_delivery_does_not_validate_earnings_assumptions(db, monkeypatch):
    import app.services.quarterly_review as module
    company = issuer(db, 'CAPITAL')
    period = result_period(db, company, date(2026,6,30), '2026-07-20')
    call(db, company, period, 'CEO: We plan INR 100 crore of capex for FY27.')
    records = checkpoint_review(db, period)
    records[0].update(status='DELIVERED', is_previous=True)
    monkeypatch.setattr(module, 'checkpoint_review', lambda *_: records)
    report = {'tested_pe': {'reverse_expectations': {'required_eps_cagr': '20'}},
        'results': {'evidence_ids': []}, 'investor_conclusion': {'text': 'Results are mixed.'}}
    output = build_review(db, period, {}, report)
    assert output['quarterly_review']['valuation_implication']['status'] == 'INSUFFICIENT_EVIDENCE'


def test_live_esds_review_packet():
    """Opt-in existing-corpus regression; transaction rolls back and creates no documents."""
    import os
    if os.getenv('RUN_PHASE72_LIVE') != '1':
        pytest.skip('Requires existing local ESDS results and transcript')
    from app.database import SessionLocal
    from app.models.company import Company
    from app.models.financial import FinancialPeriod
    from app.ai.analyst.context_builder import build_context
    with SessionLocal() as session:
        company = session.scalar(select(Company).where(Company.symbol == 'ESDS'))
        period = session.scalar(select(FinancialPeriod).where(
            FinancialPeriod.company_id == company.id, FinancialPeriod.fiscal_year == 'FY2027',
            FinancialPeriod.quarter == 1, FinancialPeriod.scope == FinancialScope.CONSOLIDATED))
        context = build_context(session, company.name, company.symbol, period, max_chars=12000)
        packet = json.loads(context.text)['investor_review_packet']
        quotes = ' '.join(s['source_quote'] for s in packet['material_statements']).lower()
        assert all(term in quotes for term in ('gpu', 'pat margin', 'capex', 'debt', 'order book', 'seasonality', 'guidance'))
        assert len(packet['material_statements']) <= 14
        assert 'good morning' not in quotes
        assert context.investor_report['full_statement_count'] > len(packet['material_statements'])
        assert {c['metric'] for c in packet['checkpoints']} >= {'GPU_DEPLOYMENT', 'REVENUE_START', 'PAT_MARGIN'}
        assert all(c['status'] != 'DELIVERED' for c in packet['checkpoints'])
        assert len(context.text) <= 12000
