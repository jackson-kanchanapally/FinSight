"""Deterministic investor evidence derived from compatible, validated facts."""

from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.financial import FinancialPeriod, FinancialStatus, FinancialUnit, MetricCode
from app.market.valuation import VERIFIED


def extend_results_evidence(
    db: Session, period: FinancialPeriod, registry: dict
) -> dict:
    def facts(p):
        return {
            f.metric_code: f
            for f in p.facts
            if f.status in VERIFIED
            and f.unit == (
                FinancialUnit.INR_PER_SHARE
                if f.metric_code in {MetricCode.BASIC_EPS, MetricCode.DILUTED_EPS}
                else FinancialUnit.INR_CRORE
            )
        }

    current = facts(period)
    priors = list(
        db.scalars(
            select(FinancialPeriod)
            .where(
                FinancialPeriod.company_id == period.company_id,
                FinancialPeriod.scope == period.scope,
                FinancialPeriod.period_type == period.period_type,
                FinancialPeriod.status == FinancialStatus.VALIDATED,
                FinancialPeriod.period_end < period.period_end,
            )
            .order_by(FinancialPeriod.period_end.desc())
        )
    )
    from datetime import timedelta

    previous = {
        "YOY": next(
            (
                p
                for p in priors
                if p.period_end
                == period.period_end.replace(year=period.period_end.year - 1)
            ),
            None,
        ),
        "QOQ": next(
            (
                p
                for p in priors
                if p.period_end == period.period_start - timedelta(days=1)
            ),
            None,
        ),
    }

    def add(code, value, inputs, prior, unit="PERCENT"):
        key = f"J{period.id}_{code}"
        for fact, owner in inputs:
            registry.setdefault(
                f"F{fact.id}",
                {
                    "kind": "FACT",
                    "metric_code": fact.metric_code.value,
                    "value": str(fact.value),
                    "unit": fact.unit.value,
                    "period_id": owner.id,
                    "period_end": owner.period_end.isoformat(),
                    "scope": owner.scope.value,
                    "document_id": fact.source_document_id,
                    "page": fact.source_page,
                    "document_url": f"/api/documents/{fact.source_document_id}/file#page={fact.source_page}",
                },
            )
        registry[key] = {
            "kind": "METRIC",
            "metric_code": code,
            "value": str(value.quantize(Decimal(".000001"))),
            "unit": unit,
            "period_id": period.id,
            "scope": period.scope.value,
            "comparison_period_id": prior.id if prior else None,
            "input_evidence_ids": [f"F{fact.id}" for fact, _ in inputs],
        }
        return key

    for basis, prior in previous.items():
        if not prior:
            continue
        old = facts(prior)
        for code, label in ((MetricCode.DILUTED_EPS, "EPS"),):
            if (
                code in current
                and code in old
                and old[code].value > 0
                and current[code].unit == old[code].unit
            ):
                add(
                    f"{label}_{basis}",
                    (current[code].value / old[code].value - 1) * 100,
                    [(current[code], period), (old[code], prior)],
                    prior,
                )
        components = (MetricCode.PBT, MetricCode.FINANCE_COST, MetricCode.DEPRECIATION)
        if all(code in current and code in old for code in components):
            latest, earlier = (
                sum((values[code].value for code in components), Decimal(0))
                for values in (current, old)
            )
            if earlier > 0:
                add(
                    f"EBITDA_{basis}",
                    (latest / earlier - 1) * 100,
                    [
                        (values[code], owner)
                        for values, owner in ((current, period), (old, prior))
                        for code in components
                    ],
                    prior,
                )

    bridge = []
    basis = "YOY" if previous["YOY"] else "QOQ"
    prior = previous[basis]
    if prior:
        old = facts(prior)
        codes = (
            MetricCode.REVENUE_FROM_OPERATIONS,
            MetricCode.OTHER_INCOME,
            MetricCode.TOTAL_EXPENSES,
            MetricCode.TOTAL_TAX_EXPENSE,
            MetricCode.PAT,
        )
        if all(
            code in current and code in old and current[code].unit == old[code].unit
            for code in codes
        ):
            for code in codes[:-1]:
                sign = (
                    -1
                    if code in {MetricCode.TOTAL_EXPENSES, MetricCode.TOTAL_TAX_EXPENSE}
                    else 1
                )
                change = sign * (current[code].value - old[code].value)
                key = add(
                    f"PAT_BRIDGE_{code.value}_{basis}",
                    change,
                    [(current[code], period), (old[code], prior)],
                    prior,
                    "INR_CRORE",
                )
                bridge.append(
                    {
                        "component": code.value,
                        "contribution_crore": str(change),
                        "evidence_ids": [key],
                    }
                )
            actual = current[MetricCode.PAT].value - old[MetricCode.PAT].value
            if abs(
                sum((Decimal(row["contribution_crore"]) for row in bridge), Decimal(0))
                - actual
            ) > Decimal(".03"):
                bridge = []
    ratios = []
    valuations = {
        item["metric_code"]: (key, item)
        for key, item in registry.items()
        if item["kind"] == "VALUATION"
    }
    for numerator, denominator, label in (
        ("TTM_OPERATING_CASH_FLOW", "TTM_PAT", "OCF / PAT"),
        ("TTM_FREE_CASH_FLOW", "TTM_PAT", "FCF / PAT"),
        ("TTM_OPERATING_CASH_FLOW", "TTM_EBITDA", "OCF / EBITDA"),
    ):
        a, b = valuations.get(numerator), valuations.get(denominator)
        if a and b and Decimal(b[1]["value"]) > 0:
            ratios.append(
                {
                    "label": label,
                    "value": str(
                        (Decimal(a[1]["value"]) / Decimal(b[1]["value"])).quantize(
                            Decimal(".01")
                        )
                    ),
                    "evidence_ids": [a[0], b[0]],
                    "formula": f"{numerator} / {denominator}",
                }
            )
    gaps = []
    if not ratios:
        gaps.append(
            "Cash conversion: verified OCF and FCF are unavailable for a compatible earnings period."
        )
    gaps.append(
        "Exceptional items and working-capital movements are not separately verified; no causal efficiency claim is made."
    )
    if bridge:
        pre_tax = sum(
            (Decimal(row["contribution_crore"]) for row in bridge[:-1]), Decimal(0)
        )
        tax = Decimal(bridge[-1]["contribution_crore"])
        if pre_tax > 0 and tax < -pre_tax:
            text = f"Pre-tax profit improved {basis}, but the higher tax expense more than offset that improvement and reduced PAT."
        elif pre_tax > 0 and tax > 0:
            text = f"Both higher pre-tax profit and lower tax contributed to PAT growth {basis}; the profit increase is not entirely operating improvement."
        elif pre_tax < 0 and tax > -pre_tax:
            text = f"Lower tax more than offset weaker pre-tax profit {basis}; PAT growth therefore overstates the underlying pre-tax improvement."
        elif pre_tax < 0:
            text = f"Lower pre-tax profit drove the weaker {basis} earnings comparison; the bridge separates income, expenses and tax."
        else:
            text = f"The {basis} PAT movement reconciles to the income, expense and tax contributions below."
    else:
        text = "A complete comparable income and tax bridge is unavailable; the operating cause of PAT cannot be established."
    return {
        "text": text,
        "basis": basis if bridge else None,
        "pat_bridge": bridge,
        "cash_conversion": ratios,
        "research_gaps": gaps,
        "confidence": "MEDIUM" if bridge and ratios else "LOW",
        "evidence_ids": [key for row in bridge for key in row["evidence_ids"]],
    }
