from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.services.telegram_alerts import delivery_status
from app.services.telegram_client import TelegramError, configured, send_message
from app.models.financial import FinancialPeriod, FinancialStatus
from app.models.analysis import AIAnalysis
from app.models.telegram import TelegramDelivery
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.schemas.earnings_notification import EarningsNotification

router = APIRouter(prefix="/api/telegram", tags=["telegram"])


@router.get("/earnings/{period_id}/preview", response_model=EarningsNotification)
def preview(period_id: int, db: Session = Depends(get_db)):
    from app.services.earnings_notification import build_notification
    period = db.get(FinancialPeriod, period_id)
    if period is None or period.status != FinancialStatus.VALIDATED:
        raise HTTPException(404, "Validated financial period not found")
    return build_notification(db, period)


@router.post("/earnings/{period_id}/queue")
def queue_result(period_id: int, analysis_id: int | None = None, db: Session = Depends(get_db)):
    from app.services.telegram_alerts import enqueue_result
    period = db.get(FinancialPeriod, period_id)
    if period is None:
        raise HTTPException(404, "Financial period not found")
    analysis = db.get(AIAnalysis, analysis_id) if analysis_id is not None else None
    if analysis_id is not None and analysis is None:
        raise HTTPException(404, "Analysis version not found")
    try:
        job = enqueue_result(db, period, analysis=analysis)
        db.commit()
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    except IntegrityError:
        db.rollback()
        job = enqueue_result(db, period, analysis=analysis)
        db.commit()
    return {"delivery_id": job.id, "status": job.status, "idempotency_key": job.idempotency_key}


@router.post("/deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: int, db: Session = Depends(get_db)):
    from app.services.telegram_alerts import now
    job = db.get(TelegramDelivery, delivery_id)
    if job is None:
        raise HTTPException(404, "Delivery not found")
    if job.status != "FAILED":
        raise HTTPException(409, "Only definitively failed deliveries can be retried; uncertain sends require checking the chat")
    job.status, job.attempts, job.next_attempt_at, job.last_error = "PENDING", 0, now(), None
    db.commit()
    return {"delivery_id": job.id, "status": job.status}


@router.get("/status")
def status(db: Session = Depends(get_db)):
    from app.services.telegram_queries import query_status
    return {**delivery_status(db), "queries": query_status(db)}


@router.post("/test")
async def test_connection():
    if not settings.telegram_enabled or not configured():
        raise HTTPException(409, "Connect and enable Telegram before sending a test")
    try:
        receipt = await send_message(settings.telegram_chat_id.strip(),
            "Almonds is connected. New portfolio disclosures will include a short AI thesis and source links.")
    except TelegramError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"status": "sent", "message_id": receipt}
