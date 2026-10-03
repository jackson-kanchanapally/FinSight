import asyncio
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import events as events_api
from app.config import settings
from app.database import Base, get_db
from app.ingestion.downloader import SafePdfDownloader
from app.ingestion.monitor import CheckSummary, check_for_events
from app.ingestion.normalizer import classify_event
from app.ingestion.sources.base import (
    DownloadedDocument,
    EventSource,
    MonitoredCompany,
    SourceEvent,
    SourceUnavailableError,
)
from app.ingestion.sources.esds_ir import (
    ESDSIRSource,
    IR_COMPANY_ID,
    IR_URL,
    parse_esds_announcement_rows,
)
from app.ingestion.sources.nse import NSESource, parse_nse_announcements
from app.ingestion.storage import LocalDocumentStorage
from app.main import app
from app.models.event import (
    CompanyEvent,
    DataOrigin,
    Document,
    DocumentPage,
    EventReference,
    MonitoringState,
    PublicationPrecision,
    ReportingBasis,
)
from app.models.event import EventType

FIXTURES = Path(__file__).parent / "fixtures"
NSE_XML = (FIXTURES / "nse_announcements.xml").read_bytes()
ESDS_HTML = (FIXTURES / "esds_ir.html").read_bytes()
RESULTS_URL = (
    "https://www.esds.co.in/assets/images/investor-relations/Announcements/results.pdf"
)


@pytest.fixture
def live_env(tmp_path):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    previous = (
        settings.event_source_mode,
        settings.nse_source_enabled,
        settings.bse_source_enabled,
        settings.company_ir_source_enabled,
        settings.document_storage_path,
    )
    settings.event_source_mode = "live"
    settings.nse_source_enabled = True
    settings.bse_source_enabled = True
    settings.company_ir_source_enabled = True
    settings.document_storage_path = tmp_path

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client, Session() as db:
        assert (
            client.post(
                "/api/companies",
                json={
                    "symbol": "ESDS",
                    "name": "ESDS Software Solution Limited",
                    "exchange": "NSE",
                },
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
        yield client, db, LocalDocumentStorage(tmp_path)
    app.dependency_overrides.clear()
    (
        settings.event_source_mode,
        settings.nse_source_enabled,
        settings.bse_source_enabled,
        settings.company_ir_source_enabled,
        settings.document_storage_path,
    ) = previous
    engine.dispose()


def source_config(client, name, **fields):
    result = client.post("/api/companies/ESDS/sources", json={"source": name, **fields})
    assert result.status_code == 201, result.text


def sample_pdf():
    with pymupdf.open() as pdf:
        first = pdf.new_page()
        first.insert_text((72, 72), "ESDS Software Solution Limited")
        first.insert_text((72, 95), "Date: September 24, 2026")
        first.insert_text((72, 118), "Scrip Symbol: ESDS")
        first.insert_text((72, 141), "Scrip Code: 544898")
        first.insert_text(
            (72, 164), "Unaudited Standalone and Consolidated Financial Results"
        )
        first.insert_text((72, 187), "for the quarter ended June 30, 2026")
        pdf.new_page().insert_text((72, 72), "Page 2 results")
        return pdf.tobytes()


def test_nse_rss_exact_issuer_match_and_timezone():
    target = MonitoredCompany(
        1,
        "ESDS",
        "ESDS Software Solution Limited",
        "ESDS",
        source_company_id="ESDS Software Solution Limited",
    )
    items = parse_nse_announcements(NSE_XML, [target])
    assert len(items) == 2
    assert items[0].external_symbol == "ESDS"
    assert items[0].title == "Financial Results"
    assert items[0].published_at.isoformat() == "2026-09-24T20:25:00+05:30"
    assert items[0].document_url.endswith(".pdf")
    assert items[1].document_url is None
    wrong_name = MonitoredCompany(
        1,
        "ESDS",
        "ESDS Software Solution Limited",
        "ESDS",
        source_company_id="ESDS Software Solution",
    )
    assert parse_nse_announcements(NSE_XML, [wrong_name]) == []
    with pytest.raises(SourceUnavailableError):
        parse_nse_announcements(b"<rss><channel></channel></rss>", [target])


@pytest.mark.parametrize(
    "status,headers", [(403, {}), (429, {"retry-after": "60"}), (500, {})]
)
def test_nse_http_failures_are_explicit(status, headers):
    target = MonitoredCompany(
        1,
        "ESDS",
        "ESDS Software Solution Limited",
        "ESDS",
        source_company_id="ESDS Software Solution Limited",
    )

    async def exercise():
        count = 0

        def respond(request):
            nonlocal count
            count += 1
            return httpx.Response(status, headers=headers)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(SourceUnavailableError):
                await NSESource(client=client).fetch_events([target], None)
        assert count == (3 if status == 500 else 1)

    asyncio.run(exercise())


def test_nse_success_timeout_and_empty_feed():
    target = MonitoredCompany(
        1,
        "ESDS",
        "ESDS Software Solution Limited",
        "ESDS",
        source_company_id="ESDS Software Solution Limited",
    )

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "application/xml"}, content=NSE_XML
                )
            )
        ) as client:
            source = NSESource(client=client)
            assert len(await source.fetch_events([target], None)) == 2
            assert source.received_count == 3
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "application/xml"},
                    content=NSE_XML.replace(b"<item>", b"<unused>").replace(
                        b"</item>", b"</unused>"
                    ),
                )
            )
        ) as client:
            assert await NSESource(client=client).fetch_events([target], None) == []
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout"))
            )
        ) as client:
            with pytest.raises(SourceUnavailableError):
                await NSESource(client=client).fetch_events([target], None)

    asyncio.run(exercise())


def test_esds_ir_parser_rejects_unsafe_or_changed_page():
    assert parse_esds_announcement_rows(ESDS_HTML) == [
        ("Board Meeting Outcome & Q1 FY27 Results", RESULTS_URL)
    ]
    assert (
        classify_event("Board Meeting Outcome & Q1 FY27 Results")
        == EventType.FINANCIAL_RESULTS
    )
    with pytest.raises(SourceUnavailableError):
        parse_esds_announcement_rows(
            ESDS_HTML.replace(
                b"assets/images/investor-relations/Announcements/results.pdf",
                b"https://evil.example/results.pdf",
            )
        )
    with pytest.raises(SourceUnavailableError):
        parse_esds_announcement_rows(b"<html><body>unrelated page</body></html>")


def test_esds_ir_live_adapter_and_pdf_pipeline(live_env):
    client, db, storage = live_env
    source_config(
        client,
        "ESDS_IR",
        external_symbol="ESDS",
        source_company_id=IR_COMPANY_ID,
        ir_url=IR_URL,
    )
    pdf = sample_pdf()
    calls = {"page": 0, "pdf": 0}

    def respond(request):
        if request.url.path == "/investor":
            calls["page"] += 1
            return httpx.Response(
                200, headers={"content-type": "text/html"}, content=ESDS_HTML
            )
        calls["pdf"] += 1
        return httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=pdf
        )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond)
        ) as http_client:
            source = ESDSIRSource(
                client=http_client,
                downloader=SafePdfDownloader(
                    {"www.esds.co.in"}, client=http_client, retries=0
                ),
            )
            first = await check_for_events(db, sources=[source], storage=storage)
            second = await check_for_events(db, sources=[source], storage=storage)
            return first, second

    first, second = asyncio.run(run())
    assert first.new_events == 1 and first.documents_downloaded == 1
    assert second.new_events == 0 and second.duplicates == 1
    assert calls == {"page": 2, "pdf": 1}
    event = db.scalar(select(CompanyEvent))
    assert event.data_origin == DataOrigin.LIVE
    assert event.event_type == EventType.FINANCIAL_RESULTS
    assert event.publication_precision == PublicationPrecision.DATE
    assert event.period_end_date.isoformat() == "2026-06-30"
    assert (event.fiscal_year, event.quarter, event.reporting_basis) == (
        "FY2027",
        1,
        ReportingBasis.BOTH,
    )
    assert db.scalar(select(Document)).sha256_hash
    assert [
        page.page_number
        for page in db.scalars(select(DocumentPage).order_by(DocumentPage.page_number))
    ] == [1, 2]
    detail = client.get(f"/api/events/{event.id}").json()
    assert detail["data_origin"] == "LIVE" and detail["publication_precision"] == "DATE"
    assert detail["documents"][0]["page_count"] == 2


class OfflineSource(EventSource):
    def __init__(self, name, content, *, origin=DataOrigin.LIVE, failure=None):
        self.name = name
        self.origin = origin
        self.content = content
        self.failure = failure

    async def fetch_events(self, companies, since):
        if self.failure:
            raise self.failure
        return [
            SourceEvent(
                external_symbol="ESDS",
                title="Financial Results",
                description=None,
                published_at=datetime(2026, 9, 24, 20, 25, tzinfo=timezone.utc),
                external_id=f"{self.name}-results",
                source_url=f"https://{self.name.lower()}.example/results",
                document_url=f"https://{self.name.lower()}.example/results.pdf",
            )
        ]

    async def download_document(self, event):
        return DownloadedDocument(self.content, "application/pdf", "results.pdf")


def test_cross_source_pdf_dedup_and_origin_isolation(live_env):
    client, db, storage = live_env
    source_config(client, "NSE", source_company_id="ESDS Software Solution Limited")
    source_config(client, "BSE", source_company_id="544898")
    content = sample_pdf()
    result = asyncio.run(
        check_for_events(
            db,
            sources=[
                OfflineSource("NSE", content),
                OfflineSource("BSE", content),
                OfflineSource("FIXTURE", content, origin=DataOrigin.FIXTURE),
            ],
            storage=storage,
        )
    )
    assert result.new_events == 2 and result.duplicates == 1
    assert db.query(CompanyEvent).count() == 2
    assert db.query(EventReference).count() == 3
    assert {event.data_origin for event in db.scalars(select(CompanyEvent))} == {
        DataOrigin.LIVE,
        DataOrigin.FIXTURE,
    }


def test_source_failure_state_and_status_api(live_env):
    client, db, storage = live_env
    source_config(client, "NSE", source_company_id="ESDS Software Solution Limited")
    source_config(client, "BSE", source_company_id="544898")
    result = asyncio.run(
        check_for_events(
            db,
            sources=[
                OfflineSource(
                    "NSE", sample_pdf(), failure=SourceUnavailableError("denied")
                ),
                OfflineSource("BSE", sample_pdf()),
            ],
            storage=storage,
        )
    )
    assert result.sources["NSE"].status == "error"
    assert result.sources["BSE"].status == "success"
    assert result.new_events == 1
    state = db.scalar(select(MonitoringState).where(MonitoringState.source == "NSE"))
    assert state.last_attempted_check is not None and state.consecutive_failures == 1
    status = {
        row["source"]: row for row in client.get("/api/events/sources/status").json()
    }
    assert status["NSE"]["status"] == "error" and status["BSE"]["status"] == "healthy"


def test_live_source_mapping_can_monitor_registered_company_without_open_shares(
    live_env,
):
    client, db, storage = live_env
    source_config(client, "NSE", source_company_id="ESDS Software Solution Limited")
    sold = client.post(
        "/api/transactions",
        json={
            "symbol": "ESDS",
            "transaction_type": "SELL",
            "quantity": "1",
            "price": "100",
            "transaction_date": "2026-09-02",
        },
    )
    assert sold.status_code == 201
    result = asyncio.run(
        check_for_events(
            db, sources=[OfflineSource("NSE", sample_pdf())], storage=storage
        )
    )
    assert result.new_events == 1


def test_bootstrap_is_bounded_and_rate_limited(live_env, monkeypatch):
    client, db, _ = live_env
    source_config(
        client,
        "ESDS_IR",
        external_symbol="ESDS",
        source_company_id=IR_COMPANY_ID,
        ir_url=IR_URL,
    )

    async def no_network(*args, **kwargs):
        return CheckSummary(sources_checked=1, new_events=1)

    monkeypatch.setattr(events_api, "check_for_events", no_network)
    events_api._last_bootstrap.clear()
    assert (
        client.post(
            "/api/events/bootstrap", json={"symbol": "ESDS", "days": 91}
        ).status_code
        == 422
    )
    first = client.post("/api/events/bootstrap", json={"symbol": "ESDS", "days": 90})
    assert first.status_code == 200 and first.json()["new_events"] == 1
    assert (
        client.post(
            "/api/events/bootstrap", json={"symbol": "ESDS", "days": 90}
        ).status_code
        == 429
    )
    events_api._last_bootstrap.clear()
