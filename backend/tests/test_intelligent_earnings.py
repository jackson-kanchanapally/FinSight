"""Offline contracts for the existing results-to-Telegram pipeline."""
import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.analysis import AIAnalysis
from app.models.event import CompanyEvent, DataOrigin, Document, DocumentPage, EventReference, EventType, ProcessingStatus, ReportingBasis
from app.models.financial import FactMethod, FactStatus, FinancialFact, FinancialScope, FinancialUnit, MetricCode, OriginalUnit
from app.models.result_evidence import ResultDiscovery, ResultVerification
from app.models.telegram import TelegramDelivery
from app.services.earnings_notification import build_notification
from app.services.financials.extractor import RawFact, RawPeriod
from app.services.financials.period import comparable_period, identify_period
from app.services.financials.service import calculate_company_metrics
from app.services.investor_evidence import extend_results_evidence
from app.services.result_sources import assess_stored_period, compare_authoritative_candidate, freshness_view, record_discovery, source_check, verification_view
from app.services.report_links import authorized, signed_link
from app.services.telegram_formatter import format_earnings, units
from app.services import telegram_alerts as alerts
from app.services.telegram_client import TelegramError, call_bot
from tests.test_market_valuation import market_db, company, add_quarter
from tests.test_telegram import telegram_env


@pytest.fixture(autouse=True)
def offline_earnings(monkeypatch):
    monkeypatch.setattr(settings, "company_memory_enabled", False)
    monkeypatch.setattr(settings, "report_public_base_url", "")
    monkeypatch.setattr(settings, "report_access_secret", SecretStr(""))


def set_facts(db, period, values):
    existing = {f.metric_code: f for f in period.facts}
    for code, value in values.items():
        code = MetricCode(code)
        row = existing.get(code)
        if row is None:
            row = FinancialFact(financial_period_id=period.id, metric_code=code,
                unit=FinancialUnit.INR_CRORE, source_document_id=period.source_document_id,
                source_page=1, source_label=code.value, source_column_date=period.period_end,
                extraction_method=FactMethod.PDF_TABLE, status=FactStatus.VERIFIED_PDF)
            db.add(row)
        row.value = Decimal(value)
        row.source_page = 1
    db.commit()
    db.refresh(period, ["facts"])


def results(db, symbol="ETERNAL"):
    issuer = company(db, symbol=symbol)
    previous = add_quarter(db, issuer, date(2026, 3, 31), date(2026, 4, 20))
    current = add_quarter(db, issuer, date(2026, 6, 30), date(2026, 7, 20))
    set_facts(db, previous, {"REVENUE_FROM_OPERATIONS": "100", "OTHER_INCOME": "2", "TOTAL_INCOME": "102",
        "TOTAL_EXPENSES": "89", "PBT": "13", "TOTAL_TAX_EXPENSE": "3", "PAT": "10"})
    set_facts(db, current, {"REVENUE_FROM_OPERATIONS": "120", "OTHER_INCOME": "2", "TOTAL_INCOME": "122",
        "TOTAL_EXPENSES": "100", "PBT": "22", "TOTAL_TAX_EXPENSE": "15", "PAT": "7"})
    calculate_company_metrics(db, issuer.id)
    return issuer, previous, current


def official_candidate(db, issuer, period, *, pat=None, identifier=True):
    event = CompanyEvent(company_id=issuer.id, title="Official results", event_type=EventType.FINANCIAL_RESULTS,
        published_at=datetime(2026, 7, 21, tzinfo=timezone.utc), source="NSE", data_origin=DataOrigin.LIVE,
        source_url="https://nsearchives.nseindia.com/results.pdf", fingerprint=f"official-{period.id}-{pat}-{identifier}")
    db.add(event)
    db.flush()
    document = Document(company_id=issuer.id, event_id=event.id, source="NSE", source_url=event.source_url,
        document_url=event.source_url, processing_status=ProcessingStatus.PROCESSED)
    db.add(document)
    db.flush()
    db.add(EventReference(event_id=event.id, source="NSE", source_url=event.source_url, document_url=event.source_url,
                          data_origin=DataOrigin.LIVE, raw_payload={"issuer_symbol": issuer.symbol}))
    identity = " CIN L12345MH2020PLC123456" if identifier else ""
    for doc_id in [period.source_document_id, document.id]:
        db.add(DocumentPage(document_id=doc_id, page_number=1, text=issuer.name+" financial results"+identity))
    db.commit()
    raw = RawPeriod(identify_period(period.period_end, period.period_type), period.scope)
    for f in period.facts:
        raw.facts[f.metric_code] = RawFact(f.metric_code, Decimal(pat) if pat is not None and f.metric_code == MetricCode.PAT else f.value,
            OriginalUnit.INR_PER_SHARE if f.unit == FinancialUnit.INR_PER_SHARE else OriginalUnit.INR_CRORE,
            1, f.source_label, period.period_end)
    return event, document, raw


@pytest.mark.parametrize("symbol", ["ETERNAL", "ESDS"])
def test_quantified_profit_bridge_tax_and_source_lineage(market_db, symbol):
    issuer, previous, current = results(market_db, symbol)
    registry = {}
    quality = extend_results_evidence(market_db, current, registry)
    assert quality["reconciliation"]["status"] == "RECONCILED"
    assert quality["observations"]["effective_tax_rate"]["value"].startswith("68.1818")
    assert Decimal(quality["observations"]["tax_change"]["value"]) == 12
    assert sum(Decimal(row["contribution_crore"]) for row in quality["pat_bridge"]) == -3
    assert "higher tax" in quality["text"]
    for row in quality["pat_bridge"]:
        metric = registry[row["evidence_ids"][0]]
        assert metric["comparison_period_id"] == previous.id
        assert all(registry[key]["document_id"] and registry[key]["page"] for key in metric["input_evidence_ids"])
    notification = build_notification(market_db, current)
    assert notification.confidence["level"] == "LOW"
    assert notification.confidence["statistically_calibrated"] is False
    assert notification.valuation.text.startswith("Unassessed")
    assert notification.full_report.url is None
    for row in notification.snapshot+notification.additional_metrics:
        assert all(key in notification.evidence_registry for key in row.evidence_ids)


def test_unreconciled_tax_and_missing_cash_do_not_become_explanations(market_db):
    _, _, current = results(market_db)
    set_facts(market_db, current, {"PAT": "50"})
    for fact in current.facts:
        if fact.metric_code in {MetricCode.OPERATING_CASH_FLOW, MetricCode.TOTAL_DEBT, MetricCode.CASH_AND_CASH_EQUIVALENTS, MetricCode.CAPITAL_EXPENDITURE}:
            fact.status = FactStatus.NEEDS_REVIEW
    market_db.commit()
    registry = {}
    quality = extend_results_evidence(market_db, current, registry)
    assert quality["reconciliation"]["status"] == "CONFLICTING"
    assert not quality["pat_bridge"]
    assert not any("PAT_BRIDGE" in row.get("metric_code", "") for row in registry.values())
    assert "cash_conversion" in quality["unassessed"]
    notification = build_notification(market_db, current)
    assert notification.contradictions and notification.confidence["level"] == "LOW"


def test_large_scenario_history_preserves_bounded_earnings_context(market_db, monkeypatch):
    from app.ai.analyst import context_builder as builder
    import json
    issuer, _, current = results(market_db, "ESDS")
    original = builder.build_investor_report

    def expanded_report(*args, **kwargs):
        report = original(*args, **kwargs)
        report["tested_pe"]["scenarios"] = [
            {"name": f"Historical assumption {i}", "source": "Explicit scenario assumption",
             "source_type": "MANUAL", "eps_growth_pct": "15", "terminal_pe": "20",
             "years": 5, "required_return_pct": "12", "evidence_ids": []}
            for i in range(100)
        ]
        return report

    monkeypatch.setattr(builder, "build_investor_report", expanded_report)
    context = builder.build_context(market_db, issuer.name, issuer.symbol, current, max_chars=12000)
    payload = json.loads(context.text)
    assert len(context.text) <= 12000
    assert payload["verified_facts"] and payload["deterministic_metrics"]
    assert payload["source_verification"]["status"] == "UNVERIFIED"
    assert payload["evidence_confidence"]["level"] == "LOW"
    assert payload["result_freshness"] and payload["previous_thesis"]
    assert "omitted" in payload["tested_pe_range"]["detail_note"]
    assert len(context.investor_report["tested_pe"]["scenarios"]) == 100
    assert context.registry


def test_negative_and_zero_denominators_and_window_mismatch(market_db):
    issuer, previous, current = results(market_db)
    set_facts(market_db, previous, {"PAT": "-10", "PBT": "0"})
    set_facts(market_db, current, {"PAT": "-5", "PBT": "-3"})
    registry = {}
    quality = extend_results_evidence(market_db, current, registry)
    pat_growth = next(r for r in registry.values() if r.get("metric_code") == "PAT_QOQ")
    assert Decimal(pat_growth["value"]) == 50
    assert "effective_tax_rate" in quality["unassessed"]
    assert "cash_conversion" in quality["unassessed"]
    previous.period_start = date(2026, 2, 1)
    market_db.commit()
    assert not comparable_period(current, previous, "QOQ")
    registry = {}
    extend_results_evidence(market_db, current, registry)
    assert not any(r.get("metric_code") == "PAT_QOQ" for r in registry.values())


def test_fact_column_date_and_rounding_are_checked(market_db):
    _, _, current = results(market_db)
    set_facts(market_db, current, {"PAT": "7.02"})
    quality = extend_results_evidence(market_db, current, {})
    assert quality["reconciliation"]["status"] == "RECONCILED"
    set_facts(market_db, current, {"PAT": "7.04"})
    quality = extend_results_evidence(market_db, current, {})
    assert quality["reconciliation"]["status"] == "CONFLICTING"
    for fact in current.facts:
        if fact.metric_code == MetricCode.PAT:
            fact.source_column_date = date(2025, 6, 30)
    market_db.commit()
    quality = extend_results_evidence(market_db, current, {})
    assert quality["reconciliation"]["status"] == "UNASSESSED"


def test_manual_pdf_verified_only_with_independent_identity_and_figures(market_db):
    issuer, _, current = results(market_db)
    event, doc, raw = official_candidate(market_db, issuer, current)
    first = compare_authoritative_candidate(market_db, current, event, doc, raw)
    second = compare_authoritative_candidate(market_db, current, event, doc, raw)
    assert first.status == "VERIFIED" and first.id == second.id
    assert first.details["identifier_match"] and first.details["figures"]
    assert not first.details["hash_is_authenticity_proof"]
    market_db.commit()
    assert verification_view(market_db, current)["status"] == "VERIFIED"


def test_matching_hash_or_figures_without_identifiers_is_partial(market_db):
    issuer, _, current = results(market_db)
    event, doc, raw = official_candidate(market_db, issuer, current, identifier=False)
    doc.sha256_hash = market_db.get(Document, current.source_document_id).sha256_hash = "a"*64
    record = compare_authoritative_candidate(market_db, current, event, doc, raw)
    assert record.status == "PARTIALLY_VERIFIED"


def test_conflicting_official_figures_are_retained_without_overwrite(market_db):
    issuer, _, current = results(market_db)
    event, doc, raw = official_candidate(market_db, issuer, current, pat="999")
    record = compare_authoritative_candidate(market_db, current, event, doc, raw)
    assert record.status == "CONFLICTING" and "PAT" in record.details["conflicts"]
    assert next(f.value for f in current.facts if f.metric_code == MetricCode.PAT) == 7
    raw.scope = FinancialScope.STANDALONE
    assert compare_authoritative_candidate(market_db, current, event, doc, raw).status == "CONFLICTING"


def test_manual_source_url_alone_never_verifies_authenticity(market_db):
    _, _, current = results(market_db)
    event = market_db.get(CompanyEvent, current.source_event_id)
    event.source_url = "https://nsearchives.nseindia.com/looks-official.pdf"
    record = assess_stored_period(market_db, current)
    assert record.status == "UNVERIFIED"


def discovery(issuer, *, end=date(2026, 9, 30), origin=DataOrigin.LIVE):
    return SimpleNamespace(company_id=issuer.id, external_id="q2", source="NSE", source_url="https://nsearchives.nseindia.com/q2.pdf",
        document_url="https://nsearchives.nseindia.com/q2.pdf", title="Financial Results Q2 FY2027",
        description=None, data_origin=origin, event_type=EventType.FINANCIAL_RESULTS,
        period_end_date=end, reporting_basis=ReportingBasis.CONSOLIDATED, raw_payload={"issuer_symbol": issuer.symbol},
        published_at=datetime(2026, 10, 21, tzinfo=timezone.utc))


def test_detected_newer_results_and_unknown_periods_flag_staleness(market_db):
    issuer, _, current = results(market_db)
    row = record_discovery(market_db, discovery(issuer))
    assert row.event_id is None and row.status == "DETECTED"
    check = SimpleNamespace(company_id=issuer.id, symbol=issuer.symbol)
    source_check(market_db, [check], "NSE", "SUCCESS")
    view = freshness_view(market_db, current)
    assert view["status"] == "POTENTIALLY_STALE" and view["newer_awaiting_ingestion"]
    assert view["latest_stored_period_end"] == "2026-06-30"
    assert view["latest_detected"]["period_end"] == "2026-09-30"
    assert view["source_checks"][0]["last_success_at"]
    assert record_discovery(market_db, discovery(issuer)).id == row.id
    assert record_discovery(market_db, discovery(issuer, origin=DataOrigin.MANUAL)) is None
    row.period_end = None
    market_db.commit()
    assert freshness_view(market_db, current)["status"] == "POTENTIALLY_STALE"


def test_source_failure_retains_last_success_and_reports_limited_coverage(market_db):
    issuer, _, current = results(market_db)
    check = SimpleNamespace(company_id=issuer.id, symbol=issuer.symbol)
    source_check(market_db, [check], "NSE", "SUCCESS")
    source_check(market_db, [check], "NSE", "FAILED", error="SourceUnavailableError")
    status = freshness_view(market_db, current)["source_checks"][0]
    assert status["status"] == "FAILED" and status["last_success_at"]
    assert "window" in status["coverage"]


def test_html_escaping_deterministic_splitting_and_optional_metrics(market_db):
    _, _, current = results(market_db)
    n = build_notification(market_db, current)
    n.company_symbol = "ABC <script>&🌰"
    n.verdict.text = "Profit < tax & uncertainty > forecasts. "*30
    from app.schemas.earnings_notification import EvidenceText
    n.management_highlights = [EvidenceText(text="Management targets growth with <care> & discipline. "*8,
        basis="MANAGEMENT_CLAIM") for _ in range(2)]
    parts = format_earnings(n, limit=1000)
    assert parts == format_earnings(n, limit=1000) and len(parts) > 1
    assert all(units(p) <= 1000 for p in parts)
    assert "<script>" not in "".join(parts) and "&lt;script&gt;" in parts[0]
    assert "127.0.0.1" not in "".join(parts) and "localhost" not in "".join(parts)
    for heading in ["RESULT HIGHLIGHTS", "AI THESIS", "MANAGEMENT SAYS"]:
        assert heading in "".join(parts)
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(); self.tags = []
        def handle_starttag(self, tag, attrs): self.tags.append(tag)
        def handle_endtag(self, tag): assert self.tags.pop() == tag
    for part in parts:
        parser = Parser(); parser.feed(part); assert not parser.tags
    n.snapshot = [row for row in n.snapshot if row.code != "EBITDA_PROXY"]
    assert "EBITDA proxy (includes" not in "".join(format_earnings(n))


@pytest.mark.parametrize("assessment,label", [("VERY_POSITIVE", "Positive"), ("NEGATIVE", "Negative"),
    ("NEUTRAL", "Neutral"), ("INSUFFICIENT_EVIDENCE", "Neutral")])
def test_clean_telegram_result_has_direction_and_no_internal_report_sections(market_db, assessment, label):
    _, _, current = results(market_db)
    n = build_notification(market_db, current)
    n.previous_thesis["current_assessment"] = assessment
    parts = format_earnings(n)
    text = "".join(parts)
    assert len(parts) == 1 and units(text) < 1500
    assert f"<b>{label}</b>" in text
    assert "Revenue: <b>₹120 cr</b>" in text and "Net profit: <b>₹7 cr</b>" in text
    assert "vs last quarter" in text
    assert "No management commentary available" in text
    assert "Source not independently verified" in text
    for junk in ["[F", "[M", "[J", "EBITDA proxy", "CONFIDENCE", "DATA QUALITY", "FULL REPORT",
        "VALUATION", "WATCH NEXT", "WHAT DROVE", "Unassessed", "VERIFIED FALLBACK"]:
        assert junk not in text
    # Full evidence survives presentation, for the unchanged dashboard/report.
    assert n.evidence_registry and n.drivers and n.confidence and n.unassessed


def test_management_highlights_use_current_period_attributed_sources(market_db):
    from tests.test_phase72 import call
    issuer, previous, current = results(market_db)
    call(market_db, issuer, previous,
         "CEO: We target 99 GPUs and will deploy those by Q1 FY27.", published="2026-04-25")
    call(market_db, issuer, current,
         "CEO: We target 200 GPUs and will deploy those by Q3 FY27.\n"
         "CFO: We are targeting PAT margins in the range of 15% to 20%.")
    n = build_notification(market_db, current)
    assert 1 <= len(n.management_highlights) <= 2
    for row in n.management_highlights:
        assert row.basis == "MANAGEMENT_CLAIM"
        source = n.evidence_registry[row.evidence_ids[0]]
        assert source["kind"] == "MANAGEMENT_STATEMENT"
        assert row.text in source["source_quote"]
    rendered = "".join(format_earnings(n))
    assert "200 GPUs" in rendered and "99 GPUs" not in rendered


def test_simple_alert_retains_material_conflicts_and_staleness(market_db):
    _, _, current = results(market_db)
    n = build_notification(market_db, current)
    n.verification["status"] = "CONFLICTING"
    n.freshness["status"] = "POTENTIALLY_STALE"
    text = "".join(format_earnings(n))
    assert "Source figures conflict" in text and "older stored quarter" in text


@pytest.mark.parametrize("base", ["http://reports.example.com", "https://localhost", "https://127.0.0.1", "https://192.168.1.1", "https://user:pass@reports.example.com", "https://mac.local"])
def test_insecure_or_local_full_report_links_are_never_emitted(monkeypatch, base):
    monkeypatch.setattr(settings, "report_public_base_url", base)
    monkeypatch.setattr(settings, "report_access_secret", SecretStr("s"*32))
    assert signed_link(1).url is None


def test_report_signatures_expiry_tampering_and_resource_scope(monkeypatch):
    monkeypatch.setattr(settings, "report_public_base_url", "https://reports.example.com")
    monkeypatch.setattr(settings, "report_access_secret", SecretStr("s"*32))
    link = signed_link(7)
    parameters = parse_qs(urlsplit(link.url).query)
    expires, sig = int(parameters["expires"][0]), parameters["signature"][0]
    assert authorized("7", expires, sig)
    assert not authorized("8", expires, sig)
    assert not authorized("7/documents/2", expires, sig)
    assert not authorized("7", expires-86401, sig)
    assert not authorized("7", expires, "0"*64)
    assert "s"*32 not in link.url


def test_authenticated_report_and_preview_routes(market_db, monkeypatch):
    _, _, current = results(market_db)
    monkeypatch.setattr(settings, "report_public_base_url", "https://reports.example.com")
    monkeypatch.setattr(settings, "report_access_secret", SecretStr("s"*32))
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            preview = client.get(f"/api/telegram/earnings/{current.id}/preview")
            assert preview.status_code == 200, preview.text
            url = signed_link(current.id).url
            path = urlsplit(url).path+"?"+urlsplit(url).query
            response = client.get(path)
            assert response.status_code == 200, response.text
            assert "Source and calculation lineage" in response.text
            assert response.headers["referrer-policy"] == "no-referrer"
            assert client.get(path.replace("signature=", "signature=0")).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_unsupported_old_ai_claim_falls_back_to_verified_report(market_db):
    issuer, _, current = results(market_db)
    record = AIAnalysis(company_id=issuer.id, financial_period_id=current.id, analysis_type="FINANCIAL_RESULTS",
        model_provider="fake", model_name="fake", prompt_version="old", context_hash="fake", context_chars=1,
        evidence_registry={}, status="SUCCESS", structured_output={"quarterly_review": {"quarterly_investor_view": [
            {"text": "Revenue grew 9999% QoQ.", "evidence_ids": ["UNKNOWN"], "basis": "INTERPRETATION"}]}})
    market_db.add(record); market_db.commit()
    n = build_notification(market_db, current, record)
    assert n.ai_status == "VERIFIED_FALLBACK" and "9999" not in n.verdict.text


def test_durable_multipart_receipts_and_stable_result_idempotency(market_db, monkeypatch):
    _, _, current = results(market_db)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:test"))
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    first = alerts.enqueue_result(market_db, current)
    assert alerts.enqueue_result(market_db, current).id == first.id
    first.message_parts, first.parse_mode, first.ai_status = ["<b>first</b>", "<b>second</b>"], "HTML", "SUCCESS"
    market_db.commit()
    factory = sessionmaker(bind=market_db.get_bind(), expire_on_commit=False)
    sent = []
    async def send(chat, text, **kwargs):
        assert kwargs["parse_mode"] == "HTML"
        sent.append(text)
        if text.endswith("second</b>") and sent.count(text) == 1:
            raise TelegramError("Telegram API error 429", retry_after=90)
        return len(sent)+100
    monkeypatch.setattr(alerts, "send_message", send)
    base = alerts.now()
    assert asyncio.run(alerts.deliver_one(factory))
    monkeypatch.setattr(alerts, "now", lambda: base+timedelta(seconds=10))
    assert not asyncio.run(alerts.deliver_one(factory))
    monkeypatch.setattr(alerts, "now", lambda: base+timedelta(seconds=101))
    assert asyncio.run(alerts.deliver_one(factory))
    market_db.refresh(first)
    assert first.status == "SENT" and len(first.message_ids) == 2
    assert sent.count("<b>first</b>") == 1
    assert not asyncio.run(alerts.deliver_one(factory))


def test_uncertain_sends_and_expired_inflight_lease_never_auto_retry(telegram_env, monkeypatch):
    factory, storage, _ = telegram_env
    from tests.test_telegram import save
    save(factory, storage)
    async def uncertain(*args, **kwargs):
        raise TelegramError("Telegram delivery outcome is unknown", uncertain=True)
    monkeypatch.setattr(alerts, "send_message", uncertain)
    assert not asyncio.run(alerts.deliver_one(factory))
    with factory() as db:
        job = db.scalar(select(TelegramDelivery))
        assert job.status == "UNCERTAIN" and job.sending_part == 0
    assert not asyncio.run(alerts.deliver_one(factory))
    save(factory, storage, key="another")
    claim = alerts._claim(factory)
    with factory() as db:
        job = db.get(TelegramDelivery, claim[0]); job.sending_part = 0
        job.lease_until = alerts.now()-timedelta(seconds=1); db.commit()
    assert alerts._claim(factory) is None
    with factory() as db:
        assert db.get(TelegramDelivery, claim[0]).status == "UNCERTAIN"


def test_read_timeout_is_uncertain_connect_failure_is_retryable(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:secret"))
    for failure, uncertain in [(httpx.ReadTimeout, True), (httpx.ConnectError, False)]:
        def respond(request): raise failure("do not echo token", request=request)
        with pytest.raises(TelegramError) as exc:
            asyncio.run(call_bot("sendMessage", transport=httpx.MockTransport(respond)))
        assert exc.value.uncertain is uncertain
        assert "secret" not in str(exc.value)


def test_end_to_end_ingestion_earnings_analysis_queue_and_delivery(telegram_env, monkeypatch):
    from app.ai.providers.base import LLMError
    from app.ingestion.monitor import check_for_events
    from app.ingestion.sources.base import DownloadedDocument, EventSource, SourceEvent
    from tests.test_financials import synthetic_pdf
    factory, storage, _ = telegram_env
    class OfflineProvider:
        name, model, supports_quarterly_review = "offline-test", "test", True
        def generate(self, *args): raise LLMError("CODEX_UNAVAILABLE")
    monkeypatch.setattr("app.ai.analyst.service.configured_provider", lambda: OfflineProvider())
    monkeypatch.setattr(settings, "document_storage_path", storage.root)
    class Source(EventSource):
        name = "NSE"
        broken = False
        async def fetch_events(self, companies, since):
            return [SourceEvent(external_symbol="ABC", title="Financial Results Q1 FY2027",
                published_at=alerts.now(), external_id="earnings-one",
                source_url="https://nsearchives.nseindia.com/results.pdf",
                document_url="https://nsearchives.nseindia.com/results.pdf", raw_payload={"issuer_symbol": "ABC"})]
        async def download_document(self, event):
            if self.broken: raise RuntimeError("PDF temporarily unavailable")
            return DownloadedDocument(synthetic_pdf(company_name="ABC Ltd"), "application/pdf", "results.pdf")
    source = Source()
    with factory() as db:
        summary = asyncio.run(check_for_events(db, sources=[source], storage=storage, defer_commentary=True))
        assert summary.new_events == 1 and not summary.errors
        row = db.scalar(select(ResultDiscovery))
        assert row.status == "INGESTED" and row.period_end == date(2026, 6, 30)
    sent = []
    async def send(chat, text, **kwargs):
        sent.append(text)
        assert kwargs.get("parse_mode") == "HTML"
        return len(sent)+100
    monkeypatch.setattr(alerts, "send_message", send)
    current = alerts.now()
    for step in range(6):
        monkeypatch.setattr(alerts, "now", lambda step=step: current+timedelta(seconds=10*step))
        asyncio.run(alerts.deliver_one(factory))
    with factory() as db:
        job = db.scalar(select(TelegramDelivery))
        assert job.status == "SENT" and job.financial_analysis_id and job.analysis_id is None
        assert job.notification["reporting_basis"] == "CONSOLIDATED"
        assert job.notification["schema_version"] == "EARNINGS_ALERT_V2"
        assert job.notification["verification"]["status"] == "VERIFIED", job.notification["verification"]
        assert db.query(AIAnalysis).count() == 1
        again = asyncio.run(check_for_events(db, sources=[source], storage=storage, defer_commentary=True))
        assert again.new_events == 0
        assert db.query(TelegramDelivery).count() == 1 and db.query(AIAnalysis).count() == 1
    assert "RESULT HIGHLIGHTS" in "".join(sent)
    assert "AI THESIS" in "".join(sent)
    assert "VERIFIED FALLBACK" not in "".join(sent)


def test_discovery_survives_failed_download_then_existing_workflow_recovers(telegram_env, monkeypatch):
    from app.ingestion.monitor import check_for_events
    from app.ingestion.sources.base import DownloadedDocument, EventSource, SourceEvent
    from tests.test_financials import synthetic_pdf
    factory, storage, _ = telegram_env
    monkeypatch.setattr(settings, "document_storage_path", storage.root)
    class Source(EventSource):
        name, broken = "NSE", True
        async def fetch_events(self, companies, since):
            return [SourceEvent(external_symbol="ABC", title="Financial Results Q2 FY2027", published_at=alerts.now(),
                external_id="awaiting", source_url="https://nsearchives.nseindia.com/results.pdf",
                document_url="https://nsearchives.nseindia.com/results.pdf", raw_payload={"issuer_symbol": "ABC"})]
        async def download_document(self, event):
            if self.broken: raise RuntimeError("Unavailable")
            return DownloadedDocument(synthetic_pdf(company_name="ABC Ltd"), "application/pdf", "results.pdf")
    source = Source()
    with factory() as db:
        first = asyncio.run(check_for_events(db, sources=[source], storage=storage, defer_commentary=True))
        row = db.scalar(select(ResultDiscovery))
        assert first.sources["NSE"].document_failures == 1
        assert row.status == "AWAITING_INGESTION" and row.period_end == date(2026, 9, 30)
        source.broken = False
        second = asyncio.run(check_for_events(db, sources=[source], storage=storage, defer_commentary=True))
        db.refresh(row)
        # The observed Q2 title conflicts with the actual Q1 PDF: never silently hide that discrepancy.
        assert second.sources["NSE"].document_failures == 0
        assert row.event_id is not None and row.status == "NEEDS_REVIEW"
        assert row.period_end == date(2026, 9, 30)


def test_deliberate_analysis_versions_are_separately_idempotent(market_db, monkeypatch):
    issuer, _, current = results(market_db)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:test"))
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    automatic = alerts.enqueue_result(market_db, current)
    record = AIAnalysis(company_id=issuer.id, financial_period_id=current.id, model_provider="test", model_name="test",
        prompt_version="v2", context_hash="new", context_chars=1, evidence_registry={}, status="SUCCESS")
    market_db.add(record); market_db.flush()
    deliberate = alerts.enqueue_result(market_db, current, analysis=record)
    assert deliberate.id != automatic.id and deliberate.idempotency_key != automatic.idempotency_key
    assert alerts.enqueue_result(market_db, current, analysis=record).id == deliberate.id


def test_additive_migration_preserves_legacy_receipts(tmp_path):
    import importlib.util
    from sqlalchemy import create_engine
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.database import Base
    from pathlib import Path
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    omitted = {"telegram_deliveries", "result_discoveries", "result_source_checks", "result_verifications"}
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in omitted: table.create(connection)
        connection.exec_driver_sql("ALTER TABLE financial_facts DROP COLUMN rounding_tolerance")
        context = MigrationContext.configure(connection)
        def load(name):
            path = Path(__file__).parents[1] / "alembic/versions" / name
            spec = __import__('importlib.util', fromlist=['spec_from_file_location']).spec_from_file_location(name, path)
            module = __import__('importlib.util', fromlist=['module_from_spec']).module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        with Operations.context(context):
            load("0023_telegram_deliveries.py").upgrade()
            connection.exec_driver_sql("INSERT INTO telegram_deliveries (event_id, chat_id, status, attempts, next_attempt_at, message_id) VALUES (1,'456','SENT',1,'2026-10-22',99)")
            load("0024_intelligent_earnings_alerts.py").upgrade()
        row = connection.exec_driver_sql("SELECT status, message_id, version, idempotency_key FROM telegram_deliveries").one()
        assert tuple(row) == ("SENT", 99, "automatic-v2", None)
    engine.dispose()


def test_confidence_rubric_caps_missing_history_and_failed_source_check(market_db):
    issuer, _, current = results(market_db)
    event, document, raw = official_candidate(market_db, issuer, current)
    compare_authoritative_candidate(market_db, current, event, document, raw)
    check = SimpleNamespace(company_id=issuer.id, symbol=issuer.symbol)
    source_check(market_db, [check], "NSE", "SUCCESS")
    notification = build_notification(market_db, current)
    assert notification.confidence["level"] == "MEDIUM"
    assert notification.confidence["checks"]["recent_source_check"]
    assert not notification.confidence["checks"]["comparison_sources_verified"]
    source_check(market_db, [check], "NSE", "FAILED", error="ProviderUnavailable")
    assert build_notification(market_db, current).confidence["level"] == "LOW"


def test_reported_precision_signed_capex_and_attributable_profit(market_db):
    from app.services.financials.calculator import printed_rounding
    assert printed_rounding(Decimal("1336.57"), OriginalUnit.INR_MILLION) == Decimal(".0005")
    assert printed_rounding(Decimal("100"), OriginalUnit.INR_CRORE) == Decimal(".5")
    _, _, current = results(market_db)
    set_facts(market_db, current, {"PAT_ATTRIBUTABLE": "6", "NON_CONTROLLING_INTEREST": "1", "CAPITAL_EXPENDITURE": "-3"})
    capex = next(f for f in current.facts if f.metric_code == MetricCode.CAPITAL_EXPENDITURE)
    capex.source_label = "Purchase of property, plant and equipment"
    market_db.commit()
    quality = extend_results_evidence(market_db, current, {})
    assert quality["reconciliation"]["attributable_status"] == "RECONCILED"
    assert Decimal(quality["observations"]["free_cash_flow"]["value"]) == 12
    set_facts(market_db, current, {"NON_CONTROLLING_INTEREST": "3"})
    assert build_notification(market_db, current).contradictions


def test_partial_income_data_still_allows_a_reconciled_pbt_tax_bridge(market_db):
    _, _, current = results(market_db)
    next(f for f in current.facts if f.metric_code == MetricCode.OTHER_INCOME).status = FactStatus.NEEDS_REVIEW
    market_db.commit()
    quality = extend_results_evidence(market_db, current, {})
    assert [row["component"] for row in quality["pat_bridge"]] == ["PBT", "TOTAL_TAX_EXPENSE"]
    assert sum(Decimal(row["contribution_crore"]) for row in quality["pat_bridge"]) == -3
    assert "detailed_operating_bridge" in quality["unassessed"]


def test_explicit_exceptional_gains_are_quantified_without_guessing_sign(market_db):
    _, previous, current = results(market_db)
    set_facts(market_db, previous, {"EXCEPTIONAL_ITEMS": "0"})
    set_facts(market_db, current, {"EXCEPTIONAL_ITEMS": "5", "PBT": "27", "PAT": "12"})
    for p in (previous, current):
        next(f for f in p.facts if f.metric_code == MetricCode.EXCEPTIONAL_ITEMS).source_label = "Exceptional items (gain)"
    market_db.commit()
    registry = {}
    quality = extend_results_evidence(market_db, current, registry)
    exceptional = next(row for row in quality["pat_bridge"] if row["component"] == "EXCEPTIONAL_ITEMS")
    assert Decimal(exceptional["contribution_crore"]) == 5
    assert sum(Decimal(row["contribution_crore"]) for row in quality["pat_bridge"]) == 2
    assert "signed current" in registry[exceptional["evidence_ids"][0]]["formula"]


def test_signed_delivery_report_uses_persisted_snapshot_and_scopes_pdf(market_db, monkeypatch):
    _, _, current = results(market_db)
    monkeypatch.setattr(settings, "report_public_base_url", "https://reports.example.com")
    monkeypatch.setattr(settings, "report_access_secret", SecretStr("s"*32))
    notification = build_notification(market_db, current)
    notification.verdict.text = "This is the persisted, evidence-backed alert snapshot."
    job = TelegramDelivery(event_id=current.source_event_id, chat_id="456", status="SENT", attempts=1,
        next_attempt_at=alerts.now(), notification=notification.model_dump(mode="json"))
    market_db.add(job); market_db.commit()
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            url = urlsplit(signed_link(current.id, delivery_id=job.id).url)
            response = client.get(url.path+"?"+url.query)
            assert response.status_code == 200
            assert notification.verdict.text in response.text
            assert "Additional financial metrics" in response.text
            wrong = client.get(url.path.replace("/deliveries/", "/documents/")+"?"+url.query)
            assert wrong.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_report_access_log_redaction_preserves_uvicorn_arguments():
    import logging
    from uvicorn.logging import AccessFormatter
    from app.services.report_links import _RedactReportAccess
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/api/reports/earnings/1?expires=1&signature="+"a"*64, "1.1", 200), None)
    assert _RedactReportAccess().filter(record)
    formatted = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(record)
    assert "a"*64 not in formatted and "[REDACTED]" in formatted


def test_queue_keeps_requested_period_and_rejects_silent_basis_switch(market_db, monkeypatch):
    issuer, _, current = results(market_db)
    monkeypatch.setattr(settings, "telegram_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", SecretStr("123:test"))
    monkeypatch.setattr(settings, "telegram_chat_id", "456")
    job = alerts.enqueue_result(market_db, current)
    assert job.financial_period_id == current.id
    standalone = add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20), scope=FinancialScope.STANDALONE)
    standalone.source_event_id = current.source_event_id
    market_db.commit()
    with pytest.raises(ValueError, match="another reporting basis"):
        alerts.enqueue_result(market_db, standalone)


def test_reported_ebitda_is_not_replaced_or_compared_with_proxy(market_db):
    issuer, previous, current = results(market_db)
    set_facts(market_db, current, {"EBITDA": "50"})
    calculate_company_metrics(market_db, issuer.id)
    n = build_notification(market_db, current)
    reported = next(row for row in n.snapshot if row.code == "EBITDA")
    assert reported.value == 50 and reported.basis == "REPORTED_FACT"
    assert reported.qoq is None
    assert not any(row.code == "EBITDA_PROXY_MARGIN" for row in n.additional_metrics)
    set_facts(market_db, previous, {"EBITDA": "25"})
    calculate_company_metrics(market_db, issuer.id)
    reported = next(row for row in build_notification(market_db, current).snapshot if row.code == "EBITDA")
    assert reported.qoq.value == 100


def test_newer_detection_is_not_hidden_by_a_deduplicated_older_pdf(market_db):
    from app.services.result_sources import link_discovery
    issuer, _, current = results(market_db)
    row = record_discovery(market_db, discovery(issuer))
    link_discovery(market_db, row, current.source_event_id)
    assert row.period_end == date(2026, 9, 30) and row.status == "NEEDS_REVIEW"
    assert row.details["period_conflict"]["stored_pdf_period_end"] == "2026-06-30"
    assert freshness_view(market_db, current)["newer_awaiting_ingestion"]
