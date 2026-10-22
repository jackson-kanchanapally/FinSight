"""Explainable seasonal forecasts. No simulated forecast is entered in history."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from statistics import median
from types import SimpleNamespace
from sqlalchemy import select
from app.models.financial import FinancialPeriod, PeriodType
from app.models.expectations import HistoricalExpectation as Ledger
from app.models.thesis import ThesisPillar, ThesisPillarRevision
from app.services import expectations
from app.services.expectation_evidence import actual_evidence, financial_evidence_batch, utc, utcnow
from app.services.expectation_math import canonical_unit
from app.services.thesis_monitoring_evidence import digest
from app.schemas.expectations import ExpectationInput

VERSION = 'SEASONAL_BASELINES_V2'
METRICS = ('REVENUE_FROM_OPERATIONS','EBITDA','EBITDA_MARGIN','PAT','PAT_MARGIN','DILUTED_EPS')
D = Decimal


def number(value):
    return str(D(value).quantize(D('0.000001')))


def next_quarter(end):
    return date(end.year+1,3,31) if end.month==12 else date(end.year,end.month+3,30 if end.month in (3,6) else 31)


def predict(values, model, horizon=1):
    if model == 'SEASONAL_NAIVE':
        return values[-4+horizon-1]
    if model == 'TRAILING_MEAN':
        return sum(values[-4:])/4
    if model == 'SEASONAL_DRIFT':
        changes = [values[i]-values[i-4] for i in range(len(values)-4,len(values))]
        return values[-4+horizon-1]+D(median(changes))
    raise ValueError('UNKNOWN_FORECAST_MODEL')


def fit(rows, horizon):
    """Comparable expanding-window hindcasts, never claimed as stored predictions.

    Every training input must have been available before the held-out result's
    publication. Corrections are treated as newly available data, preventing a
    corrected historical value from leaking into earlier validation folds.
    """
    models = ['SEASONAL_NAIVE','TRAILING_MEAN']
    if not 1 <= horizon <= 4:
        raise ValueError('FORECAST_HORIZON_MUST_BE_1_TO_4_QUARTERS')
    start = 4
    if len(rows) >= 12 + horizon - 1:
        models.append('SEASONAL_DRIFT'); start = 8
    errors = {model:[] for model in models}
    for end in range(start,len(rows)-horizon+1):
        target = rows[end+horizon-1]
        training = rows[:end]
        if max(r['available_at'] for r in training) >= target['published_at']:
            continue
        # Reproduce the generator's pre-quarter-end capture rule at every fold,
        # not merely availability before the eventual target publication. A
        # delayed input published inside the target's reporting lag is leakage.
        if not training[-1].get('period'):
            continue
        origin = datetime.combine(next_quarter(date.fromisoformat(training[-1]['period'])), time.min, tzinfo=timezone.utc)
        if (max(utc(datetime.fromisoformat(r['available_at'])) for r in training) >= origin
                or utc(datetime.fromisoformat(target['published_at'])) <= origin):
            continue
        values = [D(r['value']) for r in training]
        for model in models:
            errors[model].append(predict(values,model,horizon)-D(target['value']))
    if not errors or min(map(len,errors.values())) < 4:
        return None
    scores = {m:sum(abs(e) for e in values)/len(values) for m,values in errors.items()}
    chosen = min(models,key=lambda m:(scores[m],models.index(m)))
    base = predict([D(r['value']) for r in rows],chosen,horizon)
    # Empirical scenario envelope, not a calibrated prediction probability.
    width = max(abs(e) for e in errors[chosen])
    return {'model':chosen,'base':number(base),'low':number(base-width),'high':number(base+width),
        'validation_n':len(errors[chosen]),'hindcast_mae':{m:number(v) for m,v in scores.items()},
        'interval_method':'Base ± maximum absolute expanding-window validation error at this horizon; descriptive scenario envelope, no probability guarantee',
        'uncertainty':'LIMITED_SAMPLE' if len(errors[chosen]) < 20 else 'EMPIRICAL_NOT_CALIBRATED'}


def verified_history(db, company_id, scope, metric, periods, now, batch=None):
    rows=[]
    for period in periods:
        match=SimpleNamespace(company_id=company_id,scope=scope,metric=metric,period_end=period.period_end,period_type='QUARTERLY')
        _, actual, registry = actual_evidence(db,match,now,batch)
        if not actual or actual['unit'] != canonical_unit(metric):
            rows=[]; continue
        sources=[r for r in registry.values() if r.get('kind')=='FACT']
        if not sources:
            rows=[]; continue
        dates=[r.get('available_at',r['published_at']) for r in sources]
        dates.extend(c['corrected_at'] for r in sources for c in r.get('corrections',[]))
        available=max(datetime.fromisoformat(v) for v in dates)
        if available > now:
            rows=[]; continue
        if rows and next_quarter(date.fromisoformat(rows[-1]['period'])) != period.period_end:
            rows=[]
        rows.append({'period':period.period_end.isoformat(),'value':actual['value'],
            'published_at':max(r['published_at'] for r in sources),'available_at':available.isoformat(),'evidence':registry})
    return rows


def thesis_link(db,company_id,scope,metric,now):
    candidates=list(db.scalars(select(ThesisPillarRevision).join(ThesisPillar).where(
        ThesisPillar.company_id==company_id,ThesisPillar.scope==scope,ThesisPillarRevision.created_at<=now).order_by(ThesisPillarRevision.version.desc())))
    latest={}
    for r in candidates: latest.setdefault(r.pillar_id,r)
    matches=[r.id for r in latest.values() if metric in r.definition.get('metric_codes',[])]
    return matches[0] if len(matches)==1 else None


def generate_scope(db,company_id,scope,now=None):
    now=now or utcnow()
    expectations.lock(db,company_id)
    periods=list(db.scalars(select(FinancialPeriod).where(FinancialPeriod.company_id==company_id,
        FinancialPeriod.scope==scope,FinancialPeriod.period_type==PeriodType.QUARTERLY,
        FinancialPeriod.status=='VALIDATED',FinancialPeriod.period_end<now.date()).order_by(FinancialPeriod.period_end.desc()).limit(20)))[::-1]
    output=[]
    batch = financial_evidence_batch(db, periods)
    for metric in METRICS:
        rows=verified_history(db,company_id,scope,metric,periods,now,batch)
        gap={'metric':metric,'scope':scope,'status':'INSUFFICIENT_EVIDENCE','verified_quarters':len(rows)}
        if len(rows)<8:
            output.append({**gap,'reason':'At least 8 consecutive, verified quarterly observations in this scope are required.'}); continue
        last=date.fromisoformat(rows[-1]['period'])
        if (now.date()-last).days>185:
            output.append({**gap,'reason':'Latest verified quarterly history is stale (over 185 days).'}); continue
        for horizon in range(1,5):
            target=last
            for _ in range(horizon): target=next_quarter(target)
            if target<=now.date(): continue
            fitted=fit(rows,horizon)
            if not fitted:
                output.append({**gap,'period':target.isoformat(),'horizon_quarters':horizon,'reason':'Fewer than 4 publication-safe validation folds at this horizon.'}); continue
            if metric=='REVENUE_FROM_OPERATIONS' and D(fitted['low'])<0:
                if D(fitted['base']) < 0:
                    output.append({**gap,'period':target.isoformat(),'reason':'Candidate model implies negative revenue; no defensible forecast.'}); continue
                fitted['low']='0.000000'
            fingerprint=digest({'rows':rows,'model':VERSION,'metric':metric,'target':target,'fit':fitted})
            reference=f'ALMONDS:{VERSION}:{metric}:{target}'
            existing=db.scalar(select(Ledger).where(Ledger.company_id==company_id,Ledger.scope==scope,
                Ledger.metric==metric,Ledger.period_end==target,Ledger.source_type=='AI',
                Ledger.identity_key.is_not(None)).order_by(Ledger.id.desc()))
            # Only this deterministic engine's identity may be revised.
            previous=expectations.latest_revision(db,existing.id) if existing else None
            if previous and previous.definition['source_reference']!=reference:
                existing=None; previous=None
            if previous and previous.definition.get('forecast',{}).get('input_hash')==fingerprint:
                output.append({'metric':metric,'scope':scope,'period':target.isoformat(),'status':'MODEL_ESTIMATE','expectation_id':existing.id,'revision_id':previous.id,'unchanged':True}); continue
            metadata={**fitted,'engine_version':VERSION,'input_hash':fingerprint,'information_cutoff':now.isoformat(),
                'horizon_quarters':horizon,'horizon_days':(target-now.date()).days,'inputs':rows,'data_quality':'VERIFIED_INPUTS',
                'direction_baseline': rows[-4+horizon-1],
                'direction_method': 'Sign of change from the same quarter a year earlier, using the frozen baseline; zero is unchanged.',
                'assumptions':['Quarterly seasonal patterns persist; no management target is substituted for a result.',
                    'No sector uplift, consensus estimate, or unverified operating KPI is assumed.',
                    'Forecasts are captured before quarter-end; no post-quarter nowcast is inferred without a verified announcement calendar.',
                    'Model selected by lowest publication-safe hindcast MAE; historical validation is not prospective performance.'],
                'classification':'MODEL_ESTIMATE','interval_probability':None}
            evidence=next(k for k in rows[-1]['evidence'] if k.startswith('F'))
            linked=previous.thesis_revision_id if previous else thesis_link(db,company_id,scope,metric,now)
            payload=ExpectationInput(metric=metric,financial_scope=scope,period=target,unit=canonical_unit(metric),
                currency='NONE' if canonical_unit(metric)=='PERCENT' else 'INR',expected_value=fitted['base'],
                description=f'Almonds {metric} quarterly forecast for {target}',source_type='AI',source_reference=reference,
                evidence_id=evidence,cutoff_at=datetime.combine(target,time.max,tzinfo=timezone.utc),
                expected_version=previous.version if previous else None,thesis_revision_id=linked)
            ledger,revision=expectations.save(db,company_id,payload,existing.id if existing else None,forecast_metadata=metadata)
            output.append({'metric':metric,'scope':scope,'period':target.isoformat(),'status':'MODEL_ESTIMATE','expectation_id':ledger.id,'revision_id':revision.id,'unchanged':False})
    return output


def generate_company(db,company_id):
    return {'engine_version':VERSION,'scopes':{scope:generate_scope(db,company_id,scope) for scope in ('CONSOLIDATED','STANDALONE')}}
