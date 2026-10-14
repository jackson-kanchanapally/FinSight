"""Monitoring exercises real evidence storage, hooks and immutable snapshots."""
import copy
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
from app.main import app
from app.models.commentary import ManagementStatement
from app.models.event import EventType, DataOrigin
from app.models.financial import FinancialFact, FinancialFactCorrection, FinancialStatus, FinancialScope, FinancialUnit, FactMethod, FactStatus, MetricCode, PeriodType
from app.models.thesis import ThesisEvaluationRun
from app.services.financials.service import calculate_company_metrics
from app.services.thesis import save_pillar, thesis_view
from app.services.thesis_evaluation import run_view
from app.services.thesis_monitoring import monitor_company, refresh_monitoring, compare_assessments
from tests.test_phase71 import issuer, result_period, event_doc
from tests.test_phase72 import call
from tests.test_phase8 import pillar, requirement

DAY = date(2026, 8, 1)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def fact(db, period, code, value):
    row = FinancialFact(financial_period_id=period.id, metric_code=code, value=Decimal(value),
        unit=FinancialUnit.INR_CRORE, source_document_id=period.source_document_id,
        source_page=3, source_label=code.value, source_column_date=period.period_end,
        extraction_method=FactMethod.PDF_TABLE, status=FactStatus.VERIFIED_PDF)
    db.add(row); db.flush()
    return row


def setup(db, *, history=False):
    company = issuer(db, "MONITOR")
    period = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    revenue = fact(db, period, MetricCode.REVENUE_FROM_OPERATIONS, "120")
    fact(db, period, MetricCode.PAT, "12")
    if history:
        old = result_period(db, company, date(2025, 6, 30), "2025-07-20", fy="FY2026")
        fact(db, old, MetricCode.REVENUE_FROM_OPERATIONS, "100")
        fact(db, old, MetricCode.PAT, "15")
    calculate_company_metrics(db, company.id)
    save_pillar(db, company.id, pillar(expectations=[requirement(period, "100")]), today=DAY)
    db.commit()
    return company, period, revenue


def monitor(db, company, today=DAY):
    result = monitor_company(db, company.id, today=today)
    db.commit()
    return result[0]


def rows(db):
    return db.scalar(select(func.count()).select_from(ThesisEvaluationRun))


def test_first_assessment_needs_no_saved_analysis_or_ai(db, monkeypatch):
    company, period, _ = setup(db)
    monkeypatch.setattr("app.ai.analyst.service.configured_provider", lambda: pytest.fail("Automatic monitoring called AI"))
    result = monitor(db, company)
    assert result["analysis_id"] is None and result["previous_run_id"] is None
    assert result["assessment"]["first_assessment"]
    assert result["impacts"][0]["new_status"] == "STRENGTHENING"
    assert result["assessment"]["current_state"][0]["status"] == "STRENGTHENING"
    assert result["assessment"]["model"] is None
    assert all(c["classification"] == "INSUFFICIENT_EVIDENCE" for c in result["assessment"]["changes"] if c["category"] == "FINANCIAL")
    assert any(c["category"] == "EXECUTION" and c["classification"] == "IMPROVING" for c in result["assessment"]["changes"])
    assert result["assessment"]["unresolved_questions"]


def test_yoy_conflicts_and_verified_margin_changes(db):
    company, _, _ = setup(db, history=True)
    result = monitor(db, company)
    changes = {r["key"]: r for r in result["assessment"]["changes"]}
    assert changes["REVENUE_FROM_OPERATIONS:QUARTERLY"]["classification"] == "IMPROVING"
    assert changes["PAT:QUARTERLY"]["classification"] == "DETERIORATING"
    assert changes["PAT_MARGIN:QUARTERLY"]["delta"] == "-5.000000"
    assert result["assessment"]["conflicting_evidence"]
    assert result["assessment"]["confidence"] != "HIGH"
    assert result["assessment"]["supporting_evidence"] and result["assessment"]["contradicting_evidence"]
    assert all(k in result["evidence"] for r in changes.values() for k in r["evidence_ids"])


def test_idempotence_across_days_and_recalculated_metrics(db):
    company, _, _ = setup(db, history=True)
    first = monitor(db, company)
    second = monitor(db, company, date(2026, 8, 2))
    assert first["run_id"] == second["run_id"]
    calculate_company_metrics(db, company.id)
    assert rows(db) == 1


def test_new_quarter_is_automatic_and_sequential_decline_is_not_structural(db):
    company, _, _ = setup(db)
    initial = monitor(db, company)
    new = result_period(db, company, date(2026, 9, 30), "2026-10-05", quarter=2)
    fact(db, new, MetricCode.REVENUE_FROM_OPERATIONS, "80")
    fact(db, new, MetricCode.PAT, "8")
    db.commit()
    calculate_company_metrics(db, company.id)
    latest = db.scalar(select(ThesisEvaluationRun).order_by(ThesisEvaluationRun.id.desc()))
    assert latest.previous_run_id == initial["run_id"]
    revenue = next(c for c in latest.assessment["changes"] if c["key"] == "REVENUE_FROM_OPERATIONS:QUARTERLY")
    assert revenue["classification"] == "INSUFFICIENT_EVIDENCE"
    assert "Sequential" in revenue["reason"]
    assert run_view(db, latest)["impacts"][0]["new_status"] != "BROKEN"


def test_revision_retains_old_values_and_compares_any_stored_pair(db):
    company, period, revenue = setup(db)
    first = monitor(db, company)
    original = copy.deepcopy(first)
    db.add(FinancialFactCorrection(fact_id=revenue.id, previous_value=revenue.value,
        corrected_value=Decimal("90"), reason="Verified corrected result"))
    revenue.value = Decimal("90")
    db.commit()
    calculate_company_metrics(db, company.id)
    second = monitor(db, company)
    assert second["run_id"] != first["run_id"] and rows(db) == 2
    revised = next(c for c in second["assessment"]["changes"] if c["key"].startswith("REVENUE_FROM_OPERATIONS:"))
    assert revised["corrected"] and revised["classification"] == "DETERIORATING"
    assert second["impacts"][0]["new_status"] == "WEAKENING"
    assert second["assessment"]["evidence_revisions"][f"F{revenue.id}"] != first["assessment"]["evidence_revisions"][f"F{revenue.id}"]
    assert run_view(db, db.get(ThesisEvaluationRun, first["run_id"])) == original
    comparison = compare_assessments(db, company.id, "CONSOLIDATED", first["run_id"], second["run_id"])
    assert all(k in comparison["evidence"] for c in comparison["changes"] for k in c["evidence_ids"])
    assert comparison["from"]["evidence"][f"F{revenue.id}"]["value"] == "120.000000"
    historical_margin = next(r for k, r in comparison["evidence"].items() if k.startswith(f"P{first['run_id']}:M") and r["metric_code"] == "PAT_MARGIN")
    assert f"P{first['run_id']}:F{revenue.id}" in historical_margin["input_evidence_ids"]
    assert comparison["evidence"][f"P{first['run_id']}:F{revenue.id}"]["value"] == "120.000000"
    reverse = compare_assessments(db, company.id, "CONSOLIDATED", second["run_id"], first["run_id"])
    assert next(c for c in reverse["changes"] if c["key"].startswith("REVENUE_FROM_OPERATIONS:"))["classification"] == "IMPROVING"


@pytest.mark.parametrize("mode", ["orm", "sql_update", "sql_delete"])
def test_assessment_immutability(db, mode):
    company, _, _ = setup(db)
    result = monitor(db, company)
    with pytest.raises((ValueError, DBAPIError), match="THESIS_HISTORY_IS_IMMUTABLE"):
        if mode == "orm":
            db.get(ThesisEvaluationRun, result["run_id"]).assessment = {"summary": "rewrite"}
            db.flush()
        else:
            db.execute(text("UPDATE thesis_evaluation_runs SET assessment='{}'" if mode == "sql_update" else "DELETE FROM thesis_evaluation_runs"))
    db.rollback()
    assert db.get(ThesisEvaluationRun, result["run_id"]).assessment["first_assessment"]


def test_incomplete_results_and_unverified_statements_cannot_trigger(db):
    company, _, _ = setup(db)
    first = monitor(db, company)
    new = result_period(db, company, date(2026, 9, 30), "2026-10-05", quarter=2)
    new.status = FinancialStatus.NEEDS_REVIEW
    fact(db, new, MetricCode.REVENUE_FROM_OPERATIONS, "1")
    db.commit()
    calculate_company_metrics(db, company.id)
    assert rows(db) == 1
    assert monitor(db, company, date(2026, 10, 12))["run_id"] == first["run_id"]


def test_guidance_upgrade_downgrade_withdrawal_and_risk_disclosure(db):
    company, period, _ = setup(db)
    monitor(db, company)
    call(db, company, period, "CEO: We target PAT margins of 15% to 20% for FY27.")
    assert rows(db) == 2
    call(db, company, period, "CEO: We target PAT margins of 20% to 25% for FY27.", published="2026-07-26")
    latest = db.scalar(select(ThesisEvaluationRun).order_by(ThesisEvaluationRun.id.desc()))
    assert any(c["category"] == "GUIDANCE" and c["classification"] == "IMPROVING" and c["basis"] == "MANAGEMENT_CLAIM" for c in latest.assessment["changes"])
    call(db, company, period, "CEO: We target PAT margins of 10% to 12% for FY27.", published="2026-07-27")
    latest = db.scalar(select(ThesisEvaluationRun).order_by(ThesisEvaluationRun.id.desc()))
    assert any(c["category"] == "GUIDANCE" and c["classification"] == "DETERIORATING" for c in latest.assessment["changes"])
    call(db, company, period, "CEO: We target PAT margins of 8% to 20% for FY27.", published="2026-07-28")
    latest = db.scalar(select(ThesisEvaluationRun).order_by(ThesisEvaluationRun.id.desc()))
    assert any(c["category"] == "GUIDANCE" and c["classification"] == "INSUFFICIENT_EVIDENCE" and "opposite directions" in c["reason"] for c in latest.assessment["changes"])
    call(db, company, period, "CEO: We withdraw our previous PAT margin guidance for FY27.\nCEO: We face a risk from customer concentration in the existing business.", published="2026-07-29")
    latest = db.scalar(select(ThesisEvaluationRun).order_by(ThesisEvaluationRun.id.desc()))
    assert any(c["category"] == "EXECUTION" and "withdrawn" in c["reason"] for c in latest.assessment["changes"])
    assert any(c["category"] == "RISK" for c in latest.assessment["changes"])
    assert latest.assessment["current_state"][0]["status"] != "BROKEN"


def test_corporate_announcement_is_source_linked_and_manual_upload_is_excluded(db):
    company, _, _ = setup(db)
    monitor(db, company)
    event_doc(db, company, EventType.LITIGATION, "Litigation disclosure", "2026-07-26", text="The company has received a legal notice; the outcome is uncertain.")
    event_doc(db, company, EventType.ORDER_WIN, "Unverified order", "2026-07-26", origin=DataOrigin.MANUAL, text="An unverified order was announced.")
    db.commit()
    result = monitor(db, company)
    disclosures = [r for r in result["evidence"].values() if r["kind"] == "CORPORATE_DISCLOSURE"]
    assert len(disclosures) == 1 and disclosures[0]["document_url"].endswith("#page=3")
    assert any(c["category"] == "CORPORATE_EVENT" and c["classification"] == "INSUFFICIENT_EVIDENCE" for c in result["assessment"]["changes"])


def test_ordinary_fluctuations_are_unchanged(db):
    company, period, revenue = setup(db, history=True)
    revenue.value = Decimal("102")
    pat = db.scalar(select(FinancialFact).where(FinancialFact.financial_period_id == period.id, FinancialFact.metric_code == MetricCode.PAT))
    pat.value = Decimal("15.3")
    db.commit()
    result = monitor(db, company)
    assert next(c for c in result["assessment"]["changes"] if c["key"].startswith("REVENUE_FROM_OPERATIONS:"))["classification"] == "UNCHANGED"
    assert next(c for c in result["assessment"]["changes"] if c["key"].startswith("PAT:"))["classification"] == "UNCHANGED"


def test_monitoring_failure_preserves_ingestion_and_can_retry(db, monkeypatch):
    company, _, revenue = setup(db)
    def fail(*args, **kwargs):
        raise RuntimeError("monitor unavailable")
    with monkeypatch.context() as m:
        m.setattr("app.services.thesis_monitoring.collect_evidence", fail)
        revenue.value = Decimal("125")
        db.commit()
        assert refresh_monitoring(db, company.id) == []
        assert db.get(FinancialFact, revenue.id).value == Decimal("125")
    assert refresh_monitoring(db, company.id)[0]["run_id"]


def test_concurrent_requests_create_one_assessment(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'monitor.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company, _, _ = setup(session)
        company_id = company.id
    barrier = Barrier(2)
    def run():
        with Session(engine) as session:
            barrier.wait(timeout=5)
            result = monitor_company(session, company_id, today=DAY)
            session.commit()
            return result[0]["run_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run), pool.submit(run)]
        ids = [f.result(timeout=15) for f in futures]
    with Session(engine) as session:
        assert ids[0] == ids[1] and rows(session) == 1
    engine.dispose()


def test_api_history_compare_is_read_only_and_company_scope_isolated(db):
    company, _, _ = setup(db)
    initial = monitor(db, company)
    other = issuer(db, "OTHERMONITOR"); db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            base = "/api/companies/MONITOR/thesis/assessments"
            detail = client.get(f"{base}/{initial['run_id']}")
            assert detail.status_code == 200 and detail.json()["assessment"]["first_assessment"]
            assert client.get(f"{base}/{initial['run_id']}?scope=STANDALONE").status_code == 404
            assert client.get(f"/api/companies/OTHERMONITOR/thesis/assessments/{initial['run_id']}").status_code == 404
            comparison = client.get(f"{base}/compare?from_id={initial['run_id']}&to_id={initial['run_id']}")
            assert comparison.status_code == 200
            assert not any(c["material"] for c in comparison.json()["changes"])
            assert client.get("/api/companies/MONITOR/thesis?as_of_date=2026-07-01").json()["monitoring"]["latest"] is None
            assert rows(db) == 1
    finally:
        app.dependency_overrides.clear()


def test_annual_results_are_monitored_with_separate_comparison_basis(db):
    company = issuer(db, "ANNUAL")
    old = result_period(db, company, date(2025, 3, 31), "2025-05-01", kind=PeriodType.ANNUAL, quarter=None, fy="FY2025")
    current = result_period(db, company, date(2026, 3, 31), "2026-05-01", kind=PeriodType.ANNUAL, quarter=None, fy="FY2026")
    fact(db, old, MetricCode.REVENUE_FROM_OPERATIONS, "100")
    fact(db, current, MetricCode.REVENUE_FROM_OPERATIONS, "120")
    save_pillar(db, company.id, pillar(), today=DAY); db.commit()
    result = monitor(db, company)
    row = next(c for c in result["assessment"]["changes"] if c["key"] == "REVENUE_FROM_OPERATIONS:ANNUAL")
    assert row["classification"] == "IMPROVING"


def test_forged_or_low_confidence_commentary_never_enters_assessment(db):
    company, period, _ = setup(db)
    call(db, company, period, "CEO: We target PAT margins of 15% to 20% for FY27.")
    statement = db.scalar(select(ManagementStatement).where(ManagementStatement.company_id == company.id))
    statement.raw_statement = "Management invents a guaranteed return of 900 percent."
    db.commit()
    result = monitor(db, company)
    assert statement.evidence_id not in result["evidence"]
    assert not any(r["kind"] in {"GUIDANCE", "CHECKPOINT_RECORD"} for k, r in result["evidence"].items() if not k.startswith("P"))


def test_corrected_inputs_exclude_stale_metrics(db):
    company, period, revenue = setup(db)
    initial = monitor(db, company)
    assert any(r.get("metric_code") == "PAT_MARGIN" for r in initial["evidence"].values())
    revenue.value = Decimal("60"); db.commit()
    result = monitor(db, company)
    assert not any(r.get("metric_code") == "PAT_MARGIN" for k, r in result["evidence"].items() if not k.startswith("P"))


def test_corrected_historical_fact_and_resolved_requirement(db):
    company, period, revenue = setup(db, history=True)
    revenue.value = Decimal("90"); db.commit()
    first = monitor(db, company)
    assert first["impacts"][0]["new_status"] == "WEAKENING"
    older = db.scalar(select(FinancialFact).where(FinancialFact.metric_code == MetricCode.REVENUE_FROM_OPERATIONS,
        FinancialFact.financial_period_id != period.id))
    older.value = Decimal("80")
    revenue.value = Decimal("120"); db.commit()
    second = monitor(db, company)
    assert any(c["key"] == f"REVISION:F{older.id}" and c["corrected"] for c in second["assessment"]["changes"])
    assert any(c.get("resolved_risk") and c["classification"] == "IMPROVING" for c in second["assessment"]["changes"])


def test_event_ingestion_hook_automatically_assesses_and_deduplicates(db, tmp_path):
    import pymupdf
    from datetime import datetime, timezone
    from app.ingestion.normalizer import normalize_event
    from app.ingestion.sources.base import MonitoredCompany, SourceEvent, DownloadedDocument
    from app.ingestion.storage import LocalDocumentStorage
    from app.services.events.event_service import save_event
    company, _, _ = setup(db)
    monitor(db, company)
    source = SourceEvent(external_symbol=company.symbol, title="Litigation disclosure", external_id="monitor-litigation",
        published_at=datetime(2026, 7, 26, tzinfo=timezone.utc), source_url="https://example.org/filing",
        document_url="https://example.org/filing.pdf")
    normalized = normalize_event(source, "NSE", [MonitoredCompany(company.id, company.symbol, company.name, company.symbol)])
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "The company has received a legal notice. Its outcome remains uncertain.")
        downloaded = DownloadedDocument(pdf.tobytes(), "application/pdf", "filing.pdf")
    storage = LocalDocumentStorage(tmp_path)
    assert save_event(db, normalized, downloaded, None, storage)[0]
    assert rows(db) == 2
    assert not save_event(db, normalized, downloaded, None, storage)[0]
    assert rows(db) == 2
    latest = db.scalar(select(ThesisEvaluationRun).order_by(ThesisEvaluationRun.id.desc()))
    assert any(c["category"] == "CORPORATE_EVENT" for c in latest.assessment["changes"])


@pytest.mark.skipif(os.getenv("ALMONDS_PG_MONITOR_TEST") != "1", reason="Opt-in local PostgreSQL disposable schema")
def test_postgres_migration_concurrency_and_immutable_history():
    import importlib.util
    from pathlib import Path
    from uuid import uuid4
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from app.database import engine as local_engine
    schema = "thesis_monitor_test_" + uuid4().hex
    with local_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(local_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        from sqlalchemy.dialects.postgresql import ENUM
        # Some pre-existing models use create_type=False because their enums
        # normally come from earlier migrations. Supply those in this empty schema.
        with engine.begin() as connection:
            for table in Base.metadata.sorted_tables:
                for column in table.columns:
                    if getattr(column.type, "enums", None):
                        ENUM(*column.type.enums, name=column.type.name).create(connection, checkfirst=True)
        Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if not t.name.startswith(("thesis_", "historical_expectation", "expectation_evaluation"))])
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                for filename in ("0018_living_thesis.py", "0019_thesis_monitoring.py", "0020_historical_expectations.py"):
                    spec = importlib.util.spec_from_file_location(filename[:-3], Path(__file__).parents[1] / "alembic/versions" / filename)
                    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
                    migration.upgrade()
        with Session(engine) as session:
            company, _, _ = setup(session)
            company_id = company.id
        barrier = Barrier(2)
        def evaluate():
            with Session(engine) as session:
                barrier.wait(timeout=5)
                result = monitor_company(session, company_id, today=DAY)
                session.commit()
                return result[0]["run_id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(evaluate), pool.submit(evaluate)]
            ids = [task.result(timeout=20) for task in tasks]
        assert ids[0] == ids[1]
        with Session(engine) as session:
            assert rows(session) == 1
            with pytest.raises(DBAPIError, match="THESIS_HISTORY_IS_IMMUTABLE"):
                session.execute(text("UPDATE thesis_evaluation_runs SET assessment='{}'"))
            session.rollback()
            assert session.get(ThesisEvaluationRun, ids[0]).assessment["first_assessment"]
    finally:
        engine.dispose()
        with local_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
