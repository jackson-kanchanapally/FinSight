import asyncio
import json
from pathlib import Path

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.ingestion.deduplicator import event_fingerprint
from app.ingestion.downloader import (
    DocumentDownloadError,
    SafePdfDownloader,
    sha256_bytes,
)
from app.ingestion.monitor import check_for_events
from app.ingestion.normalizer import classify_event, normalize_event
from app.ingestion.parsers.pdf import extract_pdf_pages
from app.ingestion.parsers.rss import MalformedFeedError, parse_rss
from app.ingestion.sources.base import (
    DownloadedDocument,
    EventSource,
    SourceEvent,
    SourceUnavailableError,
)
from app.ingestion.sources.nse import NSESource
from app.ingestion.storage import LocalDocumentStorage
from app.main import app
from app.models.event import (
    CompanyEvent,
    Document,
    DocumentPage,
    EventReference,
    EventType,
    MonitoringState,
    ProcessingStatus,
)

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "source_events.json").read_text()
)


@pytest.fixture
def event_env(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    original_storage = settings.document_storage_path
    original_mode = settings.event_source_mode
    settings.document_storage_path = tmp_path
    settings.event_source_mode = "fixture"

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client, Session() as db:
        yield client, db, LocalDocumentStorage(tmp_path)
    app.dependency_overrides.clear()
    settings.document_storage_path = original_storage
    settings.event_source_mode = original_mode
    engine.dispose()


def active_esds(client):
    assert (
        client.post(
            "/api/companies", json={"symbol": "ESDS", "name": "ESDS", "exchange": "NSE"}
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/transactions",
            json={
                "symbol": "ESDS",
                "transaction_type": "BUY",
                "quantity": "1",
                "price": "100",
                "transaction_date": "2026-09-01",
            },
        ).status_code
        == 201
    )


def pdf_content():
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Quarterly financial results")
        pdf.new_page().insert_text((72, 72), "Page two highlights")
        return pdf.tobytes()


class FakeSource(EventSource):
    def __init__(self, name, keys, content=None, failure=None):
        self.name = name
        self.keys = keys
        self.content = content or pdf_content()
        self.failure = failure
        self.broken = False
        self.download_count = 0

    async def fetch_events(self, companies, since):
        if self.failure:
            raise self.failure
        return [SourceEvent.model_validate(FIXTURES[key]) for key in self.keys]

    async def download_document(self, event):
        self.download_count += 1
        if self.broken:
            raise DocumentDownloadError("Broken fixture URL")
        return DownloadedDocument(self.content, "application/pdf", "results.pdf")


@pytest.mark.parametrize(
    "key,expected",
    [
        ("financial_results", EventType.FINANCIAL_RESULTS),
        ("board_meeting", EventType.BOARD_MEETING),
        ("investor_presentation", EventType.INVESTOR_PRESENTATION),
        ("compliance", EventType.COMPLIANCE),
        ("unknown", EventType.OTHER),
    ],
)
def test_classification_fixtures(key, expected):
    assert classify_event(FIXTURES[key]["title"]) == expected


def test_normalization_and_validation(event_env):
    client, db, _ = event_env
    active_esds(client)
    from app.ingestion.monitor import active_companies

    source = FakeSource("SOURCE_A", [])
    company = active_companies(db, source)
    raw = SourceEvent.model_validate(FIXTURES["financial_results"])
    event = normalize_event(raw, source.name, company)
    assert event.symbol == "ESDS" and event.event_type == EventType.FINANCIAL_RESULTS
    assert len(event_fingerprint(event)) == 64
    assert (
        normalize_event(
            raw.model_copy(update={"external_symbol": "UNKNOWN"}), source.name, company
        )
        is None
    )
    with pytest.raises(ValidationError):
        SourceEvent.model_validate(
            {**FIXTURES["financial_results"], "published_at": "2026-09-24T10:30:00"}
        )


def test_pdf_hash_extraction_and_storage(tmp_path):
    content = pdf_content()
    digest = sha256_bytes(content)
    assert digest == sha256_bytes(content)
    pages = extract_pdf_pages(content)
    assert pages == [(1, "Quarterly financial results"), (2, "Page two highlights")]
    storage = LocalDocumentStorage(tmp_path)
    relative = storage.store(content, digest)
    assert storage.resolve(relative).read_bytes() == content
    with pytest.raises(FileNotFoundError):
        storage.resolve("../outside.pdf")


def test_external_and_pdf_dedup_preserve_source_references(event_env):
    client, db, storage = event_env
    active_esds(client)
    content = pdf_content()
    source_a = FakeSource("SOURCE_A", ["financial_results", "duplicate_event"], content)
    source_b = FakeSource("SOURCE_B", ["duplicate_pdf_other_source"], content)
    first = asyncio.run(
        check_for_events(db, sources=[source_a, source_b], storage=storage)
    )
    assert first.new_events == 1 and first.documents_downloaded == 1
    assert db.query(CompanyEvent).count() == 1
    assert db.query(Document).count() == 1
    assert db.query(EventReference).count() == 2
    assert [
        page.page_number
        for page in db.scalars(select(DocumentPage).order_by(DocumentPage.page_number))
    ] == [1, 2]
    second = asyncio.run(
        check_for_events(db, sources=[source_a, source_b], storage=storage)
    )
    assert second.new_events == 0 and db.query(EventReference).count() == 2
    assert source_a.download_count == 1 and source_b.download_count == 1


def test_same_pdf_for_two_companies_keeps_two_events(event_env):
    client, db, storage = event_env
    active_esds(client)
    assert (
        client.post(
            "/api/companies", json={"symbol": "IEX", "name": "IEX", "exchange": "NSE"}
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/transactions",
            json={
                "symbol": "IEX",
                "transaction_type": "BUY",
                "quantity": "1",
                "price": "100",
                "transaction_date": "2026-09-01",
            },
        ).status_code
        == 201
    )
    second = {
        **FIXTURES["financial_results"],
        "external_symbol": "IEX",
        "external_id": "results-iex",
        "source_url": "https://source-a.example/iex",
        "document_url": "https://source-a.example/iex.pdf",
    }
    source = FakeSource("SOURCE_A", [])

    async def fetch(companies, since):
        return [
            SourceEvent.model_validate(FIXTURES["financial_results"]),
            SourceEvent.model_validate(second),
        ]

    source.fetch_events = fetch
    result = asyncio.run(check_for_events(db, sources=[source], storage=storage))
    assert result.new_events == 2 and db.query(Document).count() == 2


def test_different_pdfs_with_same_title_and_date_remain_separate(event_env):
    client, db, storage = event_env
    active_esds(client)
    source_a = FakeSource("SOURCE_A", ["financial_results"], pdf_content())
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Different same-day financial results")
        different_pdf = pdf.tobytes()
    source_b = FakeSource("SOURCE_B", ["duplicate_pdf_other_source"], different_pdf)
    result = asyncio.run(
        check_for_events(db, sources=[source_a, source_b], storage=storage)
    )
    assert result.new_events == 2
    assert db.query(CompanyEvent).count() == 2
    assert db.query(Document).count() == 2


def test_broken_document_is_recorded_and_retried(event_env):
    client, db, storage = event_env
    active_esds(client)
    source = FakeSource("SOURCE_A", ["broken_document"])
    source.broken = True
    first = asyncio.run(check_for_events(db, sources=[source], storage=storage))
    assert first.new_events == 1 and first.documents_downloaded == 0
    assert db.scalar(select(Document)).processing_status == ProcessingStatus.FAILED
    source.broken = False
    second = asyncio.run(check_for_events(db, sources=[source], storage=storage))
    assert second.new_events == 0 and second.documents_downloaded == 1
    assert db.scalar(select(Document)).processing_status == ProcessingStatus.PROCESSED


def test_source_timeout_unavailable_and_state(event_env):
    client, db, storage = event_env
    active_esds(client)
    assert (
        client.post(
            "/api/companies/ESDS/sources",
            json={
                "source": "NSE",
                "source_company_id": "ESDS Software Solution Limited",
            },
        ).status_code
        == 201
    )
    unavailable = NSESource()

    async def denied(companies, since):
        raise SourceUnavailableError("NSE unavailable")

    unavailable.fetch_events = denied
    timeout = FakeSource("SLOW", [], failure=httpx.ReadTimeout("timed out"))
    healthy = FakeSource("SOURCE_A", ["board_meeting"])
    result = asyncio.run(
        check_for_events(db, sources=[unavailable, timeout, healthy], storage=storage)
    )
    assert result.new_events == 1 and len(result.errors) == 2
    slow_state = db.scalar(
        select(MonitoringState).where(MonitoringState.source == "SLOW")
    )
    assert (
        slow_state.consecutive_failures == 1
        and slow_state.last_successful_check is None
    )
    timeout.failure = None
    recovered = asyncio.run(check_for_events(db, sources=[timeout], storage=storage))
    assert recovered.errors == []
    db.refresh(slow_state)
    assert (
        slow_state.consecutive_failures == 0
        and slow_state.last_successful_check is not None
    )


def test_rss_parser_rejects_malformed_response():
    rss = b"<rss><channel><item><title>Board Meeting</title><link>https://example.test/x</link><pubDate>Thu, 24 Sep 2026 10:30:00 +0530</pubDate></item></channel></rss>"
    assert parse_rss(rss)[0].title == "Board Meeting"
    with pytest.raises(MalformedFeedError):
        parse_rss(b"<not-rss/>")
    with pytest.raises(MalformedFeedError):
        parse_rss(b"<!DOCTYPE rss><rss/>")


def test_safe_downloader_validates_url_mime_size_and_failure():
    content = pdf_content()

    def respond(request):
        if request.url.path == "/broken.pdf":
            return httpx.Response(404)
        if request.url.path == "/wrong.pdf":
            return httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"<html/>"
            )
        if request.url.path == "/bad-length.pdf":
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/pdf",
                    "content-length": "invalid",
                },
                content=content,
            )
        return httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=content
        )

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            downloader = SafePdfDownloader(
                {"trusted.example"}, client=client, retries=0
            )
            assert (
                await downloader.download("https://trusted.example/report.pdf")
            ).content == content
            for url in (
                "http://trusted.example/report.pdf",
                "https://evil.example/report.pdf",
                "https://trusted.example@evil.example/report.pdf",
            ):
                with pytest.raises(DocumentDownloadError):
                    await downloader.download(url)
            with pytest.raises(DocumentDownloadError):
                await downloader.download("https://trusted.example/wrong.pdf")
            with pytest.raises(DocumentDownloadError):
                await downloader.download("https://trusted.example/broken.pdf")
            with pytest.raises(DocumentDownloadError):
                await downloader.download("https://trusted.example/bad-length.pdf")
            small = SafePdfDownloader(
                {"trusted.example"}, client=client, retries=0, max_bytes=10
            )
            with pytest.raises(DocumentDownloadError):
                await small.download("https://trusted.example/report.pdf")

    asyncio.run(exercise())


def test_event_api_filters_and_document_download(event_env):
    client, db, _ = event_env
    active_esds(client)
    first = client.post("/api/events/check")
    assert first.status_code == 200 and first.json()["new_events"] == 4
    assert client.post("/api/events/check").json()["new_events"] == 0
    events = client.get("/api/events").json()
    assert len(events) == 4
    assert len(client.get("/api/events?event_type=FINANCIAL_RESULTS").json()) == 1
    assert len(client.get("/api/events?source=FIXTURE").json()) == 4
    assert (
        len(client.get("/api/events?from_date=2026-09-24&to_date=2026-09-24").json())
        == 1
    )
    assert len(client.get("/api/events?limit=2").json()) == 2
    assert len(client.get("/api/events?symbol=ESDS").json()) == 4
    assert len(client.get("/api/companies/esds/events").json()) == 4
    assert (
        client.get("/api/events?from_date=2026-09-25&to_date=2026-09-24").status_code
        == 422
    )
    assert client.get("/api/events/99999").status_code == 404
    result = next(
        event for event in events if event["event_type"] == "FINANCIAL_RESULTS"
    )
    detail = client.get(f"/api/events/{result['id']}").json()
    assert detail["references"][0]["source"] == "FIXTURE"
    document = detail["documents"][0]
    assert document["page_count"] == 2 and document["sha256_hash"]
    assert client.get(document["download_url"]).content.startswith(b"%PDF-")
    assert [
        page["page_number"]
        for page in client.get(f"/api/documents/{document['id']}/pages").json()
    ] == [1, 2]
    source = client.post(
        "/api/companies/esds/sources", json={"source": "FIXTURE", "enabled": False}
    )
    assert source.status_code == 201
    assert next(row for row in client.get("/api/companies/ESDS/sources").json() if row["source"] == "FIXTURE")["enabled"] is False
    assert client.post("/api/events/check").json()["sources_checked"] == 0
    updated = client.put(
        "/api/companies/ESDS/sources/FIXTURE",
        json={"source": "FIXTURE", "enabled": True},
    )
    assert updated.status_code == 200 and updated.json()["enabled"] is True
