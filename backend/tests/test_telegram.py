import asyncio
import importlib.util
import json
import logging
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ai.events import service as analyst
from app.ai.providers.base import LLMError
from app.config import settings
from app.database import Base
from app.ingestion.normalizer import NormalizedEvent
from app.ingestion.storage import LocalDocumentStorage
from app.models.company import Company
from app.models.event import CompanyEvent, DataOrigin, EventType, PublicationPrecision
from app.models.telegram import TelegramDelivery
from app.services import telegram_alerts as alerts
from app.services.events.event_service import save_event
from app.services.telegram_client import TelegramError, call_bot, send_message


class BriefProvider:
    name, model = "test", "test-model"

    def __init__(self, fail=False):
        self.calls, self.fail = 0, fail

    def generate(self, system, user, schema):
        self.calls += 1
        if self.fail:
            raise LLMError("OLLAMA_UNAVAILABLE")
        context = json.loads(user.split("CONTEXT:\n", 1)[1])
        evidence = [context["evidence"][0]["id"]]
        def claim(text):
            return {"text": text, "evidence_ids": evidence}
        return json.dumps({
            "summary": claim("The company announced a dividend."),
            "why_it_matters": claim("The announcement concerns shareholder distributions."),
            "what_next": claim("Watch for the disclosed payment arrangements."),
            "recommendation": {"action": "WATCH", "reason": "Review the official disclosure.", "evidence_ids": evidence},
            "management_says": None,
        })


@pytest.fixture
def telegram_env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'telegram.sqlite'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:TEST_SECRET"))
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    provider = BriefProvider()
    monkeypatch.setattr(analyst, "configured_provider", lambda: provider)
    with factory() as db:
        db.add(Company(symbol="ABC", name="ABC Ltd", exchange="NSE"))
        db.commit()
    yield factory, LocalDocumentStorage(tmp_path / "pdfs"), provider
    engine.dispose()


def save(factory, storage, *, key="one", origin=DataOrigin.LIVE, age=0):
    with factory() as db:
        company = db.scalar(select(Company))
        normalized = NormalizedEvent(
            company_id=company.id, symbol=company.symbol, source="NSE", event_type=EventType.DIVIDEND,
            title=f"Dividend announcement {key}", description=None,
            published_at=alerts.now()-timedelta(hours=age), external_id=key,
            source_url=f"https://nsearchives.nseindia.com/{key}", document_url=None,
            raw_payload=None, data_origin=origin, publication_precision=PublicationPrecision.TIMESTAMP,
            period_end_date=None, fiscal_year=None, quarter=None, reporting_basis=None,
        )
        return save_event(db, normalized, None, None, storage)


def test_live_event_and_alert_are_saved_once(telegram_env):
    factory, storage, _ = telegram_env
    assert save(factory, storage)[0]
    assert not save(factory, storage)[0]
    with factory() as db:
        assert len(list(db.scalars(select(CompanyEvent)))) == 1
        queued = list(db.scalars(select(TelegramDelivery)))
        assert len(queued) == 1
        assert queued[0].chat_id == "456"


@pytest.mark.parametrize("origin,age,enabled,token", [
    (DataOrigin.FIXTURE, 0, True, "123:secret"),
    (DataOrigin.MANUAL, 0, True, "123:secret"),
    (DataOrigin.LIVE, 72, True, "123:secret"),
    (DataOrigin.LIVE, 0, False, "123:secret"),
    (DataOrigin.LIVE, 0, True, ""),
])
def test_no_demo_manual_old_or_disabled_alerts(telegram_env, monkeypatch, origin, age, enabled, token):
    factory, storage, _ = telegram_env
    monkeypatch.setattr(settings, "telegram_enabled", enabled)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr(token))
    save(factory, storage, origin=origin, age=age)
    with factory() as db:
        assert db.scalar(select(CompanyEvent)) is not None
        assert db.scalar(select(TelegramDelivery)) is None


def test_cited_ai_thesis_delivered_once_and_receipt_saved(telegram_env, monkeypatch):
    factory, storage, provider = telegram_env
    save(factory, storage)
    messages = []
    async def send(chat_id, message):
        messages.append((chat_id, message))
        return 99
    monkeypatch.setattr(alerts, "send_message", send)
    assert asyncio.run(alerts.deliver_one(factory))
    assert not asyncio.run(alerts.deliver_one(factory))
    assert provider.calls == 1
    assert len(messages) == 1
    chat_id, message = messages[0]
    assert chat_id == "456"
    for text in ["Almonds · ABC", "AI thesis", "Why it matters", "Watch next", "[E1]", "Source: https://"]:
        assert text in message
    with factory() as db:
        job = db.scalar(select(TelegramDelivery))
        assert (job.status, job.message_id, job.ai_status) == ("SENT", 99, "SUCCESS")
        assert job.analysis_id is not None and job.sent_at is not None
        assert job.lease_token is None


def test_ai_unavailable_still_sends_disclosure_with_explicit_label(telegram_env, monkeypatch):
    factory, storage, provider = telegram_env
    provider.fail = True
    save(factory, storage)
    messages = []
    async def send(chat_id, message):
        messages.append(message)
        return 100
    monkeypatch.setattr(alerts, "send_message", send)
    assert asyncio.run(alerts.deliver_one(factory))
    assert "AI thesis unavailable" in messages[0]
    assert "Why it matters" not in messages[0]
    assert "https://nsearchives" in messages[0]
    with factory() as db:
        assert db.scalar(select(TelegramDelivery)).ai_status == "FAILED"


def test_rate_limit_persists_cooldown_and_reuses_thesis_on_retry(telegram_env, monkeypatch):
    factory, storage, provider = telegram_env
    save(factory, storage)
    save(factory, storage, key="two")
    current = alerts.now()
    monkeypatch.setattr(alerts, "now", lambda: current)
    async def limited(*args):
        raise TelegramError("Telegram API error 429", retry_after=120)
    monkeypatch.setattr(alerts, "send_message", limited)
    assert not asyncio.run(alerts.deliver_one(factory))
    with factory() as db:
        job = db.scalar(select(TelegramDelivery).order_by(TelegramDelivery.id))
        assert job.status == "RETRY" and job.message
        assert alerts.utc(job.next_attempt_at) >= current+timedelta(seconds=120)
    assert not asyncio.run(alerts.deliver_one(factory))
    assert provider.calls == 1
    monkeypatch.setattr(alerts, "now", lambda: current+timedelta(seconds=121))
    async def success(*args):
        return 101
    monkeypatch.setattr(alerts, "send_message", success)
    assert asyncio.run(alerts.deliver_one(factory))
    assert provider.calls == 1
    with factory() as db:
        assert db.scalar(select(TelegramDelivery).order_by(TelegramDelivery.id)).attempts == 2


def test_claim_leases_and_recovery(telegram_env, monkeypatch):
    factory, storage, _ = telegram_env
    save(factory, storage)
    current = alerts.now()
    first = alerts._claim(factory)
    assert first is not None
    assert alerts._claim(factory) is None
    monkeypatch.setattr(alerts, "now", lambda: current+timedelta(minutes=31))
    second = alerts._claim(factory)
    assert second[0] == first[0] and second[1] != first[1]
    assert alerts._prepare(factory, *first) is None


def test_permanent_failure_and_pause(telegram_env, monkeypatch):
    factory, storage, provider = telegram_env
    save(factory, storage)
    monkeypatch.setattr(settings, "telegram_enabled", False)
    assert not asyncio.run(alerts.deliver_one(factory))
    assert provider.calls == 0
    monkeypatch.setattr(settings, "telegram_enabled", True)
    async def blocked(*args):
        raise TelegramError("Telegram API error 403", retryable=False)
    monkeypatch.setattr(alerts, "send_message", blocked)
    assert not asyncio.run(alerts.deliver_one(factory))
    with factory() as db:
        assert db.scalar(select(TelegramDelivery)).status == "FAILED"
        status = alerts.delivery_status(db)
        assert status["counts"] == {"FAILED": 1}
        assert "TEST_SECRET" not in json.dumps(status, default=str)


def test_long_messages_preserve_source_and_utf16_limit(telegram_env):
    factory, storage, _ = telegram_env
    save(factory, storage)
    with factory() as db:
        event = db.scalar(select(CompanyEvent))
        event.title = "🌰" * 500
        event.source_url = "https://example.com/" + "a" * 2000
        event.document_url = "https://example.com/" + "b" * 2000
        message = alerts._render(event, None)
        assert len(message.encode("utf-16-le")) <= 8192
        assert message.endswith(event.source_url)


def test_transport_redacts_token_and_posts_plain_text(monkeypatch, caplog):
    token = "123:VERY_SECRET"
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr(token))
    def respond(request):
        payload = json.loads(request.content)
        assert payload["chat_id"] == "456"
        assert payload["text"] == "hello <world>"
        assert "parse_mode" not in payload
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})
    with caplog.at_level(logging.INFO, logger="httpx"):
        assert asyncio.run(send_message("456", "hello <world>", transport=httpx.MockTransport(respond))) == 7
    assert token not in caplog.text
    assert "[REDACTED]" in caplog.text


@pytest.mark.parametrize("code,retryable", [(400, False), (401, False), (403, False), (429, True), (500, True)])
def test_transport_sanitizes_errors_and_retry_after(monkeypatch, code, retryable):
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:secret"))
    transport = httpx.MockTransport(lambda request: httpx.Response(code, json={
        "ok": False, "error_code": code, "description": "secret data", "parameters": {"retry_after": 90}}))
    with pytest.raises(TelegramError) as caught:
        asyncio.run(call_bot("getMe", transport=transport))
    assert caught.value.retryable is retryable
    assert caught.value.retry_after == 90
    assert "secret" not in str(caught.value)


def test_status_and_explicit_test_endpoint(client, monkeypatch):
    from app.api import telegram
    monkeypatch.setattr(settings, "telegram_enabled", False)
    assert client.get("/api/telegram/status").json()["enabled"] is False
    assert client.post("/api/telegram/test").status_code == 409
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:secret"))
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    async def send(chat_id, message):
        assert chat_id == "456" and "Almonds" in message
        return 42
    monkeypatch.setattr(telegram, "send_message", send)
    assert client.post("/api/telegram/test").json() == {"status": "sent", "message_id": 42}
    assert "secret" not in client.get("/api/telegram/status").text


def test_setup_preserves_env_and_hides_token_permissions(tmp_path):
    path = Path(__file__).parents[1] / "scripts/setup_telegram.py"
    spec = importlib.util.spec_from_file_location("setup_telegram", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    env = tmp_path / ".env"
    env.write_text("# existing\nDATABASE_NAME=keep\nTELEGRAM_ENABLED=false\n")
    module.write_settings(env, {"TELEGRAM_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "123:secret"})
    assert "DATABASE_NAME=keep" in env.read_text()
    assert env.read_text().count("TELEGRAM_ENABLED=") == 1
    assert env.stat().st_mode & 0o777 == 0o600
