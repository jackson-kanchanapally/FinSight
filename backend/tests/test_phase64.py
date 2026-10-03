"""Investor engine boundaries found in the Phase 6.4 audit."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ai.analyst.context_builder import build_context
from app.database import get_db
from app.main import app
from app.market.expectations import latest_quarter_eps_growth
from app.models.financial import (
    FactMethod,
    FactStatus,
    FinancialFact,
    FinancialMetric,
    FinancialScope,
    FinancialUnit,
    MetricCode,
)
from app.models.market import ValuationAssumptions
from app.services.financials.service import calculate_company_metrics
from app.services.investor_evidence import extend_results_evidence
from app.services.investor_verdict import classify_valuation, results_verdict
from tests.test_market_valuation import add_quarter, company, market_db
from tests.test_phase62 import _registry


def test_wrong_unit_never_becomes_ebitda_or_eps_growth(market_db):
    issuer = company(market_db)
    add_quarter(market_db, issuer, date(2025, 6, 30), date(2025, 7, 20))
    current = add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20))
    for fact in current.facts:
        if fact.metric_code == MetricCode.PBT:
            fact.unit = FinancialUnit.INR_PER_SHARE
        if fact.metric_code == MetricCode.DILUTED_EPS:
            fact.unit = FinancialUnit.INR_CRORE
    market_db.commit()

    calculate_company_metrics(market_db, issuer.id)
    assert not market_db.scalar(select(FinancialMetric).where(
        FinancialMetric.financial_period_id == current.id,
        FinancialMetric.metric_code == MetricCode.EBITDA))
    registry = {}
    extend_results_evidence(market_db, current, registry)
    assert f"J{current.id}_EBITDA_YOY" not in registry
    assert f"J{current.id}_EPS_YOY" not in registry
    assert latest_quarter_eps_growth(market_db, issuer.id, FinancialScope.CONSOLIDATED,
                                    date(2026, 9, 29)) is None


def test_recalculation_removes_metrics_whose_input_needs_review(market_db):
    issuer = company(market_db)
    add_quarter(market_db, issuer, date(2025, 6, 30), date(2025, 7, 20))
    current = add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20))
    calculate_company_metrics(market_db, issuer.id)
    market_db.refresh(current, ['metrics'])
    existing = {m.metric_code: m.id for m in current.metrics}
    assert MetricCode.PAT_MARGIN in existing and MetricCode.PAT_YOY in existing
    assert MetricCode.REVENUE_YOY in existing

    pat = next(f for f in current.facts if f.metric_code == MetricCode.PAT)
    pat.status = FactStatus.NEEDS_REVIEW
    market_db.commit()
    calculate_company_metrics(market_db, issuer.id)
    market_db.refresh(current, ['metrics'])
    remaining = {m.metric_code: m.id for m in current.metrics}
    assert MetricCode.PAT_MARGIN not in remaining
    assert MetricCode.PAT_YOY not in remaining
    assert remaining[MetricCode.REVENUE_YOY] == existing[MetricCode.REVENUE_YOY]


def test_per_share_income_statement_cannot_be_labeled_crores(market_db):
    issuer = company(market_db)
    periods = [add_quarter(market_db, issuer, end, published) for end, published in (
        (date(2025, 6, 30), date(2025, 7, 20)),
        (date(2026, 6, 30), date(2026, 7, 20)),
    )]
    for period in periods:
        for code in (MetricCode.OTHER_INCOME, MetricCode.TOTAL_EXPENSES,
                     MetricCode.TOTAL_TAX_EXPENSE):
            market_db.add(FinancialFact(
                financial_period_id=period.id, metric_code=code, value=Decimal('1'),
                unit=FinancialUnit.INR_PER_SHARE, source_document_id=period.source_document_id,
                source_label=code.value, source_column_date=period.period_end,
                extraction_method=FactMethod.STRUCTURED, status=FactStatus.VERIFIED_STRUCTURED))
        for fact in period.facts:
            if fact.metric_code in (MetricCode.REVENUE_FROM_OPERATIONS, MetricCode.PAT):
                fact.unit = FinancialUnit.INR_PER_SHARE
    market_db.commit()
    registry = {}
    quality = extend_results_evidence(market_db, periods[-1], registry)
    assert quality['pat_bridge'] == []
    assert not any(key.startswith(f"J{periods[-1].id}_PAT_BRIDGE") for key in registry)


def test_analysis_rejects_metric_with_other_scope_input(market_db):
    issuer = company(market_db)
    current = add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20))
    other = add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20),
                        scope=FinancialScope.STANDALONE)
    revenue = lambda p: next(f for f in p.facts if f.metric_code == MetricCode.REVENUE_FROM_OPERATIONS)
    metric = FinancialMetric(financial_period_id=current.id, metric_code=MetricCode.REVENUE_QOQ,
        value=Decimal('999'), unit=FinancialUnit.PERCENT,
        input_fact_ids=[revenue(current).id, revenue(other).id], comparison_period_id=other.id)
    market_db.add(metric)
    market_db.commit()
    context = build_context(market_db, issuer.name, issuer.symbol, current)
    assert f"M{metric.id}" not in context.registry
    assert f"F{revenue(other).id}" not in context.registry
    assert context.investor_report['results']['verdict'] == 'INSUFFICIENT_EVIDENCE'


def test_analysis_rejects_comparison_without_prior_fact(market_db):
    issuer = company(market_db)
    current = add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20))
    other = add_quarter(market_db, issuer, date(2026, 6, 30), date(2026, 7, 20),
                        scope=FinancialScope.STANDALONE)
    revenue = next(f for f in current.facts if f.metric_code == MetricCode.REVENUE_FROM_OPERATIONS)
    metric = FinancialMetric(financial_period_id=current.id, metric_code=MetricCode.REVENUE_YOY,
        value=Decimal('999'), unit=FinancialUnit.PERCENT,
        input_fact_ids=[revenue.id], comparison_period_id=other.id)
    market_db.add(metric)
    market_db.commit()
    context = build_context(market_db, issuer.name, issuer.symbol, current)
    assert f"M{metric.id}" not in context.registry


def test_scenario_names_follow_calculated_order_and_do_not_persist_invalid_set(market_db):
    issuer = company(market_db)
    app.dependency_overrides[get_db] = lambda: market_db
    try:
        with TestClient(app) as client:
            scenarios = [
                {'name': name, 'eps_growth_pct': str(growth), 'terminal_pe': str(pe),
                 'years': 3, 'required_return_pct': '12', 'source': 'User sensitivity'}
                for name, growth, pe in (
                    ('CONSERVATIVE', 50, 60), ('BASE', 15, 30), ('OPTIMISTIC', 5, 10))
            ]
            response = client.post(f'/api/companies/{issuer.symbol}/valuation/tested-pe',
                                   json={'scenarios': scenarios})
            assert response.status_code == 422
            assert 'conservative' in response.text.lower()
            assert market_db.scalar(select(ValuationAssumptions)) is None
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_business_and_valuation_verdicts_can_diverge_both_ways():
    assert results_verdict(_registry(30, 35, 150, cash=120))['verdict'] == 'VERY_POSITIVE'
    assert classify_valuation(Decimal(150), Decimal(20), Decimal(40)) == 'EXTREMELY_OVERVALUED'
    assert results_verdict(_registry(-30, -35))['verdict'] == 'VERY_NEGATIVE'
    assert classify_valuation(Decimal(5), Decimal(20), Decimal(40)) == 'EXTREMELY_UNDERVALUED'
