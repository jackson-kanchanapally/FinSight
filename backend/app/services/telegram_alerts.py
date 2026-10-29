"""One source-linked AI event thesis per new live disclosure, delivered off-path."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, update

from app.ai.events.service import analyze_event
from app.config import settings
from app.models.event import CompanyEvent, DataOrigin
from app.models.event import EventType
from app.models.financial import FinancialPeriod, FinancialStatus, FinancialScope, PeriodType
from app.models.telegram import TelegramDelivery
from app.services.telegram_client import TelegramError, configured, send_message
from app.services.result_sources import digest


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def now():
    return datetime.now(timezone.utc)


def _important_event_types():
    """Return the configured allowlist for automatic disclosure alerts."""
    result = set()
    for value in settings.telegram_important_event_types.split(","):
        value = value.strip().upper()
        if not value:
            continue
        try:
            result.add(EventType(value))
        except ValueError:
            continue
    return result


def enqueue_event(db, event):
    """Called before the first event commit; never replay historical imports."""
    current = now()
    if (not settings.telegram_enabled or not configured()
            or event.data_origin != DataOrigin.LIVE
            or event.event_type not in _important_event_types()
            or utc(event.published_at) < current-timedelta(hours=settings.telegram_max_event_age_hours)
            or utc(event.published_at) > current+timedelta(days=1)):
        return
    db.add(TelegramDelivery(event_id=event.id, chat_id=settings.telegram_chat_id.strip(),
                           status="PENDING", attempts=0, next_attempt_at=current, version="automatic-v2",
                           idempotency_key=digest(["event", event.id, "automatic-v2", settings.telegram_chat_id.strip()])))


def enqueue_result(db, period, *, analysis=None):
    """Explicit requests may alert on manual/historical results; never implicitly replay them."""
    if not configured() or not settings.telegram_enabled:
        raise ValueError("Telegram is not connected and enabled")
    if period.status != FinancialStatus.VALIDATED:
        raise ValueError("A validated result is required")
    if analysis is not None and (analysis.financial_period_id != period.id or analysis.status not in {"SUCCESS", "VERIFIED_FALLBACK"}):
        raise ValueError("Analysis version does not belong to this validated result")
    version = f"analysis-{analysis.id}" if analysis else "automatic-v2"
    key = digest(["earnings", period.id, version, settings.telegram_chat_id.strip()])
    existing = db.scalar(select(TelegramDelivery).where(TelegramDelivery.idempotency_key == key))
    if existing is None:
        existing = db.scalar(select(TelegramDelivery).where(TelegramDelivery.event_id == period.source_event_id,
            TelegramDelivery.chat_id == settings.telegram_chat_id.strip(), TelegramDelivery.version == version))
    if existing:
        if existing.financial_period_id and existing.financial_period_id != period.id:
            raise ValueError("This event version already targets another reporting basis or period; select a deliberate analysis version")
        if existing.status in {"PENDING", "RETRY"} and existing.financial_period_id is None and not existing.message:
            existing.financial_period_id = period.id
        return existing
    job = TelegramDelivery(event_id=period.source_event_id, chat_id=settings.telegram_chat_id.strip(), version=version,
        idempotency_key=key, financial_analysis_id=analysis.id if analysis else None,
        financial_period_id=period.id,
        status="PENDING", attempts=0, next_attempt_at=now())
    db.add(job)
    db.flush()
    return job


def _claim(session_factory):
    current, token = now(), str(uuid4())
    with session_factory() as db:
        # Oldest first also enforces provider cooldown after a 429.
        job = db.scalar(select(TelegramDelivery).where(
            TelegramDelivery.status.in_(["PENDING", "RETRY", "PROCESSING"])
        ).order_by(TelegramDelivery.id).limit(1))
        if job is None or utc(job.next_attempt_at) > current:
            return None
        if job.lease_until and utc(job.lease_until) > current:
            return None
        if job.sending_part is not None:
            job.status, job.last_error = "UNCERTAIN", "Worker stopped during a Telegram send; verify the chat before any deliberate resend"
            job.lease_until = job.lease_token = None
            db.commit()
            return None
        changed = db.execute(update(TelegramDelivery).execution_options(synchronize_session="fetch").where(
            TelegramDelivery.id == job.id,
            TelegramDelivery.status.in_(["PENDING", "RETRY", "PROCESSING"]),
            (TelegramDelivery.lease_until.is_(None) | (TelegramDelivery.lease_until <= current)),
        ).values(status="PROCESSING", lease_token=token,
                 lease_until=current+timedelta(minutes=30), attempts=TelegramDelivery.attempts+1))
        db.commit()
        return (job.id, token) if changed.rowcount == 1 else None


def _render(event, record):
    from app.services.telegram_formatter import format_event
    return format_event(event, record)


def _prepare(session_factory, delivery_id, token):
    with session_factory() as db:
        job = db.get(TelegramDelivery, delivery_id)
        if job is None or job.lease_token != token:
            return None
        event = db.get(CompanyEvent, job.event_id)
        # Also discard routine events left in the queue before the importance
        # filter was enabled. Explicit earnings queue requests have a period ID.
        if (job.financial_period_id is None and
                (event is None or event.event_type not in _important_event_types())):
            job.status, job.last_error = "SUPERSEDED", "Filtered by Telegram important-event settings"
            job.lease_token = job.lease_until = None
            db.commit()
            return None
        if job.message_parts:
            index = len(job.message_ids or [])
            return (job.chat_id, job.message_parts[index]) if index < len(job.message_parts) else None
        if job.message:
            return job.chat_id, job.message
        if event.event_type == EventType.FINANCIAL_RESULTS:
            from app.ai.analyst.service import analyze_period
            from app.models.analysis import AIAnalysis
            from app.services.earnings_notification import build_notification
            from app.services.telegram_formatter import format_earnings
            period = db.get(FinancialPeriod, job.financial_period_id) if job.financial_period_id else db.scalar(select(FinancialPeriod).where(FinancialPeriod.company_id == event.company_id,
                FinancialPeriod.source_event_id == event.id, FinancialPeriod.status == FinancialStatus.VALIDATED)
                .order_by(FinancialPeriod.period_end.desc(), (FinancialPeriod.scope == FinancialScope.CONSOLIDATED).desc(),
                          (FinancialPeriod.period_type == PeriodType.QUARTERLY).desc()).limit(1))
            if period is not None and (period.company_id != event.company_id or period.status != FinancialStatus.VALIDATED):
                raise TelegramError("Requested result is not a compatible validated period", retryable=False)
            if period is None:
                from app.models.result_evidence import ResultVerification
                matches = [row.period_id for row in db.scalars(select(ResultVerification).where(ResultVerification.status == "VERIFIED"))
                           if row.details.get("comparison_event_id") == event.id]
                period = db.scalar(select(FinancialPeriod).where(FinancialPeriod.id.in_(matches),
                    FinancialPeriod.company_id == event.company_id, FinancialPeriod.status == FinancialStatus.VALIDATED)
                    .order_by(FinancialPeriod.period_end.desc(), (FinancialPeriod.scope == FinancialScope.CONSOLIDATED).desc(),
                              (FinancialPeriod.period_type == PeriodType.QUARTERLY).desc()).limit(1)) if matches else None
            if period is None:
                raise TelegramError("Earnings alert is waiting for validated financial ingestion")
            key = digest(["earnings", period.id, job.version, job.chat_id])
            duplicate = db.scalar(select(TelegramDelivery).where(
                ((TelegramDelivery.idempotency_key == key) | ((TelegramDelivery.event_id == period.source_event_id)
                    & (TelegramDelivery.version == job.version) & (TelegramDelivery.chat_id == job.chat_id))),
                TelegramDelivery.id != job.id))
            if duplicate:
                job.status, job.last_error, job.lease_token, job.lease_until = "SUPERSEDED", "Result version already queued or delivered", None, None
                db.commit()
                return None
            job.idempotency_key = key
            job.financial_period_id = period.id
            db.commit()
            record = db.get(AIAnalysis, job.financial_analysis_id) if job.financial_analysis_id else analyze_period(db, event.company, period)
            notification = build_notification(db, period, record, delivery_id=job.id)
            parts = format_earnings(notification)
            changed = db.execute(update(TelegramDelivery).where(TelegramDelivery.id == delivery_id,
                TelegramDelivery.lease_token == token).values(notification=notification.model_dump(mode="json"),
                message_parts=parts, message=parts[0], parse_mode="HTML", message_ids=[],
                financial_analysis_id=record.id, ai_status=notification.ai_status))
            db.commit()
            return (job.chat_id, parts[0]) if changed.rowcount == 1 else None
        record = analyze_event(db, event)
        message = _render(event, record)
        changed = db.execute(update(TelegramDelivery).where(
            TelegramDelivery.id == delivery_id, TelegramDelivery.lease_token == token,
        ).values(message=message, message_parts=[message], message_ids=[], parse_mode="HTML",
                 analysis_id=record.id, ai_status=record.status))
        db.commit()
        return (job.chat_id, message) if changed.rowcount == 1 else None


async def deliver_one(session_factory):
    if not settings.telegram_enabled or not configured():
        return False
    claim = _claim(session_factory)
    if claim is None:
        return False
    delivery_id, token = claim
    error, receipt, prepared, send_started = None, None, None, False
    try:
        prepared = await asyncio.to_thread(_prepare, session_factory, delivery_id, token)
        if prepared is None:
            return False
        # Pausing alerts during generation also pauses the send.
        if not settings.telegram_enabled:
            raise TelegramError("Telegram alerts paused")
        with session_factory() as db:
            job = db.get(TelegramDelivery, delivery_id)
            if job.lease_token != token:
                return False
            mode = job.parse_mode
            job.sending_part = len(job.message_ids or [])
            db.commit()
        send_started = True
        receipt = await send_message(*prepared, **({"parse_mode": mode} if mode else {}))
    except TelegramError as exc:
        error = exc
    except Exception:
        error = TelegramError("Telegram alert preparation failed", uncertain=send_started)
    with session_factory() as db:
        job = db.get(TelegramDelivery, delivery_id)
        if job is None or job.lease_token != token:
            return False
        values = {"lease_until": None, "lease_token": None}
        if error:
            retry = error.retryable and not error.uncertain and job.attempts < 8
            delay = max(30 * 2**min(job.attempts-1, 7), error.retry_after or 0)
            values.update(status="UNCERTAIN" if error.uncertain else "RETRY" if retry else "FAILED", last_error=str(error),
                          next_attempt_at=now()+timedelta(seconds=delay))
            if not error.uncertain:
                values["sending_part"] = None
        else:
            receipts = list(job.message_ids or []) + [receipt]
            complete = len(receipts) >= len(job.message_parts or [job.message])
            values.update(status="SENT" if complete else "PENDING", sent_at=now() if complete else None,
                          message_id=receipt, message_ids=receipts, sending_part=None, last_error=None,
                          next_attempt_at=now()+timedelta(seconds=settings.telegram_poll_seconds))
        db.execute(update(TelegramDelivery).where(
            TelegramDelivery.id == delivery_id, TelegramDelivery.lease_token == token).values(**values))
        db.commit()
    return error is None


def delivery_status(db):
    counts = dict(db.execute(select(TelegramDelivery.status, func.count())
                            .group_by(TelegramDelivery.status)).all())
    latest = db.scalar(select(TelegramDelivery).order_by(TelegramDelivery.id.desc()).limit(1))
    error_job = db.scalar(select(TelegramDelivery).where(TelegramDelivery.last_error.is_not(None))
                          .order_by(TelegramDelivery.id.desc()).limit(1))
    last_sent = db.scalar(select(func.max(TelegramDelivery.sent_at)))
    return {"enabled": settings.telegram_enabled, "configured": configured(),
            "counts": counts, "last_sent_at": last_sent,
            "latest_status": latest.status if latest else None,
            "last_error": error_job.last_error if error_job else None,
            "latest_ai_status": latest.ai_status if latest else None}
