"""Owner-only inbound messages and durable quick-analysis work on the existing bot."""
import asyncio
import json
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select, text, update

from app.ai.analyst.service import configured_provider
from app.ai.analyst.validator import InvalidAnalysis
from app.ai.providers.base import LLMError
from app.ai.quick_analysis import (QuickOutput, SYSTEM_PROMPT, format_quick_analysis,
                                   source_references, validate_quick_output)
from app.config import settings
from app.models.telegram import TelegramInbox, TelegramQuery
from app.services.telegram_alerts import now, utc
from app.services.telegram_client import TelegramError, call_bot, send_message
from app.services.telegram_research import build_quick_context, portfolio_companies, queue_refresh, resolve_holding

NOT_IN_PORTFOLIO = "This company is not in your Almonds portfolio."
UNAVAILABLE = "Almonds AI analysis is temporarily unavailable. Please try again later. No paid model was used."
HELP = "Send a company name, ticker or maintained alias from your Almonds portfolio, for example ETERNAL or ICICI Bank."


def authorized(user_id, chat_id, chat_type="private"):
    # Both conditions are mandatory. No implicit authorization from alert destination.
    return (chat_type == "private" and str(user_id) in {str(value) for value in settings.telegram_allowed_user_ids}
            and str(chat_id) in {str(value) for value in settings.telegram_allowed_chat_ids})


def enabled():
    return bool(settings.telegram_enabled and settings.telegram_queries_enabled
        and settings.telegram_bot_token.get_secret_value().strip()
        and settings.telegram_allowed_user_ids and settings.telegram_allowed_chat_ids)


def query_status(db):
    return {"enabled": settings.telegram_queries_enabled, "configured": enabled(),
            "counts": dict(db.execute(select(TelegramQuery.status, func.count()).group_by(TelegramQuery.status)).all())}


def _lock(db, name):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": name})


def accept_update(db, incoming):
    """No portfolio access, persistence or response for unauthorized senders."""
    message = incoming.get("message")
    if not isinstance(message, dict) or type(incoming.get("update_id")) is not int:
        return None
    sender, chat = message.get("from"), message.get("chat")
    if not isinstance(sender, dict) or not isinstance(chat, dict) or sender.get("is_bot"):
        return None
    if not authorized(sender.get("id"), chat.get("id"), chat.get("type")):
        return None
    _lock(db, "almonds.telegram.accept")
    existing = db.scalar(select(TelegramQuery).where(TelegramQuery.update_id == incoming["update_id"]))
    if existing:
        return existing
    job = TelegramQuery(update_id=incoming["update_id"], user_id=str(sender["id"]), chat_id=str(chat["id"]),
                        status="PENDING", next_attempt_at=now(), attempts=0)
    content = message.get("text")
    content = content.strip() if isinstance(content, str) else ""
    if not content or len(content) > 200:
        job.message = HELP
    elif content.startswith("/"):
        command = content.split()[0].split("@")[0].lower()
        job.message = HELP if command in {"/start", "/help"} else "Unknown command. " + HELP
    else:
        recent = db.scalar(select(TelegramQuery).where(TelegramQuery.user_id == job.user_id,
            TelegramQuery.company_id.is_not(None)).order_by(TelegramQuery.id.desc()).limit(1))
        if recent and utc(recent.created_at) > now()-timedelta(seconds=settings.telegram_query_cooldown_seconds):
            job.message = "Your recent analysis request is queued or complete. Please wait a few seconds before another request."
        else:
            matches = resolve_holding(db, content)
            if not matches:
                job.message = NOT_IN_PORTFOLIO
            elif len(matches) > 1:
                job.message = "Please clarify with a ticker or ISIN: " + ", ".join(company.symbol for company in matches)
            else:
                company = matches[0]
                running = db.scalar(select(TelegramQuery.id).where(TelegramQuery.company_id == company.id,
                    TelegramQuery.status.in_(["PENDING", "PROCESSING", "SENDING"])).limit(1))
                if running:
                    job.message = "An analysis for this company is already in progress. Please wait for its reply."
                else:
                    job.company_id = company.id
    db.add(job)
    db.flush()
    return job


async def poll_updates(session_factory):
    if not enabled():
        return False
    token = str(uuid4())
    with session_factory() as db:
        _lock(db, "almonds.telegram.poll")
        inbox = db.get(TelegramInbox, 1)
        if inbox is None:
            inbox = TelegramInbox(id=1, next_update_id=0)
            db.add(inbox)
            db.flush()
        if inbox.lease_until and utc(inbox.lease_until) > now():
            return False
        offset = inbox.next_update_id
        inbox.lease_token, inbox.lease_until = token, now()+timedelta(seconds=settings.telegram_timeout_seconds+30)
        db.commit()
    try:
        updates = await call_bot("getUpdates", {"offset": offset, "limit": 20, "timeout": 0, "allowed_updates": ["message"]})
        if not isinstance(updates, list):
            raise TelegramError("Telegram returned invalid updates")
        for incoming in sorted((item for item in updates if isinstance(item, dict) and type(item.get("update_id")) is int), key=lambda item: item["update_id"]):
            if incoming["update_id"] < offset:
                continue
            with session_factory() as db:
                inbox = db.get(TelegramInbox, 1)
                if inbox.lease_token != token:
                    return False
                accept_update(db, incoming)
                # Accepted request and cursor commit together; reconnects are idempotent.
                inbox.next_update_id = incoming["update_id"]+1
                db.commit()
        return True
    finally:
        with session_factory() as db:
            db.execute(update(TelegramInbox).where(TelegramInbox.id == 1, TelegramInbox.lease_token == token)
                .values(lease_token=None, lease_until=None))
            db.commit()


def _claim(session_factory):
    with session_factory() as db:
        _lock(db, "almonds.telegram.query.claim")
        # Sends interrupted by a crash have unknown delivery outcomes; never replay.
        db.execute(update(TelegramQuery).where(TelegramQuery.status == "SENDING", TelegramQuery.lease_until <= now())
            .values(status="UNCERTAIN", error="SEND_INTERRUPTED", lease_token=None, lease_until=None))
        job = db.scalar(select(TelegramQuery).where(TelegramQuery.status.in_(["PENDING", "PROCESSING"]),
            TelegramQuery.next_attempt_at <= now(),
            (TelegramQuery.lease_until.is_(None) | (TelegramQuery.lease_until <= now())))
            .order_by(TelegramQuery.id).with_for_update(skip_locked=True).limit(1))
        if not job:
            db.commit()
            return None
        token = str(uuid4())
        changed = db.execute(update(TelegramQuery).where(TelegramQuery.id == job.id,
            TelegramQuery.status.in_(["PENDING", "PROCESSING"]),
            (TelegramQuery.lease_until.is_(None) | (TelegramQuery.lease_until <= now())))
            .values(status="PROCESSING", lease_token=token, lease_until=now()+timedelta(minutes=5),
                    attempts=TelegramQuery.attempts+1))
        db.commit()
        return (job.id, token) if changed.rowcount == 1 else None


def _holding(db, company_id):
    return next((company for company in portfolio_companies(db) if company.id == company_id), None)


def _prepare(session_factory, query_id, token):
    with session_factory() as db:
        job = db.get(TelegramQuery, query_id)
        if job.lease_token != token:
            return None
        if not authorized(job.user_id, job.chat_id):
            job.status, job.error, job.lease_token, job.lease_until = "REJECTED", "AUTHORIZATION_REVOKED", None, None
            db.commit()
            return None
        if job.message:
            return job.chat_id, None, None
        company = _holding(db, job.company_id)
        if company is None:
            job.message = NOT_IN_PORTFOLIO
            db.commit()
            return job.chat_id, None, None
        context = build_quick_context(db, company)
        if settings.research_automation_enabled:
            queued = queue_refresh(db, company)
            if queued:
                context.gaps.append("Missing/stale inputs are queued for refresh; this response uses stored evidence.")
        route = ("QUICK_V1:" + settings.ai_provider + ":" +
                 (settings.ai_model if settings.ai_provider == "openai_compatible" else settings.ollama_model))
        # Endpoint changes must invalidate the same-named model's cache.
        import hashlib
        route += ":" + hashlib.sha256((settings.ai_api_base_url if settings.ai_provider == "openai_compatible"
                                      else settings.ollama_base_url).encode()).hexdigest()[:16]
        cached = db.scalar(select(TelegramQuery).where(TelegramQuery.id != job.id,
            TelegramQuery.company_id == company.id, TelegramQuery.user_id == job.user_id, TelegramQuery.chat_id == job.chat_id,
            TelegramQuery.status == "SENT", TelegramQuery.context_hash == context.hash,
            TelegramQuery.model_route == route, TelegramQuery.structured_output.is_not(None),
            TelegramQuery.analyzed_at > now()-timedelta(seconds=settings.telegram_query_cache_seconds))
            .order_by(TelegramQuery.analyzed_at.desc()).limit(1))
        job.context_hash, job.model_route = context.hash, route
        job.latest_data_at, job.evidence_registry = context.latest_data_at, context.registry
        db.commit()
        return job.chat_id, context, (cached.structured_output, utc(cached.analyzed_at)) if cached else None


def _save_result(session_factory, query_id, token, *, message, output=None, context=None, analyzed_at=None, error=None):
    with session_factory() as db:
        values = {"message": message, "error": error}
        if output:
            values.update(structured_output=output.model_dump(), analyzed_at=analyzed_at,
                          source_references=source_references(output, context.registry))
        db.execute(update(TelegramQuery).where(TelegramQuery.id == query_id, TelegramQuery.lease_token == token).values(**values))
        db.commit()


async def answer_one(session_factory):
    if not enabled():
        return False
    claimed = _claim(session_factory)
    if not claimed:
        return False
    query_id, token = claimed
    try:
        prepared = await asyncio.to_thread(_prepare, session_factory, query_id, token)
        if prepared is None:
            return False
        chat_id, context, cached = prepared
        if context:
            if not context.registry:
                _save_result(session_factory, query_id, token,
                    message="No usable source evidence is available for this holding yet. Existing research refresh is queued when enabled. Please try again after ingestion completes.",
                    error="NO_EVIDENCE")
            else:
                try:
                    # Re-check the configured free route even before using its cache.
                    provider = configured_provider(timeout=settings.telegram_query_timeout_seconds)
                    if cached:
                        output = validate_quick_output(json.dumps(cached[0]), context)
                        analyzed_at = cached[1]
                    else:
                        try:
                            await send_message(chat_id, "I’m checking the latest available Almonds evidence for this holding. This may take a minute.")
                        except TelegramError:
                            pass  # An acknowledgement never controls the final response.
                        raw = await asyncio.wait_for(asyncio.to_thread(provider.generate,
                            SYSTEM_PROMPT, context.text, QuickOutput.model_json_schema()), settings.telegram_query_timeout_seconds)
                        output = validate_quick_output(raw, context)
                        analyzed_at = now()
                    _save_result(session_factory, query_id, token, message=format_quick_analysis(output, context, analyzed_at),
                                 output=output, context=context, analyzed_at=analyzed_at)
                except (LLMError, InvalidAnalysis, TimeoutError) as exc:
                    # Only bounded, known codes are persisted, never provider text/secrets.
                    code = "MODEL_TIMEOUT" if isinstance(exc, TimeoutError) else "INVALID_MODEL_OUTPUT" if isinstance(exc, InvalidAnalysis) else "FREE_PROVIDER_UNAVAILABLE"
                    _save_result(session_factory, query_id, token, message=UNAVAILABLE, error=code)
    except Exception:
        _save_result(session_factory, query_id, token, message=UNAVAILABLE, error="QUERY_PREPARATION_FAILED")

    with session_factory() as db:
        job = db.get(TelegramQuery, query_id)
        if job.lease_token != token:
            return False
        # Recheck both authorization and holdings immediately before disclosing data.
        if not enabled() or not authorized(job.user_id, job.chat_id):
            job.status, job.error, job.lease_token, job.lease_until = "REJECTED", "AUTHORIZATION_REVOKED", None, None
            db.commit()
            return False
        if job.company_id and not _holding(db, job.company_id):
            job.message = NOT_IN_PORTFOLIO
        job.status = "SENDING"
        db.commit()
        chat_id, message = job.chat_id, job.message
        mode = "HTML" if job.structured_output and message != NOT_IN_PORTFOLIO else None
    error, receipt = None, None
    try:
        receipt = await send_message(chat_id, message, **({"parse_mode": mode} if mode else {}))
    except TelegramError as exc:
        error = exc
    except Exception:
        error = TelegramError("Send outcome unknown", uncertain=True)
    with session_factory() as db:
        job = db.get(TelegramQuery, query_id)
        if job.lease_token != token:
            return False
        if error:
            retry = error.retryable and not error.uncertain and job.attempts < 4
            job.status = "UNCERTAIN" if error.uncertain else "PENDING" if retry else "FAILED"
            job.error = "TELEGRAM_SEND_UNCERTAIN" if error.uncertain else "TELEGRAM_SEND_FAILED"
            job.next_attempt_at = now()+timedelta(seconds=max(error.retry_after or 0, 30*2**min(job.attempts, 5)))
        else:
            job.status, job.message_id = "SENT", receipt
        job.lease_until = job.lease_token = None
        db.commit()
    return error is None
