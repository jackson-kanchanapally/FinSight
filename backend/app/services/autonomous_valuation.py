"""Automatic dated valuation; forward values require complete eligible forecasts."""
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from app.models.financial import FinancialScope
from app.models.market import MarketSnapshot
from app.market.service import latest_snapshot
from app.market.valuation import calculate_valuation
from app.market.historical_pe import historical_pe_summary
from app.services.forecasting import next_quarter
from app.services.expectations import list_expectations

D=Decimal


def automated_valuation(db,company_id,scope,now=None):
    now=now or date.today()
    market=latest_snapshot(db,company_id,now)
    if not market:
        return {'status':'INSUFFICIENT_EVIDENCE','reason':'No permitted dated market observation is available. Automatic refresh will retry.','market':None,'metrics':{},'forward':{'status':'INSUFFICIENT_EVIDENCE'},'scenarios':[]}
    _,metrics=calculate_valuation(db,market,FinancialScope(scope))
    age=(now-market.as_of_date).days
    stale=age>7
    history=historical_pe_summary(db,company_id,market,FinancialScope(scope))
    output={'status':'STALE' if stale else 'DATED_OBSERVATION','reason':f'Price is {age} days old; not a current executable quote.' if stale else 'Dated closing observation; not a live executable quote.',
        'market':{'price':str(market.close_price),'date':market.as_of_date.isoformat(),'age_days':age,'stale':stale,
            'source_url':market.source_url,'provider':market.provider,'observed_at':market.observed_at.isoformat(),'fetched_at':market.fetched_at.isoformat(),
            'classification':'MANUAL_OBSERVATION' if market.data_origin=='MANUAL' else 'VERIFIED'},
        'metrics':metrics,'history':history,'forward':{'status':'INSUFFICIENT_EVIDENCE','reason':'Four consecutive eligible future quarterly EPS forecasts are required; a single quarter is never annualized.'},'scenarios':[]}
    # Use one original/latest snapshot per future period. Check capture against
    # today's date and distinguish this model from manually entered AI labels.
    rows=list_expectations(db,company_id,scope=scope,source='AI',metric='DILUTED_EPS',limit=200,automated=True,upcoming=True)['items']
    forecasts=sorted([r for r in rows if r['definition'].get('forecast') and date.fromisoformat(r['definition']['period'])>now
        and r['assessment_status']=='PENDING'],key=lambda r:r['definition']['period'])
    by_period={}
    for row in forecasts: by_period.setdefault(row['definition']['period'],row)
    chosen=list(by_period.values())[:4]
    if len(chosen)!=4 or any(next_quarter(date.fromisoformat(a['definition']['period']))!=date.fromisoformat(b['definition']['period']) for a,b in zip(chosen,chosen[1:])):
        return output
    eps={name:sum(D(r['definition']['forecast'][name]) for r in chosen) for name in ('low','base','high')}
    if eps['base']<=0:
        output['forward']['reason']='Forecast earnings are nonpositive; forward P/E is not meaningful.'; return output
    output['forward']={'status':'MODEL_ESTIMATE','eps':{k:str(v) for k,v in eps.items()},'pe':str(market.close_price/eps['base']),
        'forecast_revision_ids':[r['revision_id'] for r in chosen],'periods':[r['definition']['period'] for r in chosen],
        'method':'Price / sum of four consecutive future quarterly diluted-EPS forecasts; model estimates, not external consensus.'}
    window=history.get('one_year')
    if not window or eps['low']<=0:
        output['scenario_gap']='A sufficiently covered positive historical P/E range and positive EPS scenarios are required.'
        return output
    # Sensitivity grid separates business forecast uncertainty from multiple risk.
    for earnings_name,earnings in eps.items():
        for multiple_name in ('low','median','high'):
            multiple=D(window[multiple_name]); value=earnings*multiple
            output['scenarios'].append({'earnings_scenario':earnings_name,'multiple_scenario':multiple_name,
                'eps':str(earnings),'pe':str(multiple),'value':str(value),
                'margin_of_safety_pct':str((value-market.close_price)/value*100) if value>0 else None,
                'method':'Next-four-quarter model EPS × observed one-year historical P/E scenario; relative valuation sensitivity, not intrinsic value.'})
    ttm=metrics.get('ttm_eps',{}).get('value')
    if ttm and D(ttm)>0:
        output['implied_growth_pct']=str((market.close_price/D(window['median'])/D(ttm)-1)*100)
        output['implied_growth_method']='One-year EPS growth required to justify the dated price at the observed historical median P/E; zero assumed return, no claim of intrinsic value.'
    return output
