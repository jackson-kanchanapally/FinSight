"""Opt-in, read-only regression against the existing local ESDS/Eternal filings."""

import os
from decimal import Decimal

import pytest
from sqlalchemy import select
from app.database import SessionLocal
from app.models.company import Company
from app.models.financial import FinancialPeriod, FinancialScope, FinancialStatus
from app.ai.analyst.context_builder import build_context
from app.ai.analyst.fallback import verified_fallback

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PHASE63_DB_REGRESSION") != "1",
    reason="Requires existing local ESDS/Eternal database",
)


@pytest.mark.parametrize(
    "symbol,verdict,confidence",
    [("ESDS", "NEUTRAL", "MEDIUM"), ("ETERNAL", "NEGATIVE", "LOW")],
)
def test_stored_q1_fy2027_report(symbol, verdict, confidence):
    with SessionLocal() as db:
        company = db.scalar(select(Company).where(Company.symbol == symbol))
        assert company is not None
        period = db.scalar(
            select(FinancialPeriod).where(
                FinancialPeriod.company_id == company.id,
                FinancialPeriod.scope == FinancialScope.CONSOLIDATED,
                FinancialPeriod.fiscal_year == "FY2027",
                FinancialPeriod.quarter == 1,
                FinancialPeriod.status == FinancialStatus.VALIDATED,
            )
        )
        assert period is not None
        context = build_context(db, company.name, symbol, period)
        report = context.investor_report
        assert (report["results"]["verdict"], report["results"]["confidence"]) == (
            verdict,
            confidence,
        )
        assert report["earnings_quality"]["pat_bridge"]
        assert verified_fallback(
            context.registry, period, context.reasoning
        ).quality_report
        for row in report["earnings_quality"]["pat_bridge"]:
            for metric_id in row["evidence_ids"]:
                for fact_id in context.registry[metric_id]["input_evidence_ids"]:
                    assert context.registry[fact_id]["page"]
                    assert "#page=" in context.registry[fact_id]["document_url"]
        if symbol == "ESDS":
            assert Decimal(report["tested_pe"]["current_pe"]) == Decimal("138.22")
            assert (
                Decimal(
                    report["tested_pe"]["reverse_expectations"]["required_eps_cagr_pct"]
                )
                > 0
            )
            assert report["tested_pe"]["supported_pe_range"]
        else:
            assert report["valuation"]["verdict"] == "INSUFFICIENT_EVIDENCE"
            assert report["tested_pe"]["ttm_eps"] is None
            assert "higher tax" in report["earnings_quality"]["text"]
