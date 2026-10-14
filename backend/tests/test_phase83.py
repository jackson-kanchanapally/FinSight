"""Historical forecasting accountability; deterministic fixtures, no LLM or network."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from decimal import Decimal
from threading import Barrier
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.main import app
from app.models.expectations import HistoricalExpectation, HistoricalExpectationRevision, ExpectationEvaluation
from app.models.financial import FinancialUnit, MetricCode, FinancialFactCorrection
from app.schemas.expectations import ExpectationInput
from app.services import expectations as service
from app.services.expectation_math import compare, tolerance_policy
from app.services.expectation_statistics import statistics, thesis_history
from app.services.financials.service import calculate_company_metrics
from tests.test_phase71 import issuer, result_period
from tests.test_phase82 import fact
from tests.test_phase72 import call
from tests.test_phase8 import pillar
from app.services.thesis import save_pillar
from app.models.thesis import ThesisPillarRevision

CAPTURE = datetime(2026, 7, 1, tzinfo=timezone.utc)
CUTOFF = datetime(2026, 7, 15, tzinfo=timezone.utc)
AFTER = datetime(2026, 8, 1, tzinfo=timezone.utc)

@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()

@pytest.fixture
def clock(monkeypatch):
    current = [CAPTURE]
    monkeypatch.setattr(service, "utcnow", lambda: current[0])
    return current

def data(**changes):
    base = dict(metric="REVENUE_FROM_OPERATIONS", period="2026-06-30", unit="INR_CRORE", expected_value="100",
        description="Revenue target", source_reference="Research note 1", cutoff_at=CUTOFF.isoformat())
    base.update(changes)
    return ExpectationInput.model_validate(base)

def setup(db):
    company = issuer(db, "ACCURACY")
    period = result_period(db, company, date(2026, 6, 30), "2026-07-20")
    revenue = fact(db, period, MetricCode.REVENUE_FROM_OPERATIONS, "100")
    db.commit()
    return company, period, revenue

def math_definition(**kwargs):
    definition = data(**kwargs).model_dump(mode="json")
    definition["tolerance"] = tolerance_policy(definition["metric"])
    return definition

@pytest.mark.parametrize("expected,actual,outcome,signed", [
    ("100", "100", "MET", "0"), ("120", "100", "MISSED", "20"), ("80", "100", "MISSED", "-20"),
    ("104", "100", "MET", "4"), ("108", "100", "PARTIALLY_MET", "8"),
    ("-20", "-10", "MISSED", "-10"), ("-10", "-10", "MET", "0"), ("1", "-1", "MISSED", "2")])
def test_decimal_error(expected, actual, outcome, signed):
    row = compare(math_definition(expected_value=expected), actual)
    assert row["outcome"] == outcome and Decimal(row["signed_error"]) == Decimal(signed)
    assert Decimal(row["absolute_percentage_error"]) == abs(Decimal(signed)) / abs(Decimal(actual)) * 100

@pytest.mark.parametrize("actual", ["0", "0.0000001", "-0.000001"])
def test_near_zero_no_percentage(actual):
    assert compare(math_definition(), actual)["percentage_error"] is None

def test_ranges_units_and_percentage_point_semantics():
    row = compare(math_definition(expectation_type="RANGE", lower_value="9000", upper_value="11000", unit="INR_LAKH"), "100")
    assert row["range_coverage"] and row["outcome"] == "MET"
    assert compare(math_definition(unit="INR", expected_value="1000000000"), "100")["outcome"] == "MET"
    row = compare(math_definition(metric="PAT_MARGIN", unit="BASIS_POINTS", currency="NONE", expected_value="1500"), "15.4")
    assert row["outcome"] == "MET" and row["percentage_error"] is None
    assert compare(math_definition(metric="EBITDA_MARGIN_CHANGE_BPS", unit="PERCENTAGE_POINTS", currency="NONE", expected_value="1"), "100")["outcome"] == "MET"
    from app.services.expectation_math import normalize
    with pytest.raises(ValueError): normalize(1, "PERCENT", "EBITDA_MARGIN_CHANGE_BPS")
    with pytest.raises(ValueError): normalize(1, "PERCENTAGE_POINTS", "REVENUE_YOY")

def test_revisions_dedup_and_original_scoring(db, clock):
    company, _, _ = setup(db)
    ledger, original = service.save(db, company.id, data(expected_value="80"))
    assert service.save(db, company.id, data(expected_value="80"))[0].id == ledger.id
    _, revised = service.save(db, company.id, data(expected_version=1), ledger.id)
    assert original.definition["expected_value"] == "80" and revised.version == 2
    with pytest.raises(ValueError, match="VERSION_CONFLICT"):
        service.save(db, company.id, data(expected_version=1), ledger.id)
    clock[0] = AFTER
    service.evaluate(db, company.id, ledger.id); db.commit()
    history = service.revisions_view(db, company.id, ledger.id)
    assert history[0]["status"] == "SUPERSEDED" and history[0]["evaluation"]["outcome"] == "MISSED"
    assert history[1]["evaluation"]["outcome"] == "MET"
    stats = statistics(db, company.id)
    assert stats["total"] == 1 and stats["missed"] == 1 and stats["groups"][0]["n"] == 1
    assert stats["groups"][0]["met_rate"] is None

@pytest.mark.parametrize("mode", ["orm", "sql_update", "sql_delete"])
def test_immutable_history(db, clock, mode):
    company, _, _ = setup(db)
    _, revision = service.save(db, company.id, data()); db.commit()
    with pytest.raises((ValueError, DBAPIError), match="EXPECTATION_HISTORY_IS_IMMUTABLE"):
        if mode == "orm":
            revision.definition = {}; db.flush()
        else:
            db.execute(text("UPDATE historical_expectation_revisions SET definition='{}'" if mode == "sql_update" else "DELETE FROM historical_expectation_revisions"))
    db.rollback()

def test_import_backdating_and_future_leakage(db, clock):
    company, _, _ = setup(db)
    clock[0] = AFTER
    with pytest.raises(ValueError, match="PAST_CUTOFF"):
        service.save(db, company.id, data())
    ledger, _ = service.save(db, company.id, data(historical_import=True, issued_at="2026-07-01T00:00:00Z"))
    result = service.evaluate(db, company.id, ledger.id)
    assert not result.prospective_eligible and result.outcome == "NOT_COMPARABLE"
    assert statistics(db, company.id)["evaluated"] == 0
    clock[0] = CAPTURE
    ledger, _ = service.save(db, company.id, data(source_reference="late cutoff", cutoff_at="2026-07-21T00:00:00Z"))
    clock[0] = AFTER
    assert service.evaluate(db, company.id, ledger.id).outcome == "NOT_COMPARABLE"

@pytest.mark.parametrize("change", [dict(financial_scope="STANDALONE"), dict(period="2026-03-31"), dict(period_type="ANNUAL", period="2026-03-31"), dict(metric="PAT")])
def test_scope_period_metric_mismatch(db, clock, change):
    company, _, _ = setup(db)
    ledger, _ = service.save(db, company.id, data(**change))
    clock[0] = AFTER
    assert service.evaluate(db, company.id, ledger.id).outcome == "INSUFFICIENT_EVIDENCE"

def test_missing_unverified_future_sources(db, clock):
    company, _, revenue = setup(db)
    with pytest.raises(ValueError, match="INVALID_OR_UNVERIFIED"):
        service.save(db, company.id, data(evidence_id=f"F{revenue.id}"))
    ledger, _ = service.save(db, company.id, data())
    clock[0] = AFTER
    revenue.status = "NEEDS_REVIEW"; db.commit()
    assert service.evaluate(db, company.id, ledger.id).outcome == "INSUFFICIENT_EVIDENCE"

def test_calibration_correctness_and_validation(db, clock):
    from pydantic import ValidationError
    for overrides in ({"confidence": "1.1"}, {"confidence": "NaN"}, {"confidence": "0.9"}, {"created_at": CAPTURE.isoformat()}):
        with pytest.raises(ValidationError): data(**overrides)
    company, _, _ = setup(db)
    for number in range(20):
        service.save(db, company.id, data(source_reference=f"note {number}", confidence="0.75",
            confidence_event="MET_UNDER_FROZEN_TOLERANCE", expected_value="100" if number < 15 else "80"))
    clock[0] = AFTER
    service.refresh_expectations(db, company.id)
    row = statistics(db, company.id)["calibration"][0]
    assert row["n"] == 20 and row["status"] == "SUFFICIENT_DATA"
    assert Decimal(row["brier"]) == Decimal("0.1875") and Decimal(row["expected_calibration_error"]) == 0
    assert row["buckets"][0]["observed"] == "0.75"

def test_direction_baseline(db, clock):
    company, _, _ = setup(db)
    prior = result_period(db, company, date(2025, 6, 30), "2025-07-20", fy="FY2026")
    baseline = fact(db, prior, MetricCode.REVENUE_FROM_OPERATIONS, "90")
    ledger, _ = service.save(db, company.id, data(expectation_type="DIRECTIONAL", direction="UP", baseline_evidence_id=f"F{baseline.id}"))
    clock[0] = AFTER
    outcome = service.evaluate(db, company.id, ledger.id)
    assert outcome.outcome == "MET" and outcome.result["directional_correctness"]
    assert outcome.result["percentage_error"] is None

def test_ingestion_idempotence_and_corrections(db, clock):
    company, _, revenue = setup(db)
    ledger, _ = service.save(db, company.id, data()); db.commit()
    clock[0] = AFTER
    calculate_company_metrics(db, company.id)
    first = db.scalar(select(ExpectationEvaluation))
    assert first.outcome == "MET"
    calculate_company_metrics(db, company.id)
    assert db.scalar(select(func.count()).select_from(ExpectationEvaluation)) == 1
    db.add(FinancialFactCorrection(fact_id=revenue.id, previous_value=100, corrected_value=80, reason="Correction"))
    revenue.value = Decimal("80"); db.commit()
    calculate_company_metrics(db, company.id)
    assert db.scalar(select(func.count()).select_from(ExpectationEvaluation)) == 2
    assert Decimal(first.result["actual_value"]) == 100
    assert service.evaluate(db, company.id, ledger.id).outcome == "MISSED"

def test_failure_retry(db, clock, monkeypatch):
    company, _, _ = setup(db)
    ledger, _ = service.save(db, company.id, data()); db.commit()
    clock[0] = AFTER
    with monkeypatch.context() as m:
        m.setattr(service, "actual_evidence", lambda *_: (_ for _ in ()).throw(RuntimeError("failure")))
        assert service.refresh_expectations(db, company.id) is None
    assert service.refresh_expectations(db, company.id) == 1
    assert service.evaluate(db, company.id, ledger.id).outcome == "MET"

def test_api_validation_filtering_isolation(db, clock):
    company, _, _ = setup(db)
    issuer(db, "OTHER"); db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            prefix = "/api/companies/ACCURACY/expectations"
            response = client.post(prefix, json=data().model_dump(mode="json"))
            assert response.status_code == 201, response.text
            row = response.json()
            assert row["created_at"].endswith("Z") and row["status"] == "PENDING"
            assert client.get(prefix + "?limit=0").status_code == 422
            assert client.get(prefix + "?scope=STANDALONE").json()["total"] == 0
            assert client.get(f"/api/companies/OTHER/expectations/{row['id']}/revisions").status_code == 404
            assert client.post(prefix, json={**data().model_dump(mode="json"), "confidence": 2}).status_code == 422
            clock[0] = AFTER
            assert client.post(prefix + "/evaluate-pending").json()["processed"] == 1
            assert client.get(prefix + f"/{row['id']}/results").json()[0]["outcome"] == "MET"
            assert client.get("/api/companies/ACCURACY/accuracy").json()["met"] == 1
            assert client.get("/api/accuracy").status_code == 200
    finally:
        app.dependency_overrides.clear()

def test_concurrent_evaluation(tmp_path, clock):
    engine = create_engine(f"sqlite:///{tmp_path / 'accuracy.db'}", connect_args={"timeout": 15, "check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        company, _, _ = setup(session)
        ledger, _ = service.save(session, company.id, data()); session.commit()
        company_id, ledger_id = company.id, ledger.id
    clock[0] = AFTER
    barrier = Barrier(2)
    def run():
        with Session(engine) as session:
            barrier.wait(timeout=5)
            row = service.evaluate(session, company_id, ledger_id)
            session.commit()
            return row.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = [pool.submit(run), pool.submit(run)]
        assert tasks[0].result(timeout=20) == tasks[1].result(timeout=20)
    engine.dispose()

def test_auto_guidance_and_withdrawal_are_source_backed(db, clock):
    from app.models.commentary import ManagementStatement, ManagementGuidance
    from app.services.expectation_guidance import register_guidance
    company, period, _ = setup(db)
    clock[0] = AFTER
    call(db, company, period, "CEO: We target PAT margins of 15% to 20% for FY27.", published="2026-07-25")
    register_guidance(db, company.id, AFTER)
    ledger = db.scalar(select(HistoricalExpectation).where(HistoricalExpectation.source_type == "MANAGEMENT"))
    assert ledger is not None
    original = service.latest_revision(db, ledger.id)
    assert original.provenance["source_snapshot"]["source_quote"]
    assert original.definition["guidance_kind"] == "FIRM_TARGET"
    assert original.definition["period"] == "2027-03-31"
    call(db, company, period, "CEO: We withdraw our previous PAT margin guidance for FY27.", published="2026-07-29")
    result = service.evaluate(db, company.id, ledger.id)
    assert result.result["withdrawn"] and result.outcome == "INSUFFICIENT_EVIDENCE"
    assert result.result["withdrawal_evidence"][0]["source_quote"]
    assert original.definition["lifecycle"] == "ACTIVE"
    assert statistics(db, company.id)["evaluated"] == 0


def test_unmeasurable_claim_stays_unresolved(db, clock):
    company, _, _ = setup(db)
    ledger, _ = service.save(db, company.id, data(metric="BUSINESS_MILESTONE", expectation_type="QUALITATIVE",
        unit="EVENT", currency="NONE", expected_value=None, description="The new facility begins operating"))
    clock[0] = AFTER
    assert service.evaluate(db, company.id, ledger.id).outcome == "INSUFFICIENT_EVIDENCE"


def test_thesis_links_preserve_original_version_and_verdict(db, clock):
    company, _, _ = setup(db)
    # Existing thesis timestamps are server generated. Use current capture for this
    # provenance test and a future cutoff; no historical thesis is backdated.
    save_pillar(db, company.id, pillar(), today=date(2026, 7, 1)); db.commit()
    thesis = db.scalar(select(ThesisPillarRevision))
    clock[0] = datetime.now(timezone.utc)
    ledger, revision = service.save(db, company.id, data(period="2027-06-30", cutoff_at="2027-07-15T00:00:00Z", thesis_revision_id=thesis.id))
    output = thesis_history(db, company.id)
    assert output["total"] == 1 and output["items"][0]["outcome"] == "UNRESOLVED"
    assert output["items"][0]["original_thesis"] == thesis.definition
    assert thesis.initial_status == "UNPROVEN"


def test_imported_ai_never_claims_accuracy(db, clock):
    company, _, revenue = setup(db)
    clock[0] = AFTER
    ledger, _ = service.save(db, company.id, data(source_type="AI", historical_import=True,
        evidence_id=f"F{revenue.id}", cutoff_at="2026-07-25T00:00:00Z"))
    result = service.evaluate(db, company.id, ledger.id)
    assert result.outcome == "INSUFFICIENT_EVIDENCE" and not result.prospective_eligible


def test_low_confidence_labels_are_not_probabilities(db, clock):
    company, _, _ = setup(db)
    ledger, _ = service.save(db, company.id, data(confidence_label="HIGH"))
    clock[0] = AFTER
    service.evaluate(db, company.id, ledger.id)
    row = statistics(db, company.id)["calibration"][0]
    assert row["n"] == 0 and row["brier"] is None and row["status"] == "INSUFFICIENT_DATA"


def test_derived_margin_uses_existing_metrics_and_rejects_stale_inputs(db, clock):
    company, period, revenue = setup(db)
    fact(db, period, MetricCode.PAT, "15")
    ledger, _ = service.save(db, company.id, data(metric="PAT_MARGIN", unit="PERCENT", currency="NONE", expected_value="15")); db.commit()
    clock[0] = AFTER
    calculate_company_metrics(db, company.id)
    result = service.evaluate(db, company.id, ledger.id)
    assert result.outcome == "MET" and len(result.result["evidence"]) == 3
    revenue.value = Decimal("50"); db.commit()
    assert service.evaluate(db, company.id, ledger.id).outcome == "INSUFFICIENT_EVIDENCE"

def test_evidence_restoration_records_new_current_state(db, clock):
    company, _, revenue = setup(db)
    ledger, _ = service.save(db, company.id, data()); db.commit()
    clock[0] = AFTER
    first = service.evaluate(db, company.id, ledger.id); db.commit()
    revenue.status = "NEEDS_REVIEW"; db.commit()
    second = service.evaluate(db, company.id, ledger.id); db.commit()
    revenue.status = "VERIFIED_PDF"; db.commit()
    third = service.evaluate(db, company.id, ledger.id); db.commit()
    assert first.outcome == third.outcome == "MET" and second.outcome == "INSUFFICIENT_EVIDENCE"
    assert third.id > second.id and service.evaluate(db, company.id, ledger.id).id == third.id
    assert service.list_expectations(db, company.id)["items"][0]["evaluation"]["id"] == third.id


def test_guidance_revision_appends_and_does_not_double_count(db, clock):
    company, period, _ = setup(db)
    clock[0] = AFTER
    call(db, company, period, "CEO: We target PAT margins of 15% to 20% for FY27.", published="2026-07-25")
    ledger = db.scalar(select(HistoricalExpectation).where(HistoricalExpectation.source_type == "MANAGEMENT"))
    call(db, company, period, "CEO: We target PAT margins of 20% to 25% for FY27.", published="2026-07-26")
    history = service.revisions_view(db, company.id, ledger.id)
    assert len(history) == 2 and history[0]["definition"]["lower_value"] == "15.0000"
    assert history[1]["definition"]["lower_value"] == "20.0000"
    assert statistics(db, company.id)["total"] == 1


def test_date_only_publication_excludes_same_day_capture(db, clock):
    from app.models.event import CompanyEvent, PublicationPrecision
    company, period, _ = setup(db)
    event = db.get(CompanyEvent, period.source_event_id)
    event.publication_precision = PublicationPrecision.DATE; db.commit()
    clock[0] = datetime(2026, 7, 20, 1, tzinfo=timezone.utc)
    ledger, _ = service.save(db, company.id, data(cutoff_at="2026-07-20T02:00:00Z"))
    clock[0] = AFTER
    assert service.evaluate(db, company.id, ledger.id).outcome == "NOT_COMPARABLE"


def test_sqlite_additive_migration_and_immutability(tmp_path, clock):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    names = {"historical_expectations", "historical_expectation_revisions", "expectation_evaluations"}
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in names])
    spec = importlib.util.spec_from_file_location("phase83migration", Path(__file__).parents[1] / "alembic/versions/0020_historical_expectations.py")
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
    with Session(engine) as session:
        company, _, _ = setup(session)
        service.save(session, company.id, data()); session.commit()
        with pytest.raises(DBAPIError, match="EXPECTATION_HISTORY_IS_IMMUTABLE"):
            session.execute(text("UPDATE historical_expectations SET created_at='2000-01-01'"))
        session.rollback()
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match="lossy downgrade"):
                migration.downgrade()
    engine.dispose()


@pytest.mark.skipif(os.getenv("ALMONDS_PG_ACCURACY_TEST") != "1", reason="Opt-in disposable local PostgreSQL schema")
def test_postgres_migration_concurrency_and_guards(clock):
    import importlib.util
    from pathlib import Path
    from uuid import uuid4
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.dialects.postgresql import ENUM
    from app.database import engine as local_engine
    schema = "accuracy_test_" + uuid4().hex
    with local_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(local_engine.url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with engine.begin() as connection:
            for table in Base.metadata.sorted_tables:
                for column in table.columns:
                    if getattr(column.type, "enums", None):
                        ENUM(*column.type.enums, name=column.type.name).create(connection, checkfirst=True)
        names = {"historical_expectations", "historical_expectation_revisions", "expectation_evaluations"}
        Base.metadata.create_all(engine, tables=[t for t in Base.metadata.sorted_tables if t.name not in names])
        spec = importlib.util.spec_from_file_location("pgphase83", Path(__file__).parents[1] / "alembic/versions/0020_historical_expectations.py")
        migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
        with Session(engine) as session:
            company, _, _ = setup(session)
            ledger, _ = service.save(session, company.id, data()); session.commit()
            company_id, ledger_id = company.id, ledger.id
        clock[0] = AFTER
        barrier = Barrier(2)
        def run():
            with Session(engine) as session:
                barrier.wait(timeout=5)
                result = service.evaluate(session, company_id, ledger_id); session.commit()
                return result.id
        with ThreadPoolExecutor(max_workers=2) as pool:
            tasks = [pool.submit(run), pool.submit(run)]
            assert tasks[0].result(timeout=20) == tasks[1].result(timeout=20)
        with Session(engine) as session:
            for table in names:
                with pytest.raises(DBAPIError, match="EXPECTATION_HISTORY_IS_IMMUTABLE"):
                    session.execute(text(f"DELETE FROM {table}"))
                session.rollback()
            assert session.scalar(select(func.count()).select_from(ExpectationEvaluation)) == 1
    finally:
        engine.dispose()
        with local_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))

def test_management_target_cannot_be_invented_from_valid_quote(db, clock):
    from app.models.commentary import ManagementStatement
    company, period, _ = setup(db)
    clock[0] = AFTER
    call(db, company, period, "CEO: We target PAT margins of 15% to 20% for FY27.", published="2026-07-25")
    statement = db.scalar(select(ManagementStatement))
    with pytest.raises(ValueError, match="GUIDANCE_NORMALIZED_TARGET_MISMATCH"):
        service.save(db, company.id, data(source_type="MANAGEMENT", evidence_id=statement.evidence_id,
            guidance_kind="FIRM_TARGET", metric="PAT_MARGIN", expected_value="90", unit="PERCENT", currency="NONE",
            period="2027-03-31", period_type="ANNUAL", cutoff_at="2027-03-31T23:59:59.999999Z"))


def test_wrong_period_start_and_reporting_basis_cannot_match(db, clock):
    from app.models.event import CompanyEvent, ReportingBasis
    company, period, _ = setup(db)
    ledger, _ = service.save(db, company.id, data()); db.commit()
    clock[0] = AFTER
    period.period_start = date(2026, 1, 1); db.commit()
    assert service.evaluate(db, company.id, ledger.id).outcome == "INSUFFICIENT_EVIDENCE"
    period.period_start = date(2026, 4, 1)
    db.get(CompanyEvent, period.source_event_id).reporting_basis = ReportingBasis.STANDALONE
    db.commit()
    assert service.evaluate(db, company.id, ledger.id).outcome == "INSUFFICIENT_EVIDENCE"

def test_source_document_later_than_event_cannot_leak_past_cutoff(db, clock):
    from app.models.event import CompanyEvent, Document
    company, period, revenue = setup(db)
    db.get(CompanyEvent, period.source_event_id).published_at = datetime(2026, 7, 1, tzinfo=timezone.utc)
    db.get(Document, period.source_document_id).published_at = datetime(2026, 7, 20, tzinfo=timezone.utc)
    db.commit()
    clock[0] = AFTER
    with pytest.raises(ValueError, match="SOURCE_PUBLISHED_AFTER_CUTOFF"):
        service.save(db, company.id, data(historical_import=True, evidence_id=f"F{revenue.id}"))
