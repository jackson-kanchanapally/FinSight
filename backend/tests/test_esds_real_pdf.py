"""Optional offline integration check against a previously downloaded official PDF.

Set ALMONDS_ESDS_PDF to the local document path. The PDF is never fetched by
the test suite and is not committed to the repository.
"""

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.financial import FinancialScope, MetricCode, PeriodType
from app.services.financials.extractor import extract_pdf_tables


def test_real_esds_q1_fy27_against_manually_read_table():
    location = os.environ.get("ALMONDS_ESDS_PDF") or os.environ.get("FINSIGHT_ESDS_PDF")
    if not location:
        pytest.skip("Set ALMONDS_ESDS_PDF for the optional local official-PDF check")
    path = Path(location)
    if not path.is_file():
        pytest.fail(f"ALMONDS_ESDS_PDF does not exist: {path}")
    periods = extract_pdf_tables(path)
    expected = {
        (FinancialScope.CONSOLIDATED, date(2026, 6, 30)):
            (Decimal("1336.57"), Decimal("292.77"), Decimal("2.92"), 7),
        (FinancialScope.CONSOLIDATED, date(2026, 3, 31)):
            (Decimal("1675.00"), Decimal("676.76"), Decimal("6.71"), 7),
        (FinancialScope.CONSOLIDATED, date(2025, 6, 30)):
            (Decimal("1245.87"), Decimal("256.85"), Decimal("2.56"), 7),
        (FinancialScope.STANDALONE, date(2026, 6, 30)):
            (Decimal("1161.66"), Decimal("181.99"), Decimal("1.81"), 3),
    }
    for (scope, end), (revenue, pat, eps, page) in expected.items():
        period = next(p for p in periods if p.scope == scope and p.identity.end == end
                      and p.identity.quarter is not None)
        assert period.issues == []
        assert period.facts[MetricCode.REVENUE_FROM_OPERATIONS].original_value == revenue
        assert period.facts[MetricCode.PAT].original_value == pat
        assert period.facts[MetricCode.BASIC_EPS].original_value == eps
        assert period.facts[MetricCode.REVENUE_FROM_OPERATIONS].page == page
    annual = next(p for p in periods if p.scope == FinancialScope.CONSOLIDATED
                  and p.identity.period_type == PeriodType.ANNUAL)
    assert annual.issues == []
    assert annual.facts[MetricCode.REVENUE_FROM_OPERATIONS].original_value == Decimal("4722.10")
    assert annual.facts[MetricCode.TOTAL_EXPENSES].original_value == Decimal("3132.29")
    assert annual.facts[MetricCode.TOTAL_EXPENSES].calculated
    assert annual.facts[MetricCode.PAT].original_value == Decimal("1208.23")
    assert annual.facts[MetricCode.PAT].calculated
    assert annual.facts[MetricCode.DILUTED_EPS].original_value == Decimal("11.76")
    assert any(period.issues for period in periods)
