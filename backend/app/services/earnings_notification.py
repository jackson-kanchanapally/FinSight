"""Present the existing financial analyst's facts and validated conclusions."""
from decimal import Decimal
from sqlalchemy import select
from app.models.analysis import AIAnalysis
from app.models.company import Company
from app.models.event import CompanyEvent
from app.models.financial import MetricCode
from app.schemas.earnings_notification import EarningsMetric, EarningsNotification, EvidenceText
from app.services.report_links import signed_link
from app.services.result_sources import freshness_view, verification_view


def build_notification(db, period, analysis=None, *, delivery_id=None):
    from app.ai.analyst.context_builder import build_context
    from app.ai.analyst.validator import InvalidAnalysis, validate_review_item
    from app.ai.analyst.schemas import ReviewItem
    company = db.get(Company, period.company_id)
    if analysis is None:
        analysis = db.scalar(select(AIAnalysis).where(AIAnalysis.financial_period_id == period.id,
            AIAnalysis.status.in_(["SUCCESS", "VERIFIED_FALLBACK"])).order_by(AIAnalysis.id.desc()).limit(1))
    if analysis and analysis.financial_period_id != period.id:
        raise ValueError("ANALYSIS_PERIOD_MISMATCH")
    # Build from current, validated evidence; reuse generated prose only after validating it.
    context = build_context(db, company.name, company.symbol, period)
    registry, report = context.registry, context.investor_report
    output = (analysis.structured_output or {}) if analysis else {}
    earnings = report["earnings_quality"]
    verification, freshness = verification_view(db, period), freshness_view(db, period)
    confidence = report["evidence_confidence"]
    current = {row["metric_code"]: (key, row) for key, row in registry.items()
               if row.get("period_id") == period.id and row.get("kind") in {"FACT", "METRIC"}}

    def metric(code, label=None):
        found = current.get(code)
        if code in {"EBITDA", "EBIT", "DILUTED_EPS", "BASIC_EPS"}:
            found = next(((key, row) for key, row in registry.items() if row.get("kind") == "FACT"
                and row.get("period_id") == period.id and row.get("metric_code") == code), found)
        if not found:
            return None
        key, row = found
        reported = row["kind"] == "FACT" and row.get("extraction_method") != "CALCULATED"
        return EarningsMetric(code=code, label=label or code.replace("_", " ").title(),
            value=Decimal(row["value"]), unit=row["unit"], evidence_ids=[key], formula=row.get("formula"),
            basis="REPORTED_FACT" if reported else "CALCULATED_METRIC")

    reported_ebitda = any(row.get("kind") == "FACT" and row.get("metric_code") == "EBITDA"
        and row.get("period_id") == period.id and row.get("extraction_method") != "CALCULATED" for row in registry.values())
    snapshot = []
    for code, label, trend in [("REVENUE_FROM_OPERATIONS", "Revenue", "REVENUE"),
            ("EBITDA_PROXY", "EBITDA proxy (includes other income)", "EBITDA"),
            ("PAT", "PAT (total profit)", "PAT")]:
        if code == "EBITDA_PROXY" and reported_ebitda:
            code, label = "EBITDA", "Reported EBITDA"
        row = metric(code, label)
        if row:
            row.qoq, row.yoy = metric(trend+"_QOQ"), metric(trend+"_YOY")
            snapshot.append(row)
    additional = [row for code in ["EBITDA_PROXY_MARGIN", "EBITDA_MARGIN", "PAT_MARGIN", "PBT", "TOTAL_TAX_EXPENSE",
        "EFFECTIVE_TAX_RATE", "EFFECTIVE_TAX_RATE_CHANGE_PP_QOQ", "EFFECTIVE_TAX_RATE_CHANGE_PP_YOY", "TAX_CHANGE_QOQ", "TAX_CHANGE_YOY",
        "DILUTED_EPS", "BASIC_EPS", "OPERATING_CASH_FLOW", "FREE_CASH_FLOW", "OCF_PAT",
        "TOTAL_DEBT", "CASH_AND_CASH_EQUIVALENTS", "NET_DEBT", "DEBT_EQUITY", "EBIT", "EXCEPTIONAL_ITEMS",
        "PAT_ATTRIBUTABLE", "NON_CONTROLLING_INTEREST"] if (code != "EBITDA_MARGIN" or reported_ebitda) and (row := metric(code))]
    verdict = EvidenceText(text=report["results"]["verdict"].replace("_", " ").title() + ": "
        + "; ".join(report["results"].get("reasons", [])) + ". " + earnings["text"],
        evidence_ids=list(dict.fromkeys(report["results"]["evidence_ids"] + earnings.get("evidence_ids", []))),
                           basis="DETERMINISTIC_INTERPRETATION")
    accepted_ai = False
    for row in (output.get("quarterly_review") or {}).get("quarterly_investor_view", []):
        if not analysis or analysis.status != "SUCCESS":
            break
        try:
            item = ReviewItem.model_validate(row)
            validate_review_item(item, "quarterly_investor_view", registry, period.period_end.year)
            verdict = EvidenceText(text=item.text, evidence_ids=item.evidence_ids, basis="AI_INTERPRETATION")
            accepted_ai = True
            break
        except (InvalidAnalysis, ValueError, TypeError):
            continue
    contradictions = []
    residual = earnings.get("reconciliation", {}).get("residual_crore")
    if residual is not None and Decimal(residual) != 0:
        row = earnings.get("observations", {}).get("tax_pat_reconciliation_residual", {})
        contradictions.append(EvidenceText(text="PBT/tax/PAT has a nonzero reconciliation residual; inspect the source precision and rounding tolerance.",
            evidence_ids=row.get("evidence_ids", []), basis="DETERMINISTIC_INTERPRETATION"))
    if verification["status"] == "CONFLICTING" or earnings.get("reconciliation", {}).get("status") == "CONFLICTING" or earnings.get("reconciliation", {}).get("attributable_status") == "CONFLICTING":
        contradictions.append(EvidenceText(text="Source or tax/PAT reconciliation conflicts remain unresolved; treat this result as provisional.", basis="MISSING_EVIDENCE"))
    if freshness["status"] == "POTENTIALLY_STALE":
        contradictions.append(EvidenceText(text="A newer detected or stored result exists; this report may be stale.", basis="MISSING_EVIDENCE"))
    drivers = [EvidenceText(text=f"{row['component'].replace('_', ' ').title()}: ₹{Decimal(row['contribution_crore']):+,.2f} crore contribution to PAT change ({earnings['basis']}).",
        evidence_ids=row["evidence_ids"], basis="CALCULATED_METRIC") for row in earnings.get("pat_bridge", [])]
    if not drivers:
        drivers = [EvidenceText(text=earnings.get("text", "Comparable earnings drivers are unavailable"), basis="MISSING_EVIDENCE")]
    if earnings.get("pat_bridge"):
        drivers.append(EvidenceText(text=earnings["text"], evidence_ids=earnings["evidence_ids"], basis="DETERMINISTIC_INTERPRETATION"))
    positives, negatives = [], []
    for code, label in [("REVENUE_YOY", "Revenue YoY"), ("PAT_YOY", "PAT YoY"), ("REVENUE_QOQ", "Revenue QoQ"), ("PAT_QOQ", "PAT QoQ")]:
        row = metric(code)
        if row:
            item = EvidenceText(text=f"{label}: {row.value:+.2f}%.", evidence_ids=row.evidence_ids, basis="CALCULATED_METRIC")
            (positives if row.value >= 0 else negatives).append(item)
    watch = []
    for code, label in [("PAT", "Next comparable total PAT versus the current baseline"),
                        ("EFFECTIVE_TAX_RATE", "Next comparable effective tax rate versus the current baseline"),
                        ("OCF_PAT", "Next comparable OCF/PAT cash conversion versus the current baseline")]:
        row = metric(code)
        if row:
            suffix = "%" if row.unit == "PERCENT" else "×" if row.unit == "MULTIPLE" else " crore"
            watch.append(EvidenceText(text=f"{label}: {row.value:.2f}{suffix}.", evidence_ids=row.evidence_ids, basis="DETERMINISTIC_INTERPRETATION"))
    if len(watch) < 3:
        watch.append(EvidenceText(text="Obtain same-period operating cash flow and compare OCF/PAT; cash conversion is currently unassessed." if "OCF_PAT" not in current else "Obtain a compatible YoY revenue and PAT pair before judging durability.", basis="MISSING_EVIDENCE"))
    risks = []
    for row in (output.get("quarterly_review") or {}).get("key_risks", []):
        try:
            item = ReviewItem.model_validate(row)
            validate_review_item(item, "key_risks", registry, period.period_end.year)
            risks.append(EvidenceText(text=item.text, evidence_ids=item.evidence_ids, basis="AI_INTERPRETATION"))
        except (InvalidAnalysis, ValueError, TypeError):
            continue
    if not risks:
        risks = [EvidenceText(text=reason, basis="MISSING_EVIDENCE") for reason in report.get("research_gaps", [])[:3]]
    management = [EvidenceText(text=row["source_quote"], evidence_ids=[row["evidence_id"]],
        basis="MANAGEMENT_CLAIM") for row in report.get("review_packet", {}).get("material_statements", [])[:2]
        if row.get("source_quote") and registry.get(row.get("evidence_id"), {}).get("kind") == "MANAGEMENT_STATEMENT"]
    valuation_ids = [key for key, row in registry.items() if row.get("kind") == "VALUATION"
                     and row.get("metric_code") in {"PE_TTM", "PS_TTM", "EV_EBITDA_TTM"}]
    assessed = bool(valuation_ids)
    valuation = EvidenceText(text="Observed valuation multiples are calculated from verified financial facts and a dated price. Fair value remains unassessed without supported growth or comparable valuation evidence." if assessed else
        "Unassessed: " + "; ".join(report.get("tested_pe", {}).get("missing", []) or ["a compatible dated price and verified earnings base are unavailable"]),
        evidence_ids=valuation_ids if assessed else [], basis="DETERMINISTIC_INTERPRETATION" if assessed else "MISSING_EVIDENCE")
    if assessed:
        values = [f"{registry[key]['metric_code']}: {Decimal(registry[key]['value']):.2f}×" for key in valuation_ids]
        valuation.text = "; ".join(values) + f" (price date: {report.get('tested_pe', {}).get('market_date') or 'unavailable'}). " + valuation.text
    missing = dict(earnings.get("unassessed", {}))
    if not assessed:
        missing["valuation"] = valuation.text
    else:
        missing["fair_value"] = "Observed multiples do not establish fair value; no user-entered growth expectations are required or treated as verified forecasts"
    if not any(row.code in {"DILUTED_EPS", "BASIC_EPS"} for row in additional):
        missing["eps"] = "A verified compatible per-share EPS fact is unavailable"
    for code in ("REVENUE", "PAT"):
        for basis in ("QOQ", "YOY"):
            if code+"_"+basis not in current:
                missing[code.lower()+"_"+basis.lower()] = "A compatible same-basis comparison with a nonzero denominator is unavailable"
    return EarningsNotification(company_id=company.id, company_symbol=company.symbol, company_name=company.name,
        period_id=period.id, reporting_period=f"Q{period.quarter} {period.fiscal_year}" if period.quarter else f"{period.period_type.value} {period.fiscal_year}",
        period_end=period.period_end, reporting_basis=period.scope.value, analysis_id=analysis.id if analysis else None,
        ai_status="SUCCESS" if accepted_ai else "VERIFIED_FALLBACK", snapshot=snapshot, additional_metrics=additional,
        verdict=verdict, positives=positives, negatives=negatives, risks=risks, management_highlights=management,
        drivers=drivers, contradictions=contradictions,
        watch_next=watch[:3], valuation=valuation, confidence=confidence, previous_thesis=report["previous_thesis"],
        verification=verification, freshness=freshness, unassessed=missing,
        full_report=signed_link(period.id, delivery_id=delivery_id), evidence_registry=registry)
