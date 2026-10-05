"""Management commentary uses the existing event, evidence and memory pipelines."""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.ai.analyst.validator import InvalidAnalysis, _check_semantics
from app.ingestion.normalizer import NormalizedEvent, classify_event, normalize_event
from app.ingestion.sources.base import DownloadedDocument, MonitoredCompany
from app.ingestion.sources.nse import parse_nse_announcements
from app.ingestion.storage import LocalDocumentStorage
from app.models.company import Company
from app.models.commentary import CommentaryDocument, ManagementGuidance, ManagementStatement
from app.models.event import CompanyEvent, DataOrigin, Document, DocumentPage, EventReference, EventType, ProcessingStatus, PublicationPrecision
from app.models.financial import FinancialFact, FinancialMetric, FinancialPeriod, FinancialScope, FinancialStatus, FinancialUnit, FactMethod, FactStatus, MetricCode, PeriodType
from app.schemas.memory import MemoryQuery
from app.services.commentary import _transcript_statements, commentary_availability, ingest_commentary, parse_guidance, parse_statement, refresh_commentary_for_company
from app.services.events.event_service import save_event
from app.services.memory.service import guidance_record, manifest, query_memory
from tests.test_company_memory import Store, Embeddings


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def issuer(db, symbol):
    row = Company(symbol=symbol, name=symbol + " Limited", exchange="NSE")
    db.add(row)
    db.flush()
    return row


def test_transcript_turns_use_management_roster_and_page_evidence():
    pages = [
        DocumentPage(document_id=1, page_number=1, text=(
            "Page 1 of 3\nMANAGEMENT: MR. AARAV SHAH – CHIEF FINANCIAL\n"
            "OFFICER – Example Limited\nMODERATOR: MR. HOST NAME\n"
        )),
        DocumentPage(document_id=1, page_number=2, text=(
            "Example Limited\nPage 2 of 3\nAarav Shah:\n"
            "Revenue rose this quarter as existing customer demand strengthened.\n\n"
            "Moderator:\nWe will now take a question from an analyst.\n\n"
            "Neha Rao:\nCan you explain the margin trend for the quarter?\n\n"
            "Aarav Shah:\nWe expect capacity to increase over the coming year.\n"
        )),
        DocumentPage(document_id=1, page_number=3, text=(
            "Example Limited\nPage 3 of 3\nThe project remains in early execution.\n\n"
        )),
    ]
    rows = list(_transcript_statements(pages))
    assert [(page.page_number, row["speaker_name"], row["speaker_role"], answer)
            for page, row, answer in rows] == [
        (2, "Aarav Shah", "CFO", False),
        (2, "Aarav Shah", "CFO", True),
        (3, "Aarav Shah", "CFO", True),
    ]
    assert all("Can you explain" not in row["raw_statement"] for _, row, _ in rows)


def event_doc(db, company, kind, title, published, *, source="NSE", origin=DataOrigin.LIVE, text=None):
    event = CompanyEvent(company_id=company.id, event_type=kind, title=title,
        published_at=datetime.fromisoformat(published).replace(tzinfo=timezone.utc),
        source=source, source_url=f"https://example.org/{uuid4().hex}",
        data_origin=origin, fingerprint=uuid4().hex,
        processing_status=ProcessingStatus.PROCESSED)
    db.add(event)
    db.flush()
    if text is None:
        return event, None
    document = Document(company_id=company.id, event_id=event.id, source=source,
        source_url=event.source_url, document_url=event.source_url + ".pdf",
        processing_status=ProcessingStatus.PROCESSED, published_at=event.published_at)
    db.add(document)
    db.flush()
    db.add(DocumentPage(document_id=document.id, page_number=3, text=text))
    db.flush()
    return event, document


def result_period(db, company, end, publication, *, scope=FinancialScope.CONSOLIDATED,
                  kind=PeriodType.QUARTERLY, quarter=1, fy="FY2027"):
    event, doc = event_doc(db, company, EventType.FINANCIAL_RESULTS,
        f"{fy} results", publication, text="Financial results")
    start = (date(end.year - 1, 4, 1) if kind == PeriodType.ANNUAL else {
        1: date(end.year, 4, 1), 2: date(end.year, 7, 1),
        3: date(end.year, 10, 1), 4: date(end.year, 1, 1),
    }[quarter])
    period = FinancialPeriod(company_id=company.id, fiscal_year=fy, quarter=quarter,
        period_start=start,
        period_end=end, period_type=kind, scope=scope, status=FinancialStatus.VALIDATED,
        source_event_id=event.id, source_document_id=doc.id)
    db.add(period)
    db.flush()
    return period


@pytest.mark.parametrize(("title", "expected"), [
    ("Investor Presentation Q1 FY27 Results", EventType.INVESTOR_PRESENTATION),
    ("Earnings/Press Release Q1 FY27 Results", EventType.EARNINGS_RELEASE),
    ("Con. Call Updates", EventType.EARNINGS_CALL),
    ("Earnings Call Transcript Q1 FY27", EventType.CONCALL_TRANSCRIPT),
    ("Audio/Video Recording of Earnings Call", EventType.CONCALL_AUDIO),
    ("Board Meeting Outcome & Q1 FY27 Results", EventType.FINANCIAL_RESULTS),
])
def test_nse_commentary_classification(title, expected):
    assert classify_event(title) == expected


def test_nse_rss_discovers_pdf_and_audio_without_fabricating_audio_document():
    company = MonitoredCompany(7, "DEMO", "Demo Limited", "DEMO",
        source_company_id="Demo Limited")
    xml = b'''<rss><channel><lastBuildDate>Sun, 27 Sep 2026 18:26:41 +0530</lastBuildDate>
    <item><title>Demo Limited</title><link>https://nsearchives.nseindia.com/corporate/deck.pdf</link>
    <description>Demo Limited filing |SUBJECT: Q1 FY27 Investor Presentation</description>
    <pubDate>24-Sep-2026 20:25:00</pubDate></item>
    <item><title>Demo Limited</title><link>https://nsearchives.nseindia.com/corporate/call.mp3</link>
    <description>Demo Limited filing |SUBJECT: Earnings Call Audio Recording</description>
    <pubDate>24-Sep-2026 20:26:00</pubDate></item></channel></rss>'''
    items = parse_nse_announcements(xml, [company])
    normalized = [normalize_event(item, "NSE", [company]) for item in items]
    assert [row.event_type for row in normalized] == [EventType.INVESTOR_PRESENTATION, EventType.CONCALL_AUDIO]
    assert normalized[0].document_url.endswith("deck.pdf")
    assert normalized[1].document_url is None
    assert normalized[1].source_url.endswith("call.mp3")
    assert all(row.source == "NSE" and row.data_origin == DataOrigin.LIVE for row in normalized)


def test_period_association_priority_isolation_and_idempotence(db):
    company, other = issuer(db, "ONE"), issuer(db, "TWO")
    period = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    future = result_period(db, company, date(2026, 9, 30), "2026-10-20", quarter=2)
    result_period(db, other, date(2026, 6, 30), "2026-07-20")
    transcript, document = event_doc(db, company, EventType.CONCALL_TRANSCRIPT,
        "Con. Call Transcript", "2026-07-25",
        text="CEO: We expect FY27 revenue growth of 20–25%.\nCFO: Demand remains strong in our core market.")
    event_doc(db, company, EventType.INVESTOR_PRESENTATION, "Investor Presentation Q2 FY27",
        "2026-10-25", text="CEO: We expect FY27 revenue growth of 30%.")
    event_doc(db, other, EventType.CONCALL_TRANSCRIPT, "Con. Call Transcript Q1 FY27",
        "2026-07-25", text="CEO: We expect FY27 revenue growth of 40%.")
    manual, manual_doc = event_doc(db, company, EventType.INVESTOR_PRESENTATION,
        "Q1 FY27 Investor Presentation", "2026-07-26", source="MANUAL_UPLOAD",
        origin=DataOrigin.MANUAL, text="CFO: We expect FY27 capex of ₹500 crore.")
    db.commit()
    first = ingest_commentary(db, period)
    second = ingest_commentary(db, period)
    assert len(first) == len(second) == 3
    assert {s.document_id for s in first} == {document.id, manual_doc.id}
    assert {s.page_number for s in first} == {3}
    assert {s.company_id for s in first} == {company.id}
    assert db.scalar(select(func.count()).select_from(ManagementGuidance)) == 2
    links = list(db.scalars(select(CommentaryDocument).where(CommentaryDocument.financial_period_id == period.id)
        .order_by(CommentaryDocument.source_priority)))
    assert [link.source for link in links] == ["NSE", "MANUAL_UPLOAD"]
    assert manual.data_origin == DataOrigin.MANUAL and manual_doc.source == "MANUAL_UPLOAD"
    assert commentary_availability(db, period)["status"] == "TRANSCRIPT_AVAILABLE"
    assert ingest_commentary(db, future)[0].financial_period_id == future.id


def test_numeric_guidance_requires_explicit_target_and_unit():
    revenue = parse_guidance(parse_statement("CEO: We expect FY27 revenue growth of 20–25%."))
    capex = parse_guidance(parse_statement("CFO: We target FY27 capex of ₹500 crore."))
    assert revenue == {"metric": "REVENUE_GROWTH", "target_period": "FY27",
                       "lower_bound": Decimal(20), "upper_bound": Decimal(25), "unit": "PERCENT"}
    assert capex == {"metric": "CAPEX", "target_period": "FY27",
                     "lower_bound": Decimal(500), "upper_bound": Decimal(500), "unit": "INR_CRORE"}
    assert parse_guidance(parse_statement("CEO: We expect strong growth and improving margins.")) is None
    assert parse_guidance(parse_statement("CEO: We expect revenue growth of 25%.")) is None
    assert parse_guidance(parse_statement("CEO: We expect Q2 FY27 revenue growth of 25%."))["target_period"] == "Q2FY27"
    assert parse_guidance(parse_statement("CEO: We expect FY27-28 revenue growth of 25%.")) is None
    assert parse_guidance(parse_statement("CFO: We target FY27 debt reduction of ₹300 crore."))["metric"] == "DEBT_REDUCTION"
    assert parse_guidance(parse_statement("CFO: We target FY27 to reduce debt to ₹300 crore."))["metric"] == "DEBT"
    assert parse_statement("Revenue rose 25% due to strong demand.") is None


def test_explicit_scope_in_commentary_title_is_not_mixed(db):
    company = issuer(db, "SCOPE")
    consolidated = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    standalone = result_period(db, company, date(2026, 6, 30), "2026-07-20",
        scope=FinancialScope.STANDALONE)
    _, document = event_doc(db, company, EventType.INVESTOR_PRESENTATION,
        "Standalone Q1 FY27 Investor Presentation", "2026-07-25",
        text="CEO: We expect FY27 revenue growth of 20–25%.")
    db.commit()
    assert ingest_commentary(db, consolidated) == []
    assert {row.document_id for row in ingest_commentary(db, standalone)} == {document.id}
    assert db.scalar(select(ManagementGuidance)).financial_period_id == standalone.id


def test_audio_only_and_no_commentary_states(db):
    company = issuer(db, "AUDIO")
    period = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    db.commit()
    assert commentary_availability(db, period)["status"] == "NO_COMMENTARY_FOUND"
    event_doc(db, company, EventType.EARNINGS_CALL, "Analyst Meet Invitation",
        "2026-07-22", text="Join the conference call using the link below.")
    db.commit()
    assert commentary_availability(db, period)["status"] == "NO_COMMENTARY_FOUND"
    audio, _ = event_doc(db, company, EventType.CONCALL_AUDIO,
        "Audio/Video Recording of Earnings Call", "2026-07-23")
    db.commit()
    state = commentary_availability(db, period)
    assert state["status"] == "AUDIO_AVAILABLE"
    assert state["audio_sources"][0]["url"] == audio.source_url
    assert not ingest_commentary(db, period)


def test_commentary_uses_existing_phase7_index_and_asof_filter(db, monkeypatch):
    company, other = issuer(db, "INDEX"), issuer(db, "OTHER")
    period = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    _, doc = event_doc(db, company, EventType.CONCALL_TRANSCRIPT,
        "Q1 FY27 Earnings Call Transcript", "2026-07-25",
        text="CFO: We expect FY27 revenue growth of 20–25% from enterprise demand.")
    event_doc(db, other, EventType.CONCALL_TRANSCRIPT, "Q1 FY27 Earnings Call Transcript",
        "2026-07-25", text="CFO: We expect FY27 revenue growth of 40% from export demand.")
    db.commit()
    store, embedder = Store(), Embeddings()
    from app.services.memory import clients
    monkeypatch.setattr(clients, "QdrantStore", lambda: store)
    monkeypatch.setattr(clients, "OllamaEmbeddings", lambda: embedder)
    assert refresh_commentary_for_company(db.get_bind(), company.id) == {
        "associated_documents": 1, "indexed": 1}
    assert db.scalar(select(func.count()).select_from(CommentaryDocument)) == 1
    payload = next(p["payload"] for p in store.points.values() if p["payload"]["document_id"] == doc.id)
    assert payload["document_type"] == "CONCALL_TRANSCRIPT"
    assert payload["page"] == 3 and payload["company_id"] == company.id
    assert payload["source"] == "NSE" and payload["speaker"] == "CFO"
    assert (payload["fiscal_year"], payload["quarter"], payload["reporting_basis"]) == (
        "FY2027", 1, "CONSOLIDATED")
    found = query_memory(db, company.id, MemoryQuery(query="revenue guidance outlook",
        as_of_date=date(2026, 7, 26)), store=store, embedder=embedder)
    assert any(row["document_id"] == doc.id for row in found["results"])
    early = query_memory(db, company.id, MemoryQuery(query="revenue guidance outlook",
        as_of_date=date(2026, 7, 24)), store=store, embedder=embedder)
    assert all(row["document_id"] != doc.id for row in early["results"])
    assert not query_memory(db, other.id, MemoryQuery(query="revenue guidance outlook",
        as_of_date=date(2026, 7, 26)), store=store, embedder=embedder)["results"]


def test_guidance_history_waits_for_verified_same_scope_actual(db):
    company = issuer(db, "HISTORY")
    quarter = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    _, doc = event_doc(db, company, EventType.CONCALL_TRANSCRIPT, "Q1 FY27 Earnings Call Transcript",
        "2026-07-25", text="CFO: We expect FY27 revenue growth of 20–25%.")
    db.commit()
    ingest_commentary(db, quarter)
    statement = db.scalar(select(ManagementStatement))
    guidance = db.scalar(select(ManagementGuidance))
    before = guidance_record(db, guidance, statement, date(2027, 2, 1))
    assert before["execution_outcome"] == "TOO_EARLY_TO_ASSESS"
    prior = result_period(db, company, date(2026, 3, 31), "2026-04-20",
        kind=PeriodType.ANNUAL, quarter=None, fy="FY2026")
    actual = result_period(db, company, date(2027, 3, 31), "2027-04-20",
        kind=PeriodType.ANNUAL, quarter=None)
    facts = []
    for period, amount in ((prior, 100), (actual, 124)):
        fact = FinancialFact(financial_period_id=period.id, metric_code=MetricCode.REVENUE_FROM_OPERATIONS,
            value=Decimal(amount), unit=FinancialUnit.INR_CRORE,
            source_document_id=period.source_document_id, source_page=3,
            source_label="Revenue", source_column_date=period.period_end,
            extraction_method=FactMethod.PDF_TABLE, status=FactStatus.VERIFIED_PDF)
        db.add(fact)
        facts.append(fact)
    db.flush()
    db.add(FinancialMetric(financial_period_id=actual.id, metric_code=MetricCode.REVENUE_YOY,
        value=Decimal(24), unit=FinancialUnit.PERCENT,
        input_fact_ids=[fact.id for fact in facts], comparison_period_id=prior.id))
    db.commit()
    assert guidance_record(db, guidance, statement, date(2027, 2, 1))["actual_value"] is None
    delivered = guidance_record(db, guidance, statement, date(2027, 4, 21))
    assert delivered["execution_outcome"] == "ACHIEVED"
    assert delivered["actual_value"] == "24.000000"
    assert {entry["page"] for entry in delivered["actual_sources"]} == {3}
    evidence = {delivered["evidence_id"]: delivered}
    _check_semantics("Management achieved revenue guidance.", list(evidence), evidence, "summary")
    with pytest.raises(InvalidAnalysis, match="HISTORICAL_EXECUTION_UNSUPPORTED"):
        _check_semantics("Management missed revenue guidance.", list(evidence), evidence, "summary")
    standalone = result_period(db, company, date(2027, 3, 31), "2027-04-20",
        scope=FinancialScope.STANDALONE, kind=PeriodType.ANNUAL, quarter=None)
    db.commit()
    assert standalone.scope != quarter.scope
    assert guidance_record(db, guidance, statement, date(2027, 4, 21))["actual_evidence_ids"] == delivered["actual_evidence_ids"]


def test_guidance_versions_are_preserved_and_vague_outlook_is_not_numeric(db):
    company = issuer(db, "VERSIONS")
    period = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    event_doc(db, company, EventType.CONCALL_TRANSCRIPT, "Q1 FY27 Earnings Call Transcript",
        "2026-07-25", text="CEO: We expect FY27 revenue growth of 20–25%.")
    event_doc(db, company, EventType.INVESTOR_PRESENTATION, "Q1 FY27 Investor Presentation",
        "2026-08-01", text="CEO: We now target FY27 revenue growth of 25–30%.\nCEO: We expect strong demand next year.")
    db.commit()
    ingest_commentary(db, period)
    rows = list(db.scalars(select(ManagementGuidance).order_by(ManagementGuidance.published_at)))
    assert len(rows) == 2
    old, new = rows
    assert old.lower_bound == Decimal(20) and new.lower_bound == Decimal(25)
    statement = db.get(ManagementStatement, new.statement_id)
    assert guidance_record(db, new, statement, date(2026, 8, 2))["change"] == "GUIDANCE_RAISED"
    assert len(ingest_commentary(db, period)) == 3
    assert db.scalar(select(func.count()).select_from(ManagementGuidance)) == 2


def test_margin_capex_and_debt_reduction_require_verified_annual_inputs(db):
    company = issuer(db, "TARGETS")
    quarter = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    call, document = event_doc(db, company, EventType.CONCALL_TRANSCRIPT,
        "Q1 FY27 Earnings Call Transcript", "2026-07-25", text="Management targets")
    prior = result_period(db, company, date(2026, 3, 31), "2026-04-20",
        kind=PeriodType.ANNUAL, quarter=None, fy="FY2026")
    actual = result_period(db, company, date(2027, 3, 31), "2027-04-20",
        kind=PeriodType.ANNUAL, quarter=None)

    def fact(period, code, value):
        row = FinancialFact(financial_period_id=period.id, metric_code=code,
            value=Decimal(value), unit=FinancialUnit.INR_CRORE,
            source_document_id=period.source_document_id, source_page=3,
            source_label=code.value, source_column_date=period.period_end,
            extraction_method=FactMethod.PDF_TABLE, status=FactStatus.VERIFIED_PDF)
        db.add(row)
        db.flush()
        return row

    margin_inputs = [fact(actual, code, value) for code, value in (
        (MetricCode.REVENUE_FROM_OPERATIONS, 100), (MetricCode.PBT, 20),
        (MetricCode.FINANCE_COST, 2), (MetricCode.DEPRECIATION, 3))]
    db.add(FinancialMetric(financial_period_id=actual.id, metric_code=MetricCode.EBITDA_MARGIN,
        value=Decimal(25), unit=FinancialUnit.PERCENT,
        input_fact_ids=[row.id for row in margin_inputs]))
    capex = fact(actual, MetricCode.CAPITAL_EXPENDITURE, 500)
    fact(prior, MetricCode.TOTAL_DEBT, 800)
    fact(actual, MetricCode.TOTAL_DEBT, 500)

    guidance_rows = []
    for page, raw in enumerate((
        "CFO: We target FY27 EBITDA margin of 24–26%.",
        "CFO: We target FY27 capex of ₹500 crore.",
        "CFO: We target FY27 debt reduction of ₹300 crore.",
    ), start=4):
        parsed = parse_statement(raw)
        extracted = parse_guidance(parsed)
        statement = ManagementStatement(company_id=company.id, financial_period_id=quarter.id,
            event_id=call.id, document_id=document.id, page_number=page,
            published_at=call.published_at, source_type=call.event_type.value,
            section_type="PREPARED_REMARKS", **parsed)
        db.add(statement)
        db.flush()
        guidance = ManagementGuidance(statement_id=statement.id, company_id=company.id,
            financial_period_id=quarter.id, published_at=call.published_at, **extracted)
        db.add(guidance)
        guidance_rows.append((guidance, statement))
    db.commit()
    outcomes = [guidance_record(db, guidance, statement, date(2027, 4, 21))
                for guidance, statement in guidance_rows]
    assert [(row["metric"], row["actual_value"], row["execution_outcome"])
            for row in outcomes] == [
        ("EBITDA_MARGIN", "25.000000", "ACHIEVED"),
        ("CAPEX", "500.000000", "ACHIEVED"),
        ("DEBT_REDUCTION", "300.000000", "ACHIEVED"),
    ]
    assert all(row["actual_sources"] and row["actual_evidence_ids"] for row in outcomes)
    assert guidance_record(db, guidance_rows[0][0], guidance_rows[0][1], date(2027, 4, 1))[
        "actual_value"] is None
    capex.status = FactStatus.NEEDS_REVIEW
    db.commit()
    assert guidance_record(db, guidance_rows[1][0], guidance_rows[1][1], date(2027, 4, 21))[
        "execution_outcome"] == "INSUFFICIENT_DATA"


def test_quarter_target_is_compared_only_with_matching_quarter(db):
    company = issuer(db, "QUARTER")
    q1 = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    event_doc(db, company, EventType.CONCALL_TRANSCRIPT, "Q1 FY27 Earnings Call Transcript",
        "2026-07-25", text="CEO: We expect Q2 FY27 revenue growth of 20–25%.")
    prior = result_period(db, company, date(2025, 9, 30), "2025-10-20",
        quarter=2, fy="FY2026")
    actual = result_period(db, company, date(2026, 9, 30), "2026-10-20",
        quarter=2, fy="FY2027")
    facts = []
    for period, value in ((prior, 100), (actual, 124)):
        row = FinancialFact(financial_period_id=period.id,
            metric_code=MetricCode.REVENUE_FROM_OPERATIONS, value=Decimal(value),
            unit=FinancialUnit.INR_CRORE, source_document_id=period.source_document_id,
            source_page=3, source_label="Revenue", source_column_date=period.period_end,
            extraction_method=FactMethod.PDF_TABLE, status=FactStatus.VERIFIED_PDF)
        db.add(row)
        db.flush()
        facts.append(row)
    db.add(FinancialMetric(financial_period_id=actual.id, metric_code=MetricCode.REVENUE_YOY,
        value=Decimal(24), unit=FinancialUnit.PERCENT,
        input_fact_ids=[row.id for row in facts], comparison_period_id=prior.id))
    db.commit()
    ingest_commentary(db, q1)
    guidance = db.scalar(select(ManagementGuidance))
    statement = db.get(ManagementStatement, guidance.statement_id)
    assert guidance.target_period == "Q2FY27"
    assert guidance_record(db, guidance, statement, date(2026, 9, 15))["execution_outcome"] == "TOO_EARLY_TO_ASSESS"
    assert guidance_record(db, guidance, statement, date(2026, 10, 21))["execution_outcome"] == "ACHIEVED"


def test_manual_then_nse_same_commentary_pdf_keeps_provenance(db, tmp_path):
    company = issuer(db, "DEDUP")
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "CEO: We expect FY27 revenue growth of 20–25%.")
        content = pdf.tobytes()
    download = DownloadedDocument(content, "application/pdf", "call.pdf")
    date_time = datetime(2026, 7, 25, tzinfo=timezone.utc)
    def observation(source, origin):
        return NormalizedEvent(company_id=company.id, symbol=company.symbol, source=source,
            event_type=EventType.OTHER if source == "MANUAL_UPLOAD" else EventType.CONCALL_TRANSCRIPT,
            title="Q1 FY27 Earnings Call Transcript",
            description=None, published_at=date_time, external_id=source + ":call",
            source_url=f"https://example.org/{source}", document_url=f"https://example.org/{source}.pdf",
            raw_payload=None, data_origin=origin, publication_precision=PublicationPrecision.DATE,
            period_end_date=None, fiscal_year="FY2027", quarter=1, reporting_basis=None)
    storage = LocalDocumentStorage(tmp_path)
    assert save_event(db, observation("MANUAL_UPLOAD", DataOrigin.MANUAL), download, None, storage) == (True, True)
    assert save_event(db, observation("NSE", DataOrigin.LIVE), download, None, storage) == (False, False)
    assert db.scalar(select(func.count()).select_from(Document)) == 1
    assert db.scalar(select(func.count()).select_from(EventReference)) == 2
    document = db.scalar(select(Document))
    assert document.source == "MANUAL_UPLOAD" and document.event.data_origin == DataOrigin.MANUAL
    assert document.event.event_type == EventType.CONCALL_TRANSCRIPT
    _, chunks = manifest(db, document)
    assert chunks and chunks[0]["payload"]["data_origin"] == "MANUAL"
    assert chunks[0]["payload"]["document_type"] == "CONCALL_TRANSCRIPT"
    assert {ref["source"] for ref in chunks[0]["payload"]["references"]} == {"NSE", "MANUAL_UPLOAD"}
    period = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    db.commit()
    ingest_commentary(db, period)
    association = db.scalar(select(CommentaryDocument).where(CommentaryDocument.document_id == document.id))
    assert association.source == "MANUAL_UPLOAD" and association.source_priority < 10


def test_manual_commentary_upload_associates_and_indexes_after_result(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        company = issuer(db, "UPLOAD")
        period = result_period(db, company, date(2026, 6, 30), "2026-07-20")
        db.commit()
        company_id, period_id = company.id, period.id

    def override_db():
        with Session(engine) as session:
            yield session
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(settings, "document_storage_path", tmp_path)
    from app.services.memory import clients
    store = Store()
    monkeypatch.setattr(clients, "QdrantStore", lambda: store)
    monkeypatch.setattr(clients, "OllamaEmbeddings", Embeddings)
    try:
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), "CFO: We expect FY27 revenue growth of 20-25%.")
            content = pdf.tobytes()
        with TestClient(app) as client:
            response = client.post("/api/companies/UPLOAD/events/upload-pdf", params={
                "title": "Q1 FY27 Investor Presentation", "published_on": "2026-07-25",
                "event_type": "INVESTOR_PRESENTATION", "file_name": "presentation.pdf",
            }, content=content, headers={"content-type": "application/pdf"})
        assert response.status_code == 200, response.text
        assert response.json()["data_origin"] == "MANUAL"
        with Session(engine) as db:
            association = db.scalar(select(CommentaryDocument).where(
                CommentaryDocument.company_id == company_id,
                CommentaryDocument.financial_period_id == period_id))
            assert association is not None and association.source == "MANUAL_UPLOAD"
            assert db.scalar(select(func.count()).select_from(ManagementGuidance)) == 1
            assert any(point["payload"]["document_id"] == association.document_id
                       and point["payload"]["data_origin"] == "MANUAL"
                       for point in store.points.values())
    finally:
        app.dependency_overrides.pop(get_db, None)
        engine.dispose()
