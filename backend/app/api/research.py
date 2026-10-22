"""Read-only autonomous research views; retries enqueue bounded background work."""
from datetime import date
from fastapi import APIRouter,Depends,Query,HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.analysis import _company
from app.database import get_db
from app.models.research import ResearchJob
from app.schemas.expectations import Scope
from app.schemas.research import ForecastPage,AutomationRead,ForecastAccuracyRead,AutomatedValuationRead
from app.services.expectations import list_expectations
from app.services.forecasting import VERSION,METRICS
from app.services.forecast_accuracy import forecast_accuracy, comparisons
from app.schemas.forecast_accuracy import ComparisonPage, ScoringJobRead
from app.models.financial import MetricCode, PeriodType
from app.services.autonomous_valuation import automated_valuation
from app.services.research_jobs import status,ensure_jobs

router=APIRouter(prefix='/api/companies/{symbol}',tags=['autonomous research'])


@router.get('/forecasts',response_model=ForecastPage)
def forecasts(symbol:str,scope:Scope='CONSOLIDATED',original:bool=False,upcoming:bool=False,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),db:Session=Depends(get_db)):
    company=_company(db,symbol)
    job=db.scalar(select(ResearchJob).where(ResearchJob.company_id==company.id,ResearchJob.kind=='RESEARCH'))
    availability=(job.result or {}).get('scopes',{}).get(scope,[]) if job else []
    if not availability:
        availability=[{'metric':metric,'status':'INSUFFICIENT_EVIDENCE','reason':'Automatic verified-history assessment is queued; no manual estimate is required.'} for metric in METRICS]
    return {**list_expectations(db,company.id,scope=scope,source='AI',offset=offset,limit=limit,automated=True,original=original,upcoming=upcoming),
        'availability':availability,'engine_version':VERSION}


@router.get('/forecasts/accuracy',response_model=ForecastAccuracyRead)
def accuracy(symbol:str,scope:Scope='CONSOLIDATED',metric:MetricCode|None=None,
             horizon_quarters:int|None=Query(None,ge=1,le=40),period_type:PeriodType|None=None,
             date_from:date|None=None,date_to:date|None=None,offset:int=Query(0,ge=0),
             limit:int=Query(50,ge=1,le=200),db:Session=Depends(get_db)):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422,'date_from must not be after date_to')
    return forecast_accuracy(db,_company(db,symbol).id,scope,metric=metric,horizon_quarters=horizon_quarters,
                             period_type=period_type,date_from=date_from,date_to=date_to,offset=offset,limit=limit)


@router.get('/forecasts/comparisons',response_model=ComparisonPage)
def forecast_comparisons(symbol:str,scope:Scope='CONSOLIDATED',metric:MetricCode|None=None,
             horizon_quarters:int|None=Query(None,ge=1,le=40),period_type:PeriodType|None=None,
             date_from:date|None=None,date_to:date|None=None,exclusions_only:bool=False,
             offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200),db:Session=Depends(get_db)):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422,'date_from must not be after date_to')
    return comparisons(db,_company(db,symbol).id,scope,metric=metric,horizon_quarters=horizon_quarters,
                       period_type=period_type,date_from=date_from,date_to=date_to,offset=offset,limit=limit,
                       exclusions_only=exclusions_only)


@router.post('/forecasts/evaluate',response_model=ScoringJobRead,status_code=202)
def queue_scoring(symbol:str,db:Session=Depends(get_db)):
    from app.services.research_jobs import enqueue_accuracy
    job = enqueue_accuracy(db,_company(db,symbol).id)
    db.commit()
    return {'job_id':job.id,'status':job.status}


@router.get('/research/status',response_model=AutomationRead)
def automation(symbol:str,db:Session=Depends(get_db)):
    return status(db,_company(db,symbol).id)


@router.post('/research/retry',response_model=AutomationRead,status_code=202)
def retry(symbol:str,db:Session=Depends(get_db)):
    # Preserve persisted backoff and active leases. A repeated click cannot bypass
    # provider limits. Newly added source mappings receive new due jobs.
    company=_company(db,symbol); ensure_jobs(db,company); db.commit()
    return status(db,company.id)


@router.get('/valuation/automated',response_model=AutomatedValuationRead)
def valuation(symbol:str,scope:Scope='CONSOLIDATED',db:Session=Depends(get_db)):
    return automated_valuation(db,_company(db,symbol).id,scope)
