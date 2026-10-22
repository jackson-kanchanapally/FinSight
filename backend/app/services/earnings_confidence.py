"""Evidence-quality rubric, not a probability or a calibrated forecast score."""
from datetime import datetime, timezone, timedelta


def assess_confidence(period, registry, earnings, verification, freshness, comparison_sources_verified=False):
    facts = [row for row in registry.values() if row.get("kind") == "FACT" and row.get("period_id") == period.id]
    codes = {row.get("metric_code") for row in facts}
    independent = {row.get("metric_code") for row in facts if row.get("extraction_method") != "CALCULATED"}
    trends = {row.get("metric_code") for row in registry.values() if row.get("kind") == "METRIC"}
    checks = {
        "core_facts": {"REVENUE_FROM_OPERATIONS", "PAT", "PBT", "TOTAL_TAX_EXPENSE"} <= codes,
        "independent_profit_check": {"PAT", "PBT"} <= independent,
        "authoritative_source": verification["status"] == "VERIFIED",
        "tax_pat_reconciled": earnings.get("reconciliation", {}).get("status") == "RECONCILED"
            and earnings.get("reconciliation", {}).get("attributable_status") != "CONFLICTING",
        "comparable_yoy": {"REVENUE_YOY", "PAT_YOY"} <= trends,
        "comparable_qoq": {"REVENUE_QOQ", "PAT_QOQ"} <= trends,
        "cash_conversion": bool(earnings.get("cash_conversion")),
        "freshness_not_challenged": freshness["status"] != "POTENTIALLY_STALE",
        "no_source_conflict": verification["status"] != "CONFLICTING",
        "comparison_sources_verified": comparison_sources_verified,
        "recent_source_check": any(row.get("status") == "SUCCESS" and row.get("last_success_at")
            and datetime.fromisoformat(row["last_success_at"]) >= datetime.now(timezone.utc)-timedelta(hours=24)
            for row in freshness.get("source_checks", [])),
    }
    reasons = {
        "core_facts": "Core verified financial rows are incomplete",
        "independent_profit_check": "PBT/PAT reconciliation relies on derived cells rather than independently readable totals",
        "authoritative_source": "Issuer authenticity is not fully verified against authoritative evidence",
        "tax_pat_reconciled": "PBT, taxes and total PAT are not reconciled",
        "comparable_yoy": "Compatible revenue and PAT YoY comparisons are unavailable",
        "comparable_qoq": "Compatible revenue and PAT QoQ comparisons are unavailable",
        "cash_conversion": "Compatible cash-conversion evidence is unavailable",
        "freshness_not_challenged": "A newer detected or stored result challenges freshness",
        "no_source_conflict": "Authoritative evidence conflicts with the stored document",
        "recent_source_check": "No successful per-company authoritative source check in the last 24 hours",
        "comparison_sources_verified": "Comparison-period issuer authenticity is not fully verified",
    }
    if all(checks.values()):
        level = "HIGH"
    elif all(checks[k] for k in ("core_facts", "independent_profit_check", "authoritative_source", "tax_pat_reconciled", "freshness_not_challenged", "no_source_conflict", "recent_source_check")) and (checks["comparable_yoy"] or checks["comparable_qoq"]):
        level = "MEDIUM"
    else:
        level = "LOW"
    rationale = [reasons[k] for k, supported in checks.items() if not supported]
    return {"level": level, "rubric": "EARNINGS_EVIDENCE_V1", "checks": checks,
            "rationale": rationale or ["Core facts, source identity, tax reconciliation, comparisons and cash evidence are available within checked coverage"],
            "statistically_calibrated": False}
