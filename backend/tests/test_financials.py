import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.ingestion.downloader import sha256_bytes
from app.ingestion.monitor import check_for_events
from app.ingestion.sources.base import DownloadedDocument, EventSource, SourceEvent
from app.ingestion.storage import LocalDocumentStorage
from app.main import app
from app.models import Company
from app.models.event import CompanyEvent, CompanySource, DataOrigin, Document, DocumentPage, EventType, ProcessingStatus
from app.models.financial import (FinancialFact, FinancialMetric, FinancialPeriod, FinancialProcessingRun,
                                  FinancialScope, FinancialStatus, MetricCode, OriginalUnit, PeriodType)
from app.services.financials.calculator import growth, margin, margin_change_bps, normalize_currency
from app.services.financials.extractor import extract_pdf_tables
from app.services.financials.period import identify_period, previous_quarter_end, previous_year_end
from app.services.financials.service import process_financial_event


@pytest.mark.parametrize("end,kind,quarter,year,start", [
    (date(2026, 6, 30), PeriodType.QUARTERLY, 1, "FY2027", date(2026, 4, 1)),
    (date(2026, 9, 30), PeriodType.QUARTERLY, 2, "FY2027", date(2026, 7, 1)),
    (date(2026, 12, 31), PeriodType.QUARTERLY, 3, "FY2027", date(2026, 10, 1)),
    (date(2027, 3, 31), PeriodType.QUARTERLY, 4, "FY2027", date(2027, 1, 1)),
    (date(2027, 3, 31), PeriodType.ANNUAL, None, "FY2027", date(2026, 4, 1)),
])
def test_indian_fiscal_period(end, kind, quarter, year, start):
    identity = identify_period(end, kind)
    assert (identity.quarter, identity.fiscal_year, identity.start) == (quarter, year, start)
    if quarter == 1:
        assert previous_quarter_end(identity) == date(2026, 3, 31)
        assert previous_year_end(identity) == date(2025, 6, 30)
    if kind == PeriodType.ANNUAL:
        assert previous_quarter_end(identity) is None
    with pytest.raises(ValueError):
        identify_period(date(2026, 7, 1), PeriodType.QUARTERLY)


def test_units_and_calculations():
    assert normalize_currency(Decimal("13366.24"), OriginalUnit.INR_LAKH) == Decimal("133.6624")
    assert normalize_currency(Decimal("1336.57"), OriginalUnit.INR_MILLION) == Decimal("133.657")
    assert growth(Decimal("130"), Decimal("100")) == Decimal("30")
    assert growth(Decimal("130"), Decimal("120")).quantize(Decimal(".0001")) == Decimal("8.3333")
    assert growth(Decimal("-20"), Decimal("-10")) == Decimal("-100")
    assert growth(Decimal("1"), Decimal("0")) is None
    assert growth(None, Decimal("10")) is None
    assert margin(Decimal("5"), Decimal("0")) is None
    assert margin_change_bps(Decimal("41.9"), Decimal("39.2")) == Decimal("270.0")


VALUES = {
    "Revenue from operations": ["1336.57", "1675.00", "1245.87", "4722.10"],
    "Other income": ["15.14", "25.44", "11.34", "84.42"],
    "Total income": ["1351.71", "1700.44", "1257.21", "4806.52"],
    "Finance costs": ["25.69", "27.19", "35.77", "117.90"],
    "Depreciation and amortisation expenses": ["184.75", "170.79", "153.36", "634.63"],
    "Total expenses": ["987.04", "847.20", "910.40", "3132.29"],
    "Profit before tax": ["364.67", "853.24", "346.81", "1674.23"],
    "Current tax": ["106.59", "150.58", "81.92", "469.03"],
    "Deferred tax": ["(34.69)", "25.90", "8.04", "(3.03)"],
    "Total tax expense": ["71.90", "176.48", "89.96", "466.00"],
    "Profit for the period/year": ["292.77", "676.76", "256.85", "1208.23"],
    "Basic (in INR)": ["2.92", "6.71", "2.56", "11.98"],
    "Diluted (in INR)": ["2.85", "6.59", "2.51", "11.76"],
}
ROW_Y = {
    "Revenue from operations": 193, "Other income": 203, "Total income": 213,
    "Finance costs": 243, "Depreciation and amortisation expenses": 253,
    "Total expenses": 273, "Profit before tax": 293, "Current tax": 323,
    "Deferred tax": 333, "Total tax expense": 353,
    "Profit for the period/year": 373, "Basic (in INR)": 573, "Diluted (in INR)": 583,
}


def synthetic_pdf(*, scopes=("Consolidated",), bad_header=False, omit_eps=False,
                  omit_finance=False, adjusted_ebitda=False,
                  current_overrides: dict[str, str] | None = None) -> bytes:
    with pymupdf.open() as pdf:
        for scope in scopes:
            page = pdf.new_page(width=594, height=840)
            page.insert_text((50, 115),
                f"Unaudited {scope} Financial Results for the quarter ended June 30, 2026")
            page.insert_text((340, 140), "(All amounts are in Rupees million, unless otherwise stated)")
            page.insert_text((355, 150), "QUARTER ENDED")
            page.insert_text((518, 150), "YEAR ENDED")
            if not bad_header:
                for x, label in zip((289, 362, 443, 515),
                                    ("June 30, 2026", "March 31, 2026", "June 30, 2025", "March 31, 2026")):
                    page.insert_text((x, 165), label, fontsize=8)
            columns = (310, 383, 462, 535)
            for label, values in VALUES.items():
                if omit_eps and label.startswith(("Basic", "Diluted")):
                    continue
                if omit_finance and label == "Finance costs":
                    continue
                y = ROW_Y[label]
                page.insert_text((42, y), label, fontsize=8)
                for index, (x, value) in enumerate(zip(columns, values)):
                    if index == 0 and current_overrides and label in current_overrides:
                        value = current_overrides[label]
                    page.insert_text((x, y), value, fontsize=8)
            if adjusted_ebitda:
                page.insert_text((42, 283), "Adjusted EBITDA", fontsize=8)
                page.insert_text((310, 283), "999.99", fontsize=8)
            page.insert_text((42, 393), "Other comprehensive income", fontsize=8)
        return pdf.tobytes()


def test_table_extraction_period_columns_scopes_and_missing_values(tmp_path):
    pdf = tmp_path / "results.pdf"
    pdf.write_bytes(synthetic_pdf(scopes=("Consolidated", "Standalone"), omit_eps=True,
                                  adjusted_ebitda=True))
    rows = extract_pdf_tables(pdf)
    assert len(rows) == 8
    current = next(row for row in rows if row.scope == FinancialScope.CONSOLIDATED
                   and row.identity.end == date(2026, 6, 30))
    assert not current.issues
    assert current.facts[MetricCode.REVENUE_FROM_OPERATIONS].original_value == Decimal("1336.57")
    assert current.facts[MetricCode.PAT].original_value == Decimal("292.77")
    assert MetricCode.BASIC_EPS not in current.facts
    assert MetricCode.EBITDA not in current.facts
    assert MetricCode.ADJUSTED_EBITDA not in current.facts
    annual = next(row for row in rows if row.identity.period_type == PeriodType.ANNUAL
                  and row.scope == FinancialScope.CONSOLIDATED)
    assert annual.identity.fiscal_year == "FY2026"
    assert annual.facts[MetricCode.REVENUE_FROM_OPERATIONS].original_value == Decimal("4722.10")
    pdf.write_bytes(synthetic_pdf(bad_header=True))
    assert extract_pdf_tables(pdf) == []


def test_missing_finance_prevents_calculated_ebitda(tmp_path):
    pdf = tmp_path / "missing.pdf"
    pdf.write_bytes(synthetic_pdf(omit_finance=True))
    current = extract_pdf_tables(pdf)[0]
    assert not current.issues
    assert MetricCode.FINANCE_COST not in current.facts


def test_inconsistent_financial_table_is_flagged_for_review(tmp_path):
    pdf = tmp_path / "inconsistent.pdf"
    pdf.write_bytes(synthetic_pdf(current_overrides={"Total income": "9999.99"}))
    current = extract_pdf_tables(pdf)[0]
    assert current.issues
    assert any("TOTAL_INCOME does not reconcile" in issue for issue in current.issues)


@pytest.fixture
def financial_env(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    storage = LocalDocumentStorage(tmp_path)
    content = synthetic_pdf(scopes=("Consolidated", "Standalone"))
    digest = sha256_bytes(content)
    location = storage.store(content, digest)
    with Session() as db:
        company = Company(symbol="ESDS", name="ESDS Software Solution Limited", exchange="NSE")
        db.add(company)
        db.flush()
        event = CompanyEvent(company_id=company.id, event_type=EventType.FINANCIAL_RESULTS,
            title="Q1 FY27 Financial Results", published_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
            source="ESDS_IR", source_url="https://www.esds.co.in/investor",
            fingerprint="financial-test", data_origin=DataOrigin.LIVE,
            processing_status=ProcessingStatus.PROCESSED)
        db.add(event)
        db.flush()
        document = Document(company_id=company.id, event_id=event.id, source="ESDS_IR",
            source_url=event.source_url, document_url="https://www.esds.co.in/results.pdf",
            local_path=location, sha256_hash=digest, processing_status=ProcessingStatus.PROCESSED)
        db.add(document)
        db.flush()
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            for number, page in enumerate(pdf, start=1):
                db.add(DocumentPage(document_id=document.id, page_number=number, text=page.get_text()))
        db.commit()
        event_id = event.id
    def override_db():
        with Session() as db:
            yield db
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client, Session() as db:
        yield client, db, storage, event_id
    app.dependency_overrides.clear()
    engine.dispose()


def test_process_is_idempotent_and_api_provenance(financial_env):
    client, db, storage, event_id = financial_env
    first = process_financial_event(db, event_id, storage=storage)
    second = process_financial_event(db, event_id, storage=storage)
    assert first["periods_created"] == 8 and second["periods_created"] == 0
    assert db.query(FinancialPeriod).count() == 8
    latest = client.get("/api/companies/ESDS/financials/latest")
    assert latest.status_code == 200
    current = latest.json()
    assert current["scope"] == "CONSOLIDATED"
    assert current["period_end"] == "2026-06-30"
    revenue = next(fact for fact in current["facts"] if fact["metric_code"] == "REVENUE_FROM_OPERATIONS")
    assert revenue["value"] == "133.657000" and revenue["original_unit"] == "INR_MILLION"
    assert revenue["source_page"] == 1 and revenue["source_column_date"] == "2026-06-30"
    metrics = {item["metric_code"]: item for item in current["metrics"]}
    assert Decimal(metrics["REVENUE_YOY"]["value"]).quantize(Decimal(".01")) == Decimal("7.28")
    assert Decimal(metrics["REVENUE_QOQ"]["value"]).quantize(Decimal(".01")) == Decimal("-20.20")
    assert Decimal(metrics["PAT_YOY"]["value"]).quantize(Decimal(".01")) == Decimal("13.98")
    assert len(metrics["REVENUE_YOY"]["inputs"]) == 2
    assert {fact["source_column_date"] for fact in metrics["REVENUE_YOY"]["inputs"]} == {
        "2026-06-30", "2025-06-30"}
    assert client.get(f"/api/companies/ESDS/financials/{current['id']}").status_code == 200
    assert client.get("/api/companies/ESDS/financials/latest?scope=STANDALONE").status_code == 200
    assert client.get("/api/companies/UNKNOWN/financials").status_code == 404
    assert db.query(FinancialFact).count() > 0 and db.query(FinancialMetric).count() > 0


def test_wrong_document_hash_records_failure(financial_env):
    client, db, storage, event_id = financial_env
    document = db.scalar(select(Document))
    document.sha256_hash = "0" * 64
    db.commit()
    response = client.post(f"/api/financials/process/{event_id}")
    assert response.status_code == 422
    status = client.get(f"/api/financials/process/{event_id}")
    assert status.status_code == 200 and status.json()["status"] == "FAILED"


def test_same_scope_is_required_for_comparison(financial_env):
    _, db, storage, event_id = financial_env
    process_financial_event(db, event_id, storage=storage)
    periods = db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.period_end == date(2026, 6, 30))).all()
    assert {period.scope for period in periods} == {FinancialScope.CONSOLIDATED, FinancialScope.STANDALONE}
    for period in periods:
        yoy = db.scalar(select(FinancialMetric).where(FinancialMetric.financial_period_id == period.id,
                                                       FinancialMetric.metric_code == MetricCode.REVENUE_YOY))
        assert yoy is not None
        previous = db.get(FinancialPeriod, yoy.comparison_period_id)
        assert previous.scope == period.scope


def test_negative_pat_zero_revenue_and_missing_eps(financial_env):
    _, db, storage, event_id = financial_env
    overrides = {
        "Revenue from operations": "0.00", "Other income": "0.00",
        "Total income": "0.00", "Total expenses": "100.00",
        "Profit before tax": "(100.00)", "Current tax": "0.00",
        "Deferred tax": "0.00", "Total tax expense": "0.00",
        "Profit for the period/year": "(100.00)",
    }
    content = synthetic_pdf(omit_eps=True, current_overrides=overrides)
    document = db.scalar(select(Document))
    document.sha256_hash = sha256_bytes(content)
    document.local_path = storage.store(content, document.sha256_hash)
    db.commit()
    process_financial_event(db, event_id, storage=storage)
    period = db.scalar(select(FinancialPeriod).where(FinancialPeriod.period_end == date(2026, 6, 30)))
    assert period.status == FinancialStatus.VALIDATED
    pat = db.scalar(select(FinancialFact).where(FinancialFact.financial_period_id == period.id,
                                                FinancialFact.metric_code == MetricCode.PAT))
    assert pat.value == Decimal("-10")
    codes = {metric.metric_code for metric in db.scalars(select(FinancialMetric).where(
        FinancialMetric.financial_period_id == period.id))}
    assert MetricCode.PAT_MARGIN not in codes and MetricCode.EBITDA_MARGIN not in codes
    assert db.scalar(select(FinancialFact).where(FinancialFact.financial_period_id == period.id,
                                                FinancialFact.metric_code == MetricCode.BASIC_EPS)) is None


def test_automatic_processing_does_not_fail_event_ingestion(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    storage = LocalDocumentStorage(tmp_path)
    with Session() as db:
        company = Company(symbol="ESDS", name="ESDS Software Solution Limited", exchange="NSE")
        db.add(company)
        db.flush()
        db.add(CompanySource(company_id=company.id, source="NSE",
            source_company_id="ESDS Software Solution Limited", enabled=True))
        db.commit()

        class Source(EventSource):
            name = "NSE"

            async def fetch_events(self, companies, since):
                return [SourceEvent(external_symbol="ESDS", title="Q1 FY27 Financial Results",
                    published_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
                    source_url=f"https://official.example/{key}",
                    document_url=f"https://official.example/{key}.pdf", external_id=key)
                    for key in ("valid", "unreadable")]

            async def download_document(self, event):
                if event.external_id == "valid":
                    content = synthetic_pdf()
                else:
                    with pymupdf.open() as pdf:
                        pdf.new_page().insert_text((50, 50), "No statement table")
                        content = pdf.tobytes()
                return DownloadedDocument(content, "application/pdf", "result.pdf")

        summary = asyncio.run(check_for_events(db, sources=[Source()], storage=storage))
        assert summary.new_events == 2 and summary.documents_downloaded == 2
        assert db.query(CompanyEvent).count() == 2
        assert db.query(FinancialPeriod).count() == 4
        runs = {run.status for run in db.scalars(select(FinancialProcessingRun))}
        assert runs == {FinancialStatus.VALIDATED, FinancialStatus.NEEDS_REVIEW}
    engine.dispose()
