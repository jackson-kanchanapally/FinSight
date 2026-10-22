"""Offline coverage for the portfolio-only inbound Telegram flow."""
import asyncio
import json
import time
from datetime import date, timedelta
from decimal import Decimal
from html.parser import HTMLParser

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.ai.analyst.validator import InvalidAnalysis
from app.ai.providers.base import LLMError
from app.ai.providers.compatible import FreeCompatibleProvider
from app.ai.quick_analysis import format_quick_analysis, validate_quick_output
from app.config import settings
from app.database import Base
from app.models.company import Company
from app.models.event import CompanyEvent, DataOrigin, EventType
from app.models.research import ResearchJob
from app.models.telegram import TelegramInbox, TelegramQuery
from app.models.transaction import Transaction, TransactionType
from app.services import telegram_queries as queries
from app.services import telegram_research as research
from app.services.telegram_alerts import now
from app.services.telegram_client import TelegramError
from app.services.telegram_formatter import units
from tests.test_market_valuation import market_db


def incoming(content="ETERNAL", update_id=1, user_id=123, chat_id=123, chat_type="private"):
    return {"update_id": update_id, "message": {"text": content,
        "from": {"id": user_id}, "chat": {"id": chat_id, "type": chat_type}}}


def add_company(db, symbol="ETERNAL", name="Eternal Limited", qty=5):
    company = Company(symbol=symbol, name=name, exchange="NSE", isin="INE123456789" if symbol == "ETERNAL" else None)
    db.add(company)
    db.flush()
    if qty:
        db.add(Transaction(company_id=company.id, transaction_type=TransactionType.BUY,
            quantity=Decimal(qty), price=Decimal("100"), charges=Decimal("0"), transaction_date=date(2026, 1, 1)))
    db.commit()
    return company


def add_event(db, company_id=1, *, title="The company announced a dividend.", key="one", origin=DataOrigin.LIVE, age=0, kind=EventType.DIVIDEND):
    event = CompanyEvent(company_id=company_id, title=title, event_type=kind, published_at=now()-timedelta(days=age),
        source="NSE", data_origin=origin, source_url=f"https://nsearchives.nseindia.com/{key}.pdf", fingerprint=key)
    db.add(event)
    db.commit()
    return event


def result(context):
    key = next(key for key, item in context.registry.items() if item["kind"] == "EVENT")
    return {"outlook": None, "whats_new": [{"text": context.registry[key]["text"], "evidence_ids": [key],
        "basis": "VERIFIED_FACT", "source_quote": context.registry[key]["text"]}],
        "financial_performance": None, "order_book": None, "valuation": None, "risks": [],
        "bottom_line": {"text": "Monitor subsequent official disclosures for details and changes to the business outlook.",
            "evidence_ids": [key], "basis": "INTERPRETATION", "source_quote": None}}


class Provider:
    name, model = "mock", "free-test"

    def __init__(self, response=None, error=None, delay=0):
        self.calls, self.response, self.error, self.delay = 0, response, error, delay

    def generate(self, system, user, schema):
        self.calls += 1
        assert "untrusted" in system
        assert "evidence" in json.loads(user)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.response


@pytest.fixture
def query_env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'queries.sqlite'}", connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    for key, value in {"telegram_enabled": True, "telegram_queries_enabled": True,
        "telegram_bot_token": SecretStr("123:SECRET"), "telegram_allowed_user_ids": [123],
        "telegram_allowed_chat_ids": [123], "telegram_company_aliases": {"ETERNAL": ["Zomato"]},
        "ai_provider": "ollama", "telegram_query_cooldown_seconds": 1,
        "company_memory_enabled": False}.items():
        monkeypatch.setattr(settings, key, value)
    with factory() as db:
        company = add_company(db)
        add_event(db, company.id)
        context = research.build_quick_context(db, company)
    provider = Provider(json.dumps(result(context)))
    monkeypatch.setattr(queries, "configured_provider", lambda **kwargs: provider)
    sent = []
    async def send(chat_id, message, **kwargs):
        sent.append((chat_id, message, kwargs))
        return len(sent)
    monkeypatch.setattr(queries, "send_message", send)
    yield factory, provider, sent
    engine.dispose()


def accept(factory, update=None):
    with factory() as db:
        job = queries.accept_update(db, update or incoming())
        db.commit()
        return job.id if job else None


def expire_cooldown(factory):
    with factory() as db:
        for job in db.scalars(select(TelegramQuery)):
            job.created_at = now()-timedelta(minutes=1)
        db.commit()


@pytest.mark.parametrize("query", ["eternal", " ETERNAL ", "Eternal", "Eternal Ltd.", "ｅｔｅｒｎａｌ", "Zomato", "INE123456789"])
def test_active_portfolio_identifiers_and_maintained_aliases(query_env, query):
    factory, _, _ = query_env
    with factory() as db:
        assert [c.symbol for c in research.resolve_holding(db, query)] == ["ETERNAL"]


def test_names_punctuation_alias_ambiguity_and_sold_positions(query_env, monkeypatch):
    factory, _, _ = query_env
    with factory() as db:
        bank = add_company(db, "ICICIBANK", "ICICI Bank Limited")
        assert research.resolve_holding(db, "ICICI Bank")[0].id == bank.id
        add_company(db, "OTHER", "Eternal Ltd")
        assert len(research.resolve_holding(db, "Eternal")) == 2
        add_company(db, "UPLOADED", "Uploaded Portfolio Entry", qty=0)
        assert research.resolve_holding(db, "UPLOADED")[0].symbol == "UPLOADED"
        db.add(Transaction(company_id=bank.id, transaction_type=TransactionType.SELL, quantity=5,
            price=100, charges=0, transaction_date=date(2026, 2, 1)))
        db.commit()
        assert research.resolve_holding(db, "ICICIBANK") == []
    job_id = accept(factory)
    with factory() as db:
        assert "clarify" in db.get(TelegramQuery, job_id).message


def test_uploaded_entry_without_trade_history_is_available_through_delivery(query_env):
    factory, provider, sent = query_env
    with factory() as db:
        company = add_company(db, "ESDS", "ESDS Software Solution Limited", qty=0)
        add_event(db, company.id, key="uploaded-entry")
        provider.response = json.dumps(result(research.build_quick_context(db, company)))
    job_id = accept(factory, incoming("ESDS"))
    assert asyncio.run(queries.answer_one(factory))
    with factory() as db:
        job = db.get(TelegramQuery, job_id)
        assert job.company_id == company.id and job.status == "SENT"
    assert provider.calls == 1 and "ESDS Software Solution Limited" in sent[-1][1]


@pytest.mark.parametrize("update", [incoming(user_id=999), incoming(chat_id=999), incoming(chat_type="group"), incoming(chat_type="supergroup")])
def test_unauthorized_sender_cannot_query_or_persist_portfolio_data(query_env, monkeypatch, update):
    factory, provider, sent = query_env
    def forbidden(*args, **kwargs):
        pytest.fail("Unauthorized sender accessed portfolio")
    monkeypatch.setattr(queries, "resolve_holding", forbidden)
    assert accept(factory, update) is None
    assert not asyncio.run(queries.answer_one(factory))
    assert not provider.calls and not sent
    with factory() as db:
        assert list(db.scalars(select(TelegramQuery))) == []


@pytest.mark.parametrize("content,expected", [("TCS", queries.NOT_IN_PORTFOLIO), ("", "Send a company"), (None, "Send a company"), ("/unknown", "Unknown command"), ("/help", "Send a company")])
def test_non_portfolio_and_message_errors_never_call_model(query_env, content, expected):
    factory, provider, sent = query_env
    accept(factory, incoming(content))
    assert asyncio.run(queries.answer_one(factory))
    assert expected in sent[-1][1]
    assert provider.calls == 0


def test_query_end_to_end_sources_status_cache_and_duplicate_updates(query_env):
    factory, provider, sent = query_env
    first = accept(factory)
    assert accept(factory) == first
    assert asyncio.run(queries.answer_one(factory))
    assert len(sent) == 2 and provider.calls == 1
    assert "Quick Analysis" in sent[-1][1] and "Latest data:" in sent[-1][1] and "Source 1</a>" in sent[-1][1]
    with factory() as db:
        job = db.get(TelegramQuery, first)
        timestamp = job.analyzed_at
        assert job.status == "SENT" and job.message_id == 2
        assert job.source_references[0]["url"].startswith("https://nsearchives")
        assert job.evidence_registry and job.structured_output and job.context_hash
    expire_cooldown(factory)
    second = accept(factory, incoming(update_id=2))
    assert asyncio.run(queries.answer_one(factory))
    assert provider.calls == 1
    with factory() as db:
        assert db.get(TelegramQuery, second).analyzed_at == timestamp
        add_event(db, title="The company announced an acquisition.", key="two", kind=EventType.ACQUISITION)
    expire_cooldown(factory)
    accept(factory, incoming(update_id=3))
    assert asyncio.run(queries.answer_one(factory))
    assert provider.calls == 2


def test_duplicate_concurrent_requests_coalesce(query_env):
    factory, provider, _ = query_env
    accept(factory)
    expire_cooldown(factory)
    duplicate = accept(factory, incoming(update_id=2))
    with factory() as db:
        assert "already in progress" in db.get(TelegramQuery, duplicate).message
        assert db.get(TelegramQuery, duplicate).company_id is None
    assert asyncio.run(queries.answer_one(factory))
    assert asyncio.run(queries.answer_one(factory))
    assert provider.calls == 1


def test_authorization_revocation_and_sale_before_send(query_env, monkeypatch):
    factory, provider, sent = query_env
    first = accept(factory)
    monkeypatch.setattr(settings, "telegram_allowed_user_ids", [999])
    assert not asyncio.run(queries.answer_one(factory))
    assert not sent and not provider.calls
    with factory() as db:
        assert db.get(TelegramQuery, first).status == "REJECTED"


def test_holdings_rechecked_after_model_finishes(query_env, monkeypatch):
    factory, provider, sent = query_env
    generate = provider.generate
    def sell_then_generate(*args):
        with factory() as db:
            db.add(Transaction(company_id=1, transaction_type=TransactionType.SELL, quantity=5,
                price=100, charges=0, transaction_date=date(2026, 2, 1)))
            db.commit()
        return generate(*args)
    monkeypatch.setattr(provider, "generate", sell_then_generate)
    accept(factory)
    assert asyncio.run(queries.answer_one(factory))
    assert sent[-1][1] == queries.NOT_IN_PORTFOLIO


@pytest.mark.parametrize("failure", [LLMError("RATE_LIMIT"), LLMError("secret-token-from-provider"), TimeoutError(), RuntimeError("secret")])
def test_model_failure_never_falls_back_or_logs_provider_secrets(query_env, failure, caplog):
    factory, provider, sent = query_env
    provider.error = failure
    job_id = accept(factory)
    assert asyncio.run(queries.answer_one(factory))
    assert queries.UNAVAILABLE == sent[-1][1] and provider.calls == 1
    with factory() as db:
        job = db.get(TelegramQuery, job_id)
        assert job.error and "secret" not in job.error
        assert not job.structured_output
    assert "secret" not in caplog.text


def test_real_async_deadline_and_late_model_cannot_persist(query_env, monkeypatch):
    factory, provider, sent = query_env
    provider.delay = .05
    monkeypatch.setattr(settings, "telegram_query_timeout_seconds", .005)
    job_id = accept(factory)
    asyncio.run(queries.answer_one(factory))
    with factory() as db:
        job = db.get(TelegramQuery, job_id)
        assert job.error == "MODEL_TIMEOUT" and job.structured_output is None
    assert queries.UNAVAILABLE in sent[-1][1]


def test_missing_evidence_and_refresh_uses_existing_queue(query_env, monkeypatch):
    factory, provider, sent = query_env
    monkeypatch.setattr(settings, "research_automation_enabled", True)
    with factory() as db:
        for event in db.scalars(select(CompanyEvent)):
            db.delete(event)
        db.commit()
    job_id = accept(factory)
    asyncio.run(queries.answer_one(factory))
    with factory() as db:
        assert db.get(TelegramQuery, job_id).error == "NO_EVIDENCE"
        assert {job.kind for job in db.scalars(select(ResearchJob))} >= {"DISCLOSURES:NSE", "RESEARCH", "COMMENTARY", "MARKET"}
    assert provider.calls == 0 and "No usable source evidence" in sent[-1][1]


def test_refresh_respects_existing_backoff_and_source_rate_limits(query_env):
    factory, _, _ = query_env
    with factory() as db:
        company = db.get(Company, 1)
        research.queue_refresh(db, company)
        job = db.scalar(select(ResearchJob).where(ResearchJob.kind == "DISCLOSURES:NSE"))
        job.status, job.next_run_at = "RETRY", now()+timedelta(hours=1)
        expected = job.next_run_at
        db.commit()
        research.queue_refresh(db, company)
        assert job.status == "RETRY" and job.next_run_at == expected


def test_sources_staleness_deduplication_fixture_exclusion(query_env):
    factory, _, _ = query_env
    with factory() as db:
        add_event(db, title="The company announced a dividend.", key="duplicate")
        add_event(db, title="Fictional unsupported record", key="fixture", origin=DataOrigin.FIXTURE)
        context = research.build_quick_context(db, db.get(Company, 1))
        events = [item for item in context.registry.values() if item["kind"] == "EVENT"]
        assert len(events) == 1
        assert "Fictional" not in context.text
        for event in db.scalars(select(CompanyEvent)):
            event.published_at = now()-timedelta(days=60)
        db.commit()
        context = research.build_quick_context(db, db.get(Company, 1))
        assert any("stale" in gap for gap in context.gaps) and context.confidence == "LOW"


@pytest.mark.parametrize("mutation,reason", [
    (lambda data: data.update(unexpected=True), "INVALID_JSON_OR_SCHEMA"),
    (lambda data: data["bottom_line"].update(evidence_ids=["OTHER"]), "UNKNOWN_EVIDENCE_ID"),
    (lambda data: data["bottom_line"].update(text="The company has 999999 crore profit."), "UNSUPPORTED_NUMBER"),
    (lambda data: data["bottom_line"].update(text="Read https://invented.example/report"), "MODEL_URL_FORBIDDEN"),
    (lambda data: data["bottom_line"].update(text="Buy this stock now."), "TRADING_CALL_UNSUPPORTED"),
    (lambda data: data["whats_new"][0].update(source_quote="not in evidence"), "SOURCE_QUOTE_REQUIRED"),
    (lambda data: data.update(order_book=data["whats_new"][0]), "ORDER_BOOK_UNSUPPORTED"),
    (lambda data: data.update(valuation={**data["bottom_line"], "verdict": "Attractive"}), "VALUATION_EVIDENCE_MISSING"),
])
def test_structured_grounding_and_optional_section_validation(query_env, mutation, reason):
    factory, _, _ = query_env
    with factory() as db:
        context = research.build_quick_context(db, db.get(Company, 1))
        data = result(context)
        mutation(data)
        with pytest.raises(InvalidAnalysis) as exc:
            validate_quick_output(json.dumps(data), context)
        assert exc.value.reason == reason


def test_malformed_model_json_returns_clear_unavailability(query_env):
    factory, provider, sent = query_env
    provider.response = "```json\n{bad}\n```"
    job_id = accept(factory)
    asyncio.run(queries.answer_one(factory))
    assert sent[-1][1] == queries.UNAVAILABLE
    with factory() as db:
        assert db.get(TelegramQuery, job_id).error == "INVALID_MODEL_OUTPUT"


def test_disclosed_order_and_management_pipeline_are_distinct(query_env):
    factory, _, _ = query_env
    with factory() as db:
        event = db.scalar(select(CompanyEvent))
        event.title, event.event_type = "The company won a signed order worth 125 crore.", EventType.ORDER_WIN
        db.commit()
        context = research.build_quick_context(db, db.get(Company, 1))
        data = result(context)
        data["order_book"] = data["whats_new"][0].copy()
        validate_quick_output(json.dumps(data), context)
        event.title = "Management estimates a pipeline of 125 crore."
        db.commit()
        context = research.build_quick_context(db, db.get(Company, 1))
        data = result(context)
        data["order_book"] = data["whats_new"][0].copy()
        with pytest.raises(InvalidAnalysis, match="ORDER_BOOK_AUTHORITY_MISMATCH"):
            validate_quick_output(json.dumps(data), context)
        data["whats_new"] = []
        data["order_book"]["basis"] = "MANAGEMENT_CLAIM"
        validate_quick_output(json.dumps(data), context)
        data["order_book"]["text"] = "Management disclosed an order book of 125 crore."
        with pytest.raises(InvalidAnalysis, match="DISCLOSED_BACKLOG_REQUIRED"):
            validate_quick_output(json.dumps(data), context)


def test_formatter_escapes_html_preserves_ist_sources_and_telegram_limit(query_env):
    factory, _, _ = query_env
    with factory() as db:
        context = research.build_quick_context(db, db.get(Company, 1))
    output = validate_quick_output(json.dumps(result(context)), context)
    context.company_name = "<script> & Company " + "😀"*300
    output.bottom_line.text = "& <watch> "*40
    message = format_quick_analysis(output, context, now())
    assert units(message) <= 4000
    assert "<script>" not in message and "&lt;script&gt;" in message
    assert "IST" in message and "Source 1</a>" in message
    assert "<b>Order book:" not in message and "<b>Valuation:" not in message
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
        def handle_starttag(self, tag, attrs):
            self.stack.append(tag)
        def handle_endtag(self, tag):
            assert self.stack.pop() == tag
    parser = Parser()
    parser.feed(message)
    assert not parser.stack


def test_polling_cursor_atomic_idempotent_private_only(query_env, monkeypatch):
    factory, _, _ = query_env
    calls = []
    async def call(method, payload):
        calls.append(payload)
        return [incoming(user_id=999, update_id=40), incoming(update_id=41)] if payload["offset"] == 0 else []
    monkeypatch.setattr(queries, "call_bot", call)
    assert asyncio.run(queries.poll_updates(factory))
    assert asyncio.run(queries.poll_updates(factory))
    assert calls[1]["offset"] == 42
    with factory() as db:
        assert db.get(TelegramInbox, 1).lease_token is None
        assert [job.update_id for job in db.scalars(select(TelegramQuery))] == [41]


def test_poll_failure_retains_cursor_and_releases_lease(query_env, monkeypatch):
    factory, _, _ = query_env
    async def call(*args, **kwargs):
        raise TelegramError("Telegram unavailable")
    monkeypatch.setattr(queries, "call_bot", call)
    with pytest.raises(TelegramError):
        asyncio.run(queries.poll_updates(factory))
    with factory() as db:
        inbox = db.get(TelegramInbox, 1)
        assert inbox.next_update_id == 0 and inbox.lease_token is None


def test_uncertain_sends_never_replayed_and_definitive_429_retries(query_env, monkeypatch):
    factory, provider, sent = query_env
    job_id = accept(factory, incoming("/help"))
    async def uncertain(*args, **kwargs):
        raise TelegramError("unknown", uncertain=True)
    monkeypatch.setattr(queries, "send_message", uncertain)
    assert not asyncio.run(queries.answer_one(factory))
    with factory() as db:
        assert db.get(TelegramQuery, job_id).status == "UNCERTAIN"
    assert not asyncio.run(queries.answer_one(factory))
    job_id = accept(factory, incoming("/help", update_id=2))
    async def limited(*args, **kwargs):
        raise TelegramError("rate limited", retry_after=90)
    monkeypatch.setattr(queries, "send_message", limited)
    assert not asyncio.run(queries.answer_one(factory))
    with factory() as db:
        job = db.get(TelegramQuery, job_id)
        assert job.status == "PENDING" and job.next_attempt_at > (now()+timedelta(seconds=85)).replace(tzinfo=None)
    assert not provider.calls


@pytest.mark.parametrize("model,allowed", [("paid-model", ["free-model"]), ("auto", ["auto"]), ("", [])])
def test_free_route_rejects_unknown_paid_and_automatic_models(monkeypatch, model, allowed):
    monkeypatch.setattr(settings, "ai_model", model)
    monkeypatch.setattr(settings, "ai_free_models", allowed)
    monkeypatch.setattr(settings, "ai_api_base_url", "http://localhost:20128/v1")
    with pytest.raises(LLMError, match="FREE_MODEL_NOT_ALLOWED"):
        FreeCompatibleProvider(timeout=1)


@pytest.mark.parametrize("status", [200, 429, 500])
def test_free_gateway_never_falls_back_and_sanitizes_failures(monkeypatch, status):
    monkeypatch.setattr(settings, "ai_model", "free-model")
    monkeypatch.setattr(settings, "ai_free_models", ["free-model"])
    monkeypatch.setattr(settings, "ai_api_base_url", "http://localhost:20128/v1")
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        assert kwargs["json"]["model"] == "free-model"
        assert kwargs["json"]["provider"]["allow_fallbacks"] is False
        assert kwargs["json"]["provider"]["max_price"] == {"prompt": 0, "completion": 0}
        return httpx.Response(status, json={"model": "free-model", "choices": [{"message": {"content": "{}"}}], "secret": "never log this"})
    monkeypatch.setattr("app.ai.providers.compatible.httpx.post", post)
    provider = FreeCompatibleProvider(1)
    if status == 200:
        assert provider.generate("system", "user", {}) == "{}"
    else:
        with pytest.raises(LLMError) as exc:
            provider.generate("system", "user", {})
        assert "secret" not in str(exc.value)
    assert len(calls) == 1


def test_recurring_titles_and_distinct_query_identified_filings_are_retained(query_env):
    factory, _, _ = query_env
    with factory() as db:
        add_event(db, title="The company announced a dividend.", key="another-date", age=30)
        first = add_event(db, title="Management change", key="first-query")
        second = add_event(db, title="Credit rating revision", key="second-query")
        first.source_url = "https://exchange.example/filing?id=1&utm_source=feed"
        second.source_url = "https://exchange.example/filing?id=2&utm_source=feed"
        db.commit()
        context = research.build_quick_context(db, db.get(Company, 1))
        assert len([item for item in context.registry.values() if item["kind"] == "EVENT"]) == 4


def test_factual_documentary_claim_cannot_replace_news_with_an_invention(query_env):
    factory, _, _ = query_env
    with factory() as db:
        context = research.build_quick_context(db, db.get(Company, 1))
    data = result(context)
    data["whats_new"][0]["text"] = "The company acquired a competitor."
    with pytest.raises(InvalidAnalysis, match="FACT_TEXT_NOT_IN_SOURCE"):
        validate_quick_output(json.dumps(data), context)


def test_real_financial_builder_uses_validated_metrics_and_discloses_conflicts(market_db, monkeypatch):
    from tests.test_intelligent_earnings import results, official_candidate
    from app.services.result_sources import compare_authoritative_candidate
    from app.ai.analyst import context_builder
    company, _, period = results(market_db)
    def forbidden(*args, **kwargs):
        pytest.fail("Quick research must not perform embedding/model requests while collecting evidence")
    monkeypatch.setattr(context_builder, "historical_context", forbidden)
    context = research.build_quick_context(market_db, company)
    revenue = next(key for key, item in context.registry.items() if item["kind"] == "FACT"
        and item["metric_code"] == "REVENUE_FROM_OPERATIONS" and item["period_id"] == period.id)
    assert Decimal(context.registry[revenue]["value"]) == 120
    assert any(item.get("metric_code") == "REVENUE_QOQ" for item in context.registry.values())
    data = {"outlook": None, "whats_new": [], "order_book": None, "valuation": None, "risks": [],
        "financial_performance": {"text": "Revenue was 120 crore in the reporting period.",
            "basis": "VERIFIED_FACT", "source_quote": None, "evidence_ids": [revenue]},
        "bottom_line": {"text": "Monitor the next comparable results for revenue and profitability trends.",
            "basis": "INTERPRETATION", "source_quote": None, "evidence_ids": [revenue]}}
    validate_quick_output(json.dumps(data), context)
    data["financial_performance"]["text"] = "Revenue was 999999 crore."
    with pytest.raises(InvalidAnalysis, match="UNSUPPORTED_NUMBER"):
        validate_quick_output(json.dumps(data), context)
    event, document, raw = official_candidate(market_db, company, period, pat="999")
    compare_authoritative_candidate(market_db, period, event, document, raw)
    context = research.build_quick_context(market_db, company)
    assert any("CONFLICTING" in gap for gap in context.gaps)
    assert context.confidence == "LOW"
    assert "MANUAL" in context.text


@pytest.mark.parametrize("verdict,label", [("UNDERVALUED", "Attractive"), ("FAIRLY_VALUED", "Fair"), ("OVERVALUED", "Expensive")])
def test_valuation_requires_scenario_and_computed_multiple(query_env, verdict, label):
    factory, _, _ = query_env
    with factory() as db:
        context = research.build_quick_context(db, db.get(Company, 1))
    context.registry["V1"] = {"kind": "VALUATION", "metric_code": "PE_TTM", "value": "12", "as_of_date": now().date().isoformat()}
    context.registry["SC1"] = {"kind": "SCENARIO", "valuation_verdict": verdict, "value": "15", "assumptions": {"years": "3"}}
    data = result(context)
    data["valuation"] = {"verdict": label, "text": "Under the supplied scenario assumptions, the trailing P/E is 12.",
        "evidence_ids": ["V1", "SC1"], "basis": "INTERPRETATION", "source_quote": None}
    validate_quick_output(json.dumps(data), context)
    data["valuation"]["evidence_ids"] = ["V1"]
    with pytest.raises(InvalidAnalysis, match="VALUATION_EVIDENCE_MISSING"):
        validate_quick_output(json.dumps(data), context)


def test_provider_route_change_and_ttl_invalidate_cache(query_env, monkeypatch):
    factory, provider, _ = query_env
    accept(factory)
    asyncio.run(queries.answer_one(factory))
    expire_cooldown(factory)
    monkeypatch.setattr(settings, "ollama_model", "a-different-free-local-model")
    accept(factory, incoming(update_id=2))
    asyncio.run(queries.answer_one(factory))
    assert provider.calls == 2
    expire_cooldown(factory)
    monkeypatch.setattr(settings, "telegram_query_cache_seconds", 0)
    accept(factory, incoming(update_id=3))
    asyncio.run(queries.answer_one(factory))
    assert provider.calls == 3


def test_revoked_free_eligibility_cannot_reuse_old_cached_analysis(query_env, monkeypatch):
    factory, provider, sent = query_env
    accept(factory)
    asyncio.run(queries.answer_one(factory))
    expire_cooldown(factory)
    def unavailable(**kwargs):
        raise LLMError("FREE_MODEL_NOT_ALLOWED")
    monkeypatch.setattr(queries, "configured_provider", unavailable)
    accept(factory, incoming(update_id=2))
    asyncio.run(queries.answer_one(factory))
    assert provider.calls == 1 and sent[-1][1] == queries.UNAVAILABLE


def test_live_worker_lease_and_interrupted_send_recovery(query_env):
    factory, _, sent = query_env
    first = accept(factory, incoming("/help"))
    with factory() as db:
        job = db.get(TelegramQuery, first)
        job.status, job.lease_token, job.lease_until = "PROCESSING", "another-worker", now()+timedelta(minutes=1)
        db.commit()
    assert not asyncio.run(queries.answer_one(factory))
    with factory() as db:
        job = db.get(TelegramQuery, first)
        job.status, job.lease_until = "SENDING", now()-timedelta(seconds=1)
        db.commit()
    assert not asyncio.run(queries.answer_one(factory))
    with factory() as db:
        assert db.get(TelegramQuery, first).status == "UNCERTAIN"
    assert not sent


def test_query_feature_fail_closed_and_status(client, monkeypatch):
    monkeypatch.setattr(settings, "telegram_queries_enabled", True)
    monkeypatch.setattr(settings, "telegram_allowed_user_ids", [])
    assert not queries.enabled()
    status = client.get("/api/telegram/status").json()
    assert status["queries"] == {"enabled": True, "configured": False, "counts": {}}


def test_setup_queries_uses_selected_private_sender_without_sending(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[1] / "scripts/setup_telegram.py"
    spec = importlib.util.spec_from_file_location("setup_query_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "getpass", lambda *args: "123:TEST_SECRET")
    answers = iter(["", "1"])
    monkeypatch.setattr("builtins.input", lambda *args: next(answers))
    async def call(method, *args, **kwargs):
        return {"is_bot": True, "username": "almonds_test"} if method == "getMe" else [incoming("/start")]
    monkeypatch.setattr(module, "call_bot", call)
    async def no_send(*args, **kwargs):
        pytest.fail("Setup without --test must not send")
    monkeypatch.setattr(module, "send_message", no_send)
    # Settings object is shared, so ensure the fixture restores this secret too.
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr(""))
    (tmp_path/".env").write_text("DATABASE_NAME=preserved\n")
    asyncio.run(module.setup(enable_queries=True))
    env = (tmp_path/".env").read_text()
    assert "TELEGRAM_ALLOWED_USER_IDS=[123]" in env
    assert "TELEGRAM_ALLOWED_CHAT_IDS=[123]" in env
    assert "TELEGRAM_QUERIES_ENABLED=true" in env and "DATABASE_NAME=preserved" in env


def test_additive_query_migration_preserves_event_receipts_and_downgrades(tmp_path):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from app.models.telegram import TelegramDelivery
    engine = create_engine(f"sqlite:///{tmp_path/'migration.sqlite'}")
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in {"telegram_inbox", "telegram_queries"}:
                table.create(connection)
        connection.exec_driver_sql("INSERT INTO telegram_deliveries (event_id, chat_id, status, attempts, next_attempt_at, message_id) VALUES (1,'123','SENT',1,'2026-10-22',99)")
        path = Path(__file__).parents[1]/"alembic/versions/0025_telegram_queries.py"
        spec = importlib.util.spec_from_file_location("query_migration", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            columns = {row["name"] for row in inspect(connection).get_columns("telegram_queries")}
            assert columns == {column.name for column in TelegramQuery.__table__.columns}
            indexes = {row["name"] for row in inspect(connection).get_indexes("telegram_queries")}
            assert indexes >= {"ix_telegram_queries_company_id", "ix_telegram_queries_status"}
            assert tuple(connection.exec_driver_sql("SELECT status, message_id FROM telegram_deliveries").one()) == ("SENT", 99)
            module.downgrade()
            assert "telegram_queries" not in inspect(connection).get_table_names()
            assert connection.exec_driver_sql("SELECT message_id FROM telegram_deliveries").scalar_one() == 99
    engine.dispose()
