"""Manual results use the same financial processor while retaining their origin."""

import asyncio
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.ingestion.monitor import check_for_events
from app.ingestion.sources.base import DownloadedDocument, EventSource, SourceEvent
from app.ingestion.storage import LocalDocumentStorage
from app.main import app
from app.models.event import CompanySource, Document, EventReference
from app.models.financial import (
    FactStatus,
    FinancialFact,
    FinancialPeriod,
    FinancialStatus,
)
from app.ai.analyst.context_builder import build_context
from app.services.financials.extractor import extract_pdf_tables
from tests.test_ai_analyst import FakeProvider
from tests.test_financials import synthetic_pdf
from tests.test_market_valuation import company, market_db


def _pdf() -> bytes:
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "Eternal Limited Q2 results for review")
        return document.tobytes()


def test_upload_manual_pdf_is_bounded_deduplicated_and_reviewable(
    market_db, tmp_path, monkeypatch
):
    company(market_db, symbol="ETERNAL")
    monkeypatch.setattr(settings, "document_storage_path", tmp_path)
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        payload = _pdf()
        url = "/api/companies/ETERNAL/events/upload-pdf"
        params = {
            "title": "Q2 FY27 results",
            "published_on": "2026-09-28",
            "event_type": "FINANCIAL_RESULTS",
            "file_name": "eternal.pdf",
        }
        with TestClient(app) as client:
            uploaded = client.post(
                url,
                params=params,
                content=payload,
                headers={"Content-Type": "application/pdf"},
            )
            assert uploaded.status_code == 200, uploaded.text
            event = uploaded.json()
            assert event["symbol"] == "ETERNAL"
            assert event["data_origin"] == "MANUAL"
            assert event["source"] == "MANUAL_UPLOAD"
            assert event["processing_status"] == "PROCESSED"
            assert event["documents"][0]["page_count"] == 1
            document_id = event["documents"][0]["id"]
            pages = client.get(f"/api/documents/{document_id}/pages")
            assert "Eternal Limited" in pages.json()[0]["text"]
            assert client.get(f"/api/documents/{document_id}/file").content == payload
            repeated = client.post(
                url,
                params=params,
                content=payload,
                headers={"Content-Type": "application/pdf"},
            )
            assert repeated.status_code == 200
            assert repeated.json()["id"] == event["id"]
            review = client.post(f"/api/financials/process/{event['id']}")
            assert (
                review.status_code == 200 and review.json()["status"] == "NEEDS_REVIEW"
            )
            assert (
                client.post(
                    url,
                    params=params,
                    content=b"not a pdf",
                    headers={"Content-Type": "application/pdf"},
                ).status_code
                == 422
            )
            assert (
                client.post(
                    url,
                    params=params,
                    content=payload,
                    headers={"Content-Type": "text/plain"},
                ).status_code
                == 415
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


def _upload(
    client, payload, *, event_type="FINANCIAL_RESULTS", title="Q1 FY2027 results"
):
    response = client.post(
        "/api/companies/ETERNAL/events/upload-pdf",
        params={
            "title": title,
            "published_on": "2026-09-28",
            "event_type": event_type,
            "file_name": "eternal.pdf",
        },
        content=payload,
        headers={"Content-Type": "application/pdf"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_manual_results_process_period_scopes_pages_financials_and_ai(
    market_db, tmp_path, monkeypatch
):
    issuer = company(market_db, symbol="ETERNAL")
    monkeypatch.setattr(settings, "document_storage_path", tmp_path)
    app.dependency_overrides[get_db] = lambda: market_db
    payload = synthetic_pdf(
        scopes=("Consolidated", "Standalone"), company_name=issuer.name
    )
    try:
        with TestClient(app) as client:
            event = _upload(client, payload)
            assert event["data_origin"] == "MANUAL" and not event["source_verified"]
            assert (
                event["documents"][0]["document_classification"] == "FINANCIAL_RESULTS"
            )
            event_id = event["id"]
            assert client.get(f"/api/financials/process/{event_id}").status_code == 404
            first = client.post(f"/api/financials/process/{event_id}")
            assert first.status_code == 200, first.text
            assert first.json()["status"] == "VALIDATED"
            assert first.json()["periods_created"] == 8
            second = client.post(f"/api/financials/process/{event_id}")
            assert second.json()["periods_created"] == 0
            assert market_db.query(FinancialPeriod).count() == 8
            current = [
                period
                for period in client.get("/api/companies/ETERNAL/financials").json()[
                    "periods"
                ]
                if period["period_end"] == "2026-06-30"
            ]
            assert {period["scope"] for period in current} == {
                "CONSOLIDATED",
                "STANDALONE",
            }
            assert all(
                period["fiscal_year"] == "FY2027" and period["quarter"] == 1
                for period in current
            )
            assert all(period["status"] == "VALIDATED" for period in current)
            assert all(
                fact["source_page"] in (1, 2) and fact["document_url"]
                for period in current
                for fact in period["facts"]
            )
            assert all(
                fact["status"] == "VERIFIED_PDF"
                for period in current
                for fact in period["facts"]
            )
            assert (
                client.get(
                    "/api/companies/ETERNAL/financials/latest?scope=STANDALONE"
                ).status_code
                == 200
            )
            period = market_db.get(FinancialPeriod, current[0]["id"])
            context = build_context(market_db, issuer.name, issuer.symbol, period)
            assert (
                context.fact_count > 0
                and json.loads(context.text)["source"]["origin"] == "MANUAL"
            )
            monkeypatch.setattr(
                "app.ai.analyst.service.configured_provider",
                lambda: FakeProvider(error="OLLAMA_UNAVAILABLE"),
            )
            analysis = client.post(
                f"/api/companies/ETERNAL/financials/{period.id}/analyze"
            )
            assert analysis.status_code == 200, analysis.text
            assert analysis.json()["status"] == "VERIFIED_FALLBACK"
            assert len(client.get("/api/companies/ETERNAL/analysis").json()) == 1
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_manual_non_results_and_unsafe_identity_or_period_require_review(
    market_db, tmp_path, monkeypatch
):
    issuer = company(market_db, symbol="ETERNAL")
    monkeypatch.setattr(settings, "document_storage_path", tmp_path)
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            content = synthetic_pdf(company_name=issuer.name)
            other = _upload(client, content, event_type="INVESTOR_PRESENTATION")
            assert (
                client.post(f"/api/financials/process/{other['id']}").status_code == 422
            )
            wrong_company = _upload(
                client, synthetic_pdf(company_name="Another Limited")
            )
            result = client.post(
                f"/api/financials/process/{wrong_company['id']}"
            ).json()
            assert result["status"] == "NEEDS_REVIEW" and result["periods_created"] == 0
            wrong_period = _upload(
                client,
                synthetic_pdf(
                    company_name=issuer.name, scopes=("Consolidated", "Standalone")
                ),
                title="Q2 FY2027 results",
            )
            result = client.post(f"/api/financials/process/{wrong_period['id']}").json()
            assert result["status"] == "NEEDS_REVIEW" and result["periods_created"] == 0
            assert market_db.query(FinancialFact).count() == 0
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_manual_needs_review_keeps_facts_out_of_ai(market_db, tmp_path, monkeypatch):
    issuer = company(market_db, symbol="ETERNAL")
    monkeypatch.setattr(settings, "document_storage_path", tmp_path)
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            event = _upload(
                client,
                synthetic_pdf(
                    company_name=issuer.name,
                    current_overrides={"Revenue from operations": "999.99"},
                ),
            )
            result = client.post(f"/api/financials/process/{event['id']}").json()
            assert result["status"] == "NEEDS_REVIEW"
            period = market_db.scalar(
                select(FinancialPeriod).where(
                    FinancialPeriod.company_id == issuer.id,
                    FinancialPeriod.period_end == date(2026, 6, 30),
                )
            )
            assert period.status == FinancialStatus.NEEDS_REVIEW
            assert all(fact.status == FactStatus.NEEDS_REVIEW for fact in period.facts)
            assert (
                client.post(
                    f"/api/companies/ETERNAL/financials/{period.id}/analyze"
                ).status_code
                == 422
            )
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_later_nse_reference_merges_with_manual_pdf_and_keeps_facts(
    market_db, tmp_path, monkeypatch
):
    issuer = company(market_db, symbol="ETERNAL")
    market_db.add(CompanySource(company_id=issuer.id, source="NSE", enabled=True))
    market_db.commit()
    monkeypatch.setattr(settings, "document_storage_path", tmp_path)
    app.dependency_overrides[get_db] = lambda: market_db
    payload = synthetic_pdf(company_name=issuer.name)
    try:
        with TestClient(app) as client:
            manual = _upload(client, payload)
            assert (
                client.post(f"/api/financials/process/{manual['id']}").json()["status"]
                == "VALIDATED"
            )
            before_facts = market_db.query(FinancialFact).count()

            class Source(EventSource):
                name = "NSE"

                async def fetch_events(self, companies, since):
                    return [
                        SourceEvent(
                            external_symbol="ETERNAL",
                            title="Q1 FY27 Financial Results",
                            published_at=datetime(2026, 9, 28, tzinfo=timezone.utc),
                            source_url="https://official.example/results",
                            document_url="https://official.example/results.pdf",
                            external_id="nse-results",
                        )
                    ]

                async def download_document(self, event):
                    return DownloadedDocument(
                        payload, "application/pdf", "official.pdf"
                    )

            summary = asyncio.run(
                check_for_events(
                    market_db,
                    sources=[Source()],
                    storage=LocalDocumentStorage(tmp_path),
                )
            )
            assert summary.new_events == 0
            assert market_db.query(Document).count() == 1
            assert market_db.query(FinancialPeriod).count() == 4
            assert market_db.query(FinancialFact).count() == before_facts
            reread = client.get(f"/api/events/{manual['id']}").json()
            assert (
                reread["source"] == "MANUAL_UPLOAD"
                and reread["data_origin"] == "MANUAL"
            )
            # A matching hash and an arbitrary LIVE URL do not establish issuer authenticity.
            assert not reread["source_verified"]
            assert {ref["source"] for ref in reread["references"]} == {
                "MANUAL_UPLOAD",
                "NSE",
            }
            assert {
                ref.data_origin.value
                for ref in market_db.scalars(select(EventReference)).all()
            } == {"MANUAL", "LIVE"}
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_manual_other_can_be_explicitly_classified_before_processing(
    market_db, tmp_path, monkeypatch
):
    issuer = company(market_db, symbol="ETERNAL")
    monkeypatch.setattr(settings, "document_storage_path", tmp_path)
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            event = _upload(
                client, synthetic_pdf(company_name=issuer.name), event_type="OTHER"
            )
            assert (
                client.post(f"/api/financials/process/{event['id']}").status_code == 422
            )
            result = client.post(
                f"/api/financials/process/{event['id']}?confirm_results=true"
            )
            assert result.status_code == 200 and result.json()["status"] == "VALIDATED"
            reread = client.get(f"/api/events/{event['id']}").json()
            assert reread["event_type"] == "FINANCIAL_RESULTS"
            assert reread["data_origin"] == "MANUAL" and not reread["source_verified"]
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_confirming_non_results_pdf_does_not_change_classification(
    market_db, tmp_path, monkeypatch
):
    company(market_db, symbol="ETERNAL")
    monkeypatch.setattr(settings, "document_storage_path", tmp_path)
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text(
                (50, 100), "ETERNAL Limited Financial Results overview only"
            )
            content = pdf.tobytes()
        with TestClient(app) as client:
            event = _upload(client, content, event_type="OTHER")
            result = client.post(
                f"/api/financials/process/{event['id']}?confirm_results=true"
            )
            assert result.status_code == 422
            assert (
                client.get(f"/api/events/{event['id']}").json()["event_type"] == "OTHER"
            )
            assert market_db.query(FinancialFact).count() == 0
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_stored_eternal_pdf_layout_when_available():
    path = (
        Path(__file__).parents[1]
        / "data/documents/f8/f837bf8eb99e9f8505c3366ff9bda4469dfde1cd6220488e8aa476f01f1f4232.pdf"
    )
    if not path.exists():
        pytest.skip("Local Eternal upload is not part of the repository")
    rows = extract_pdf_tables(path)
    assert len(rows) == 8
    current = [row for row in rows if row.identity.end == date(2026, 6, 30)]
    assert {row.scope.value for row in current} == {"STANDALONE", "CONSOLIDATED"}
    assert all(
        row.identity.fiscal_year == "FY2027" and row.identity.quarter == 1
        for row in current
    )
    assert all(
        not row.issues and all(fact.page in (25, 30) for fact in row.facts.values())
        for row in current
    )
    assert any(row.issues for row in rows if row.identity.end != date(2026, 6, 30))
