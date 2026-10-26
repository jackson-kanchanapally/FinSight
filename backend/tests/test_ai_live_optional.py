"""Explicit opt-in only: uses configured Codex and a seeded local database."""

import os

import pytest
from sqlalchemy import select

from app.ai.analyst.schemas import AnalystOutput
from app.ai.analyst.service import analyze_period
from app.ai.evaluation.esds import evaluate_esds
from app.database import SessionLocal
from app.models.company import Company
from app.models.financial import FinancialPeriod, FinancialScope, FinancialStatus


def test_live_esds_analyst():
    if os.environ.get("CODEX_TEST_ENABLED", "").lower() != "true":
        pytest.skip(
            "Set CODEX_TEST_ENABLED=true for Codex and PostgreSQL integration"
        )
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.symbol == "ESDS"))
        assert company is not None
        period = db.scalar(
            select(FinancialPeriod)
            .where(
                FinancialPeriod.company_id == company.id,
                FinancialPeriod.scope == FinancialScope.CONSOLIDATED,
                FinancialPeriod.status == FinancialStatus.VALIDATED,
            )
            .order_by(FinancialPeriod.period_end.desc())
        )
        assert period is not None
        record = analyze_period(db, company, period, regenerate=True)
        assert record.status == "SUCCESS", record.failure_reason
        report = evaluate_esds(
            AnalystOutput.model_validate(record.structured_output),
            record.evidence_registry,
            period.period_end.year,
        )
        print(report)
        assert report["all_passed"], report
