"""Phase 6.2: attributed commentary and transparent verdict arithmetic."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.database import Base
from app.database import get_db
from app.main import app
from app.market.historical_pe import historical_pe_summary
from app.models.commentary import (
    CommentaryDocument,
    ManagementGuidance,
    ManagementStatement,
)
from app.models.company import Company
from app.models.event import (
    CompanyEvent,
    DataOrigin,
    Document,
    DocumentPage,
    EventType,
    ProcessingStatus,
)
from app.models.financial import (
    FinancialPeriod,
    FinancialScope,
    FinancialStatus,
    PeriodType,
)
from app.services.commentary import (
    guidance_change,
    guidance_vs_actual,
    ingest_commentary,
    parse_guidance,
    parse_statement,
)
from app.services.investor_verdict import (
    classify_valuation,
    required_terminal_pe,
    results_verdict,
    tested_pe_range as calculate_tested_pe_range,
)
from app.ai.analyst.reasoning import margin_change_units
from tests.test_market_valuation import (
    market_db,
    company,
    add_quarter,
    four_quarters,
    quote,
)


def _registry(
    revenue=None, pat=None, margin=None, revenue_qoq=None, pat_qoq=None, cash=None
):
    values = {
        "REVENUE_YOY": revenue,
        "PAT_YOY": pat,
        "EBITDA_MARGIN_CHANGE_BPS": margin,
        "REVENUE_QOQ": revenue_qoq,
        "PAT_QOQ": pat_qoq,
    }
    result = {
        f"M{i}": {"kind": "METRIC", "metric_code": name, "value": str(value)}
        for i, (name, value) in enumerate(values.items(), 1)
        if value is not None
    }
    if cash is not None:
        result["V_CASH"] = {
            "kind": "VALUATION",
            "metric_code": "TTM_OPERATING_CASH_FLOW",
            "value": str(cash),
        }
        result["V_PAT"] = {
            "kind": "VALUATION",
            "metric_code": "TTM_PAT",
            "value": "100",
        }
    return result


@pytest.mark.parametrize(
    ("registry", "expected"),
    [
        (_registry(30, 35, 140, cash=120), "VERY_POSITIVE"),
        (_registry(10, 15, 20, cash=100), "POSITIVE"),
        (_registry(3, -2), "NEUTRAL"),
        (_registry(-8, -12), "NEGATIVE"),
        (_registry(-30, -35), "VERY_NEGATIVE"),
        (_registry(10), "POSITIVE"),
        (_registry(), "INSUFFICIENT_EVIDENCE"),
        (_registry(7.28, 13.98, 1.15, -20.2, -56.74), "NEUTRAL"),
        (_registry(10, 15, 20, cash=-10), "NEGATIVE"),
    ],
)
def test_results_verdict_materiality(registry, expected):
    assert results_verdict(registry)["verdict"] == expected


def test_management_claim_is_attributed_and_guidance_is_structured():
    assert parse_statement("Demand remains very strong") is None
    claim = parse_statement("CEO: Demand remains very strong across our markets.")
    assert claim["statement_type"] == "MANAGEMENT_CLAIM"
    assert claim["raw_statement"] == "Demand remains very strong across our markets."
    guidance = parse_statement("CFO: We expect FY27 revenue growth of 20–25%.")
    assert guidance["statement_type"] == "GUIDANCE"
    parsed = parse_guidance(guidance)
    assert parsed == {
        "metric": "REVENUE_GROWTH",
        "target_period": "FY27",
        "lower_bound": Decimal(20),
        "upper_bound": Decimal(25),
        "unit": "PERCENT",
    }
    assert (
        parse_guidance(parse_statement("CEO: We expect revenue growth of 20–25%."))
        is None
    )
    capex = parse_guidance(
        parse_statement("CFO: We expect FY27 capex of ₹100–150 crore.")
    )
    assert capex["metric"] == "CAPEX" and capex["unit"] == "INR_CRORE"
    assert (capex["lower_bound"], capex["upper_bound"]) == (Decimal(100), Decimal(150))
    assert (
        parse_statement("CEO: We have withdrawn FY27 revenue guidance at this time.")[
            "statement_type"
        ]
        == "GUIDANCE_WITHDRAWN"
    )


def test_guidance_history_and_actual():
    old = ManagementGuidance(
        metric="REVENUE_GROWTH",
        target_period="FY27",
        unit="PERCENT",
        lower_bound=Decimal(20),
        upper_bound=Decimal(25),
    )
    same = ManagementGuidance(
        metric="REVENUE_GROWTH",
        target_period="FY27",
        unit="PERCENT",
        lower_bound=Decimal(20),
        upper_bound=Decimal(25),
    )
    raised = ManagementGuidance(
        metric="REVENUE_GROWTH",
        target_period="FY27",
        unit="PERCENT",
        lower_bound=Decimal(25),
        upper_bound=Decimal(30),
    )
    cut = ManagementGuidance(
        metric="REVENUE_GROWTH",
        target_period="FY27",
        unit="PERCENT",
        lower_bound=Decimal(15),
        upper_bound=Decimal(18),
    )
    assert guidance_change(old, same) == "GUIDANCE_MAINTAINED"
    assert guidance_change(old, raised) == "GUIDANCE_RAISED"
    assert guidance_change(old, cut) == "GUIDANCE_CUT"
    assert guidance_vs_actual(old, Decimal(24), "FY27") == "WITHIN_GUIDANCE"
    assert guidance_vs_actual(old, Decimal(30), "FY27") == "ABOVE_GUIDANCE"
    assert guidance_vs_actual(old, Decimal(18), "FY27") == "BELOW_GUIDANCE"
    assert guidance_vs_actual(old, Decimal(18), "FY26") == "TOO_EARLY_TO_ASSESS"


def test_official_document_ingestion_is_period_linked_and_idempotent():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        company = Company(symbol="DEMO", name="Demo Limited", exchange="NSE")
        db.add(company)
        db.flush()
        published = datetime(2026, 9, 24, tzinfo=timezone.utc)
        result = CompanyEvent(
            company_id=company.id,
            event_type=EventType.FINANCIAL_RESULTS,
            title="Q1 FY27 Results",
            published_at=published,
            source="NSE",
            source_url="https://example.com/result",
            data_origin=DataOrigin.LIVE,
            fingerprint="r" * 64,
        )
        call = CompanyEvent(
            company_id=company.id,
            event_type=EventType.EARNINGS_CALL,
            title="Q1 FY27 Concall Transcript",
            published_at=published,
            source="NSE",
            source_url="https://example.com/call",
            data_origin=DataOrigin.LIVE,
            quarter=1,
            fiscal_year="FY2027",
            fingerprint="c" * 64,
        )
        db.add_all([result, call])
        db.flush()
        result_doc = Document(
            company_id=company.id,
            event_id=result.id,
            source="NSE",
            source_url="https://example.com/result",
            document_url="https://example.com/result.pdf",
            processing_status=ProcessingStatus.PROCESSED,
        )
        call_doc = Document(
            company_id=company.id,
            event_id=call.id,
            source="NSE",
            source_url="https://example.com/call",
            document_url="https://example.com/call.pdf",
            processing_status=ProcessingStatus.PROCESSED,
        )
        db.add_all([result_doc, call_doc])
        db.flush()
        db.add(
            DocumentPage(
                document_id=call_doc.id,
                page_number=2,
                text="CEO: We expect FY27 revenue growth of 20–25%.\nAnalyst: Is demand strong?\nCEO: Demand remains strong.",
            )
        )
        period = FinancialPeriod(
            company_id=company.id,
            fiscal_year="FY2027",
            quarter=1,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 6, 30),
            period_type=PeriodType.QUARTERLY,
            scope=FinancialScope.CONSOLIDATED,
            status=FinancialStatus.VALIDATED,
            source_event_id=result.id,
            source_document_id=result_doc.id,
        )
        db.add(period)
        db.commit()
        first = ingest_commentary(db, period)
        second = ingest_commentary(db, period)
        assert len(first) == len(second) == 2
        assert all(
            item.document_id == call_doc.id and item.evidence_id.startswith("S")
            for item in first
        )
        links = list(db.scalars(select(CommentaryDocument)))
        assert len(links) == 1 and links[0].financial_period_id == period.id
        assert links[0].document_type == "EARNINGS_CALL"
        guidance = list(db.scalars(select(ManagementGuidance)))
        assert len(guidance) == 1
        assert guidance[0].statement_id == first[0].id
    engine.dispose()


@pytest.mark.parametrize(
    ("current", "expected"),
    [
        (Decimal(50), "EXTREMELY_UNDERVALUED"),
        (Decimal(80), "UNDERVALUED"),
        (Decimal(100), "FAIRLY_VALUED"),
        (Decimal(125), "OVERVALUED"),
        (Decimal(150), "EXTREMELY_OVERVALUED"),
    ],
)
def test_valuation_bands(current, expected):
    assert classify_valuation(current, Decimal(100), Decimal(110)) == expected


def test_tested_pe_math_and_missing_evidence():
    unavailable = calculate_tested_pe_range(
        current_eps=Decimal(12),
        current_price=Decimal(1200),
        growth_pct=None,
        terminal_pe_low=None,
        terminal_pe_high=None,
        years=None,
        required_return_pct=None,
        assumption_source=None,
        assumption_date=None,
    )
    assert unavailable["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert unavailable["confidence"] == "NOT_ASSESSED"
    result = calculate_tested_pe_range(
        current_eps=Decimal(12),
        current_price=Decimal(1200),
        growth_pct=Decimal(20),
        terminal_pe_low=Decimal(30),
        terminal_pe_high=Decimal(40),
        years=3,
        required_return_pct=Decimal(12),
        assumption_source="User selected scenario",
        assumption_date="2026-09-29",
    )
    assert result["status"] == "CALCULATED"
    assert result["supported_pe_range"]["lower"] == "37"
    assert result["supported_pe_range"]["upper"] == "49"
    assert result["verdict"] == "EXTREMELY_OVERVALUED"
    assert Decimal(
        result["calculation"]["required_terminal_pe"]
    ) == required_terminal_pe(
        Decimal(1200), Decimal(12), Decimal(20), 3, Decimal(12)
    ).quantize(
        Decimal("0.01")
    )
    assert result["assumptions"]["date"] == "2026-09-29"


def test_margin_units_and_ps_formula():
    units = margin_change_units(Decimal("43.028798"), Decimal("43.01732966"))
    assert Decimal(units["change_bps"]) == Decimal("1.146834")
    assert Decimal(units["change_percentage_points"]) == Decimal("0.011468")
    assert (Decimal(19604) / Decimal("481.280")).quantize(Decimal("0.001")) == Decimal(
        "40.733"
    )


def test_tested_pe_api_and_historical_dates(market_db):
    item = company(market_db)
    four_quarters(market_db, item)
    premature = quote(market_db, item, date(2026, 6, 15), close_price="100")
    current = quote(market_db, item, date(2026, 9, 28), close_price="100")
    history = historical_pe_summary(
        market_db, item.id, current, FinancialScope.CONSOLIDATED
    )
    assert history["available_points"] == 1
    assert history["one_year"] is None
    assert history["one_year_coverage"]["count"] == 1
    assert history["one_year_coverage"]["required_span_days"] == 120
    assert history["available_period"] is None
    assert history["first_stored_price_date"] == "2026-06-15"
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            automatic = client.get(
                "/api/companies/TEST/valuation/tested-pe?scope=CONSOLIDATED"
            )
            assert automatic.status_code == 200, automatic.text
            assessed = automatic.json()
            assert assessed["status"] == "CALCULATED"
            assert assessed["confidence"] == "LOW"
            assert len(assessed["scenarios"]) == 3
            assert all(
                row["source_type"] == "SCENARIO_ASSUMPTION"
                for row in assessed["scenarios"]
            )
            assert assessed["current_pe"] is not None
            assert assessed["current_price"] == "100.0000"
            assert assessed["ttm_eps"] is not None
            assert assessed["price_source"]["url"] == current.source_url
            assert assessed["historical_pe"]["one_year"] is None
            assert assessed["label"] == "SCENARIO — NOT FORECAST"
            assert assessed["supported_pe_range"]
            response = client.post(
                "/api/companies/TEST/valuation/tested-pe",
                json={
                    "scope": "CONSOLIDATED",
                    "eps_growth_pct": "20",
                    "terminal_pe_low": "30",
                    "terminal_pe_high": "40",
                    "years": 3,
                    "required_return_pct": "12",
                    "assumption_source": "User selected scenario",
                    "assumption_date": "2026-09-29",
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert data["status"] == "CALCULATED"
            assert data["historical_pe"]["one_year"] is None
            assert data["input_evidence"]
            assert data["market_date"] == "2026-09-28"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_historical_pe_median_uses_dated_comparable_earnings(market_db):
    item = company(market_db)
    for end in (
        date(2025, 6, 30),
        date(2025, 9, 30),
        date(2025, 12, 31),
        date(2026, 3, 31),
    ):
        add_quarter(market_db, item, end, date(2026, 4, 1))
    start = date(2026, 4, 2)
    for offset in range(0, 178, 8):
        quote(market_db, item, start + timedelta(days=offset), close_price="100")
    current = quote(market_db, item, date(2026, 9, 28), close_price="120")
    history = historical_pe_summary(
        market_db, item.id, current, FinancialScope.CONSOLIDATED
    )
    assert history["one_year"]["median"] == "25.00"
    assert history["one_year"]["low"] == "25.00"
    assert history["one_year"]["high"] == "30.00"
    assert history["three_year"] is None


def test_short_available_period_is_labeled_separately_from_1y_history(market_db):
    item = company(market_db)
    four_quarters(market_db, item)
    quote(market_db, item, date(2026, 9, 27), close_price="100")
    current = quote(market_db, item, date(2026, 9, 28), close_price="120")
    history = historical_pe_summary(
        market_db, item.id, current, FinancialScope.CONSOLIDATED
    )
    assert history["one_year"] is None
    assert history["one_year_coverage"]["count"] == 2
    assert history["one_year_coverage"]["span_days"] == 1
    assert history["available_period"] == {
        "median": "27.50",
        "low": "25.00",
        "high": "30.00",
        "count": 2,
        "first_date": "2026-09-27",
        "last_date": "2026-09-28",
    }
