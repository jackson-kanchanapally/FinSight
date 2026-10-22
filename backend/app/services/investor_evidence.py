"""Deterministic investor evidence derived from compatible, validated facts."""

from decimal import Decimal
import re
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.financial import FinancialPeriod, FinancialStatus, FinancialUnit, MetricCode
from app.market.valuation import VERIFIED
from app.services.financials.calculator import growth, reconciliation_tolerance, exceptional_effect
from app.services.financials.period import comparable_period


def extend_results_evidence(
    db: Session, period: FinancialPeriod, registry: dict
) -> dict:
    def facts(p):
        return {
            f.metric_code: f
            for f in p.facts
            if f.status in VERIFIED
            and f.source_column_date == p.period_end
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
                if comparable_period(period, p, "YOY")
            ),
            None,
        ),
        "QOQ": next(
            (
                p
                for p in priors
                if comparable_period(period, p, "QOQ")
            ),
            None,
        ),
    }

    def add(code, value, inputs, prior=None, unit="PERCENT", formula=None):
        existing = next((key for key, row in registry.items() if row.get("kind") == "METRIC"
            and row.get("period_id") == period.id and row.get("metric_code") == code
            and abs(Decimal(row["value"])-value) <= Decimal('.000001')), None)
        if existing:
            if formula:
                registry[existing].setdefault("formula", formula)
            return existing
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
                    "source_label": fact.source_label,
                    "extraction_method": fact.extraction_method.value,
                    "rounding_tolerance": str(fact.rounding_tolerance) if fact.rounding_tolerance is not None else None,
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
            "formula": formula or ("(current - previous) / abs(previous) × 100" if prior else code),
        }
        return key

    for basis, prior in previous.items():
        if not prior:
            continue
        old = facts(prior)
        pbt, old_pbt, tax, old_tax = (values.get(code) for values, code in (
            (current, MetricCode.PBT), (old, MetricCode.PBT),
            (current, MetricCode.TOTAL_TAX_EXPENSE), (old, MetricCode.TOTAL_TAX_EXPENSE)))
        if all((pbt, old_pbt, tax, old_tax)) and pbt.value > 0 and old_pbt.value > 0:
            add("EFFECTIVE_TAX_RATE_CHANGE_PP_"+basis,
                (tax.value/pbt.value-old_tax.value/old_pbt.value)*100,
                [(tax, period), (pbt, period), (old_tax, prior), (old_pbt, prior)], prior,
                "PERCENTAGE_POINTS", "current tax / PBT × 100 - prior tax / PBT × 100")
        if tax and old_tax:
            add("TAX_CHANGE_"+basis, tax.value-old_tax.value, [(tax, period), (old_tax, prior)], prior,
                "INR_CRORE", "current tax expense - prior tax expense")
        for code, label in ((MetricCode.DILUTED_EPS, "EPS"), (MetricCode.BASIC_EPS, "BASIC_EPS"),
                            (MetricCode.PBT, "PBT"), (MetricCode.TOTAL_TAX_EXPENSE, "TAX"),
                            (MetricCode.REVENUE_FROM_OPERATIONS, "REVENUE"), (MetricCode.PAT, "PAT")):
            if (
                code in current
                and code in old
                and old[code].value != 0
                and current[code].unit == old[code].unit
            ):
                add(
                    f"{label}_{basis}",
                    growth(current[code].value, old[code].value),
                    [(current[code], period), (old[code], prior)],
                    prior,
                )
        components = (MetricCode.PBT, MetricCode.FINANCE_COST, MetricCode.DEPRECIATION)
        if MetricCode.EBITDA in current and MetricCode.EBITDA in old:
            a, b = current[MetricCode.EBITDA], old[MetricCode.EBITDA]
            if b.value != 0:
                add(f"EBITDA_{basis}", growth(a.value, b.value), [(a, period), (b, prior)], prior)
        elif MetricCode.EBITDA not in current and MetricCode.EBITDA not in old and all(code in current and code in old for code in components):
            latest, earlier = (
                sum((values[code].value for code in components), Decimal(0))
                for values in (current, old)
            )
            if earlier != 0:
                add(
                    f"EBITDA_{basis}",
                    growth(latest, earlier),
                    [
                        (values[code], owner)
                        for values, owner in ((current, period), (old, prior))
                        for code in components
                    ],
                    prior,
                )

    observations, unavailable = {}, {}
    def observe(label, code, value, inputs, unit="INR_CRORE", formula=None):
        key = add(code, value, [(f, period) for f in inputs], unit=unit, formula=formula)
        observations[label] = {"value": str(value), "unit": unit, "evidence_ids": [key],
                               "formula": registry[key]["formula"], "basis": "CALCULATED_METRIC"}
    pbt, tax, pat = (current.get(code) for code in (MetricCode.PBT, MetricCode.TOTAL_TAX_EXPENSE, MetricCode.PAT))
    revenue = current.get(MetricCode.REVENUE_FROM_OPERATIONS)
    if pbt and tax and pbt.value > 0:
        observe("effective_tax_rate", "EFFECTIVE_TAX_RATE", tax.value/pbt.value*100, [tax, pbt],
                "PERCENT", "tax expense / positive PBT × 100")
    else:
        unavailable["effective_tax_rate"] = "Positive PBT and compatible tax expense are required"
    if pat and revenue and revenue.value > 0:
        observe("pat_margin", "PAT_MARGIN", pat.value/revenue.value*100, [pat, revenue], "PERCENT", "PAT / revenue × 100")
    for code in (MetricCode.EBITDA, MetricCode.EBIT):
        if code in current:
            f = current[code]
            add(code.value, f.value, [(f, period)], unit="INR_CRORE", formula="Reported source fact")
            if revenue and revenue.value > 0:
                observe(code.value.lower()+"_margin", code.value+"_MARGIN", f.value/revenue.value*100,
                        [f, revenue], "PERCENT", f"reported {code.value} / revenue × 100")
    components = [current.get(code) for code in (MetricCode.PBT, MetricCode.FINANCE_COST, MetricCode.DEPRECIATION)]
    if all(components) and MetricCode.EBITDA not in current:
        value = sum((f.value for f in components), Decimal(0))
        observe("ebitda_proxy", "EBITDA_PROXY", value, components,
                formula="PBT + finance costs + depreciation; includes other income and exceptional items")
        if revenue and revenue.value > 0:
            observe("ebitda_proxy_margin", "EBITDA_PROXY_MARGIN", value/revenue.value*100,
                    components+[revenue], "PERCENT", "EBITDA proxy / revenue × 100")
    ocf, capex, debt, cash, equity = (current.get(code) for code in (
        MetricCode.OPERATING_CASH_FLOW, MetricCode.CAPITAL_EXPENDITURE, MetricCode.TOTAL_DEBT,
        MetricCode.CASH_AND_CASH_EQUIVALENTS, MetricCode.TOTAL_EQUITY))
    if ocf and pat and pat.value > 0:
        observe("cash_conversion", "OCF_PAT", ocf.value/pat.value, [ocf, pat], "MULTIPLE", "same-period OCF / positive PAT")
    else:
        unavailable["cash_conversion"] = "Compatible same-period OCF and positive PAT are unavailable"
    signed_outflow = capex and capex.value < 0 and re.search(r"purchase|capital expenditure", capex.source_label, re.I)
    if ocf and capex and (capex.value >= 0 or signed_outflow):
        observe("free_cash_flow", "FREE_CASH_FLOW", ocf.value+capex.value if signed_outflow else ocf.value-capex.value,
                [ocf, capex], formula="OCF + reported signed capex outflow" if signed_outflow else "OCF - positive capital expenditure")
    else:
        unavailable["free_cash_flow"] = "Compatible OCF and explicitly positive capex outflow are required"
    if debt and cash:
        observe("net_debt", "NET_DEBT", debt.value-cash.value, [debt, cash], formula="total debt - cash and cash equivalents")
    else:
        unavailable["net_debt"] = "Verified total debt and cash on the same balance-sheet date are required"
    if debt and equity and equity.value > 0:
        observe("debt_equity", "DEBT_EQUITY", debt.value/equity.value, [debt, equity], "MULTIPLE", "total debt / positive total equity")
    else:
        unavailable["debt_equity"] = "Verified debt and positive total equity are required"
    reconciliation = {"status": "UNASSESSED", "reason": "PBT, tax and total PAT are required"}
    if pbt and tax and pat:
        residual = pbt.value-tax.value-pat.value
        tolerance = reconciliation_tolerance([pbt, tax, pat])
        reconciliation = {"status": "RECONCILED" if abs(residual) <= tolerance else "CONFLICTING",
            "residual_crore": str(residual), "formula": "PBT - tax - total PAT",
            "rounding_tolerance_crore": str(tolerance),
            "evidence_ids": [f"F{f.id}" for f in (pbt, tax, pat)]}
        for f in (pbt, tax, pat):
            add("RECONCILIATION_"+f.metric_code.value, f.value, [(f, period)], unit="INR_CRORE")
        if residual:
            observe("tax_pat_reconciliation_residual", "TAX_PAT_RECONCILIATION_RESIDUAL", residual,
                    [pbt, tax, pat], formula="PBT - tax expense - total PAT")
    attributable, minority = current.get(MetricCode.PAT_ATTRIBUTABLE), current.get(MetricCode.NON_CONTROLLING_INTEREST)
    if attributable and minority and pat:
        reconciliation["attributable_status"] = "RECONCILED" if abs(pat.value-attributable.value-minority.value) <= reconciliation_tolerance([pat, attributable, minority]) else "CONFLICTING"
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
            proposals = []
            for code in codes[:-1]:
                sign = (
                    -1
                    if code in {MetricCode.TOTAL_EXPENSES, MetricCode.TOTAL_TAX_EXPENSE}
                    else 1
                )
                change = sign * (current[code].value - old[code].value)
                proposals.append((code, change))
            actual = current[MetricCode.PAT].value - old[MetricCode.PAT].value
            tolerance = reconciliation_tolerance([values[code] for values in (current, old) for code in codes])
            if abs(sum((value for _, value in proposals), Decimal(0))-actual) > tolerance and all(MetricCode.EXCEPTIONAL_ITEMS in values for values in (current, old)):
                a, b = current[MetricCode.EXCEPTIONAL_ITEMS], old[MetricCode.EXCEPTIONAL_ITEMS]
                effects = [exceptional_effect(f.value, f.source_label) for f in (a, b)]
                if all(value is not None for value in effects):
                    proposals.insert(-1, (MetricCode.EXCEPTIONAL_ITEMS, effects[0]-effects[1]))
            reconciles = abs(sum((value for _, value in proposals), Decimal(0))-actual) <= tolerance
            if not reconciles:
                unavailable["pat_bridge"] = "Income, expense and tax contributions do not reconcile to total PAT; exceptional/attributable items may be missing"
            for code, change in proposals if reconciles else []:
                key = add(
                    f"PAT_BRIDGE_{code.value}_{basis}",
                    change,
                    [(current[code], period), (old[code], prior)],
                    prior,
                    "INR_CRORE",
                    "signed current component - signed previous component; reconciled to total PAT change",
                )
                bridge.append(
                    {
                        "component": code.value,
                        "contribution_crore": str(change),
                        "evidence_ids": [key],
                    }
                )
            if reconciles:
                delta_tax = current[MetricCode.TOTAL_TAX_EXPENSE].value-old[MetricCode.TOTAL_TAX_EXPENSE].value
                key = add("TAX_CHANGE_"+basis, delta_tax,
                          [(current[MetricCode.TOTAL_TAX_EXPENSE], period), (old[MetricCode.TOTAL_TAX_EXPENSE], prior)],
                          prior, "INR_CRORE", "current tax - prior tax")
                observations["tax_change"] = {"value": str(delta_tax), "unit": "INR_CRORE", "evidence_ids": [key]}
        if not bridge:
            # A direct PBT/tax bridge is still useful when operating expense detail is missing.
            needed = (MetricCode.PBT, MetricCode.TOTAL_TAX_EXPENSE, MetricCode.PAT)
            if all(code in values for values in (current, old) for code in needed):
                valid = all(abs(values[MetricCode.PBT].value-values[MetricCode.TOTAL_TAX_EXPENSE].value-values[MetricCode.PAT].value)
                    <= reconciliation_tolerance([values[code] for code in needed]) for values in (current, old))
                if valid:
                    for code, sign in ((MetricCode.PBT, 1), (MetricCode.TOTAL_TAX_EXPENSE, -1)):
                        value = sign*(current[code].value-old[code].value)
                        key = add(f"PAT_BRIDGE_{code.value}_{basis}", value,
                            [(current[code], period), (old[code], prior)], prior, "INR_CRORE",
                            "signed current component - signed previous component; PBT - tax = total PAT")
                        bridge.append({"component": code.value, "contribution_crore": str(value), "evidence_ids": [key]})
                    unavailable["detailed_operating_bridge"] = "Only the PBT/tax bridge reconciles; detailed operating contributions remain unassessed"
    ratios = []
    if "cash_conversion" in observations:
        ratios.append({"label": "OCF / PAT", **observations["cash_conversion"]})
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
    gaps.append("Working-capital movements are not separately verified; no causal efficiency claim is made."
        if MetricCode.EXCEPTIONAL_ITEMS in current else
        "Exceptional items and working-capital movements are not separately verified; no causal efficiency claim is made.")
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
        "observations": observations,
        "unassessed": unavailable,
        "reconciliation": reconciliation,
    }
