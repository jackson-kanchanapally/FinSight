"""Build a small, evidence-linked thesis from the existing Phase 8 proposals."""
import re
from datetime import date
from sqlalchemy import select
from app.models.thesis import ThesisPillar
from app.models.analysis import AIAnalysis
from app.models.financial import FinancialPeriod
from app.schemas.thesis import PillarInput
from app.services.thesis import bootstrap, lock_company, save_pillar


def _bucket(proposal):
    name = proposal["name"]
    category = proposal["category"]
    if category == "VALUATION":
        return "VALUATION"
    if name == "Demand conversion":
        return "DEMAND"
    if category == "PROFITABILITY" or category in {"CAPITAL_ALLOCATION", "BALANCE_SHEET", "CASH_GENERATION"}:
        return "CAPITAL_RETURNS"
    if category in {"EXECUTION", "MANAGEMENT"}:
        return "EXECUTION"
    if category == "GROWTH":
        return "GROWTH"
    # Unknown subjects retain their own category. A small card count is never
    # a reason to merge unrelated risks with revenue or valuation.
    return f"OTHER:{category}:{name}"


def grouped_proposals(proposals, registry):
    groups = {}
    for proposal in proposals:
        groups.setdefault(_bucket(proposal), []).append(proposal)
    result = []
    descriptions = {
        "GROWTH": "The disclosed growth initiative needs verified revenue, beyond management targets and planned capacity.",
        "EXECUTION": "The disclosed operating milestones and management timelines need verified delivery.",
        "CAPITAL_RETURNS": "Earnings must translate into durable margins and returns after capital spending and funding needs.",
        "DEMAND": "Disclosed demand must convert into signed business, reported revenue and cash.",
        "VALUATION": "Verified earnings need to support the growth required by the existing valuation scenarios.",
    }
    for bucket, parts in groups.items():
        evidence = list(dict.fromkeys(key for part in parts for key in part["evidence_ids"]))
        metrics = list(dict.fromkeys(code for part in parts for code in part["metric_codes"]))
        tests = [test for part in parts for test in part["expectations"]]
        if len(evidence) > 16 or len(metrics) > 12 or len(tests) > 12:
            # The Phase 8 contract has explicit source/test bounds. Avoid
            # dropping evidence merely to make an attractive grouping.
            result.extend((PillarInput.model_validate(part), [part]) for part in parts)
            continue
        if bucket == "GROWTH":
            text = " ".join(str(registry.get(key, {}).get("source_quote", "")) for key in evidence)
            name = "AI / GPU Growth" if re.search(r"\bGPU\b|\bAI (?:factory|infrastructure)\b", text, re.I) else "Growth Opportunity"
            category = "GROWTH"
        elif bucket == "CAPITAL_RETURNS":
            name, category = "Profitability & Capital Returns", "PROFITABILITY"
        elif bucket == "EXECUTION":
            name, category = "Execution", "EXECUTION"
        elif bucket == "DEMAND":
            name, category = "Demand Conversion", "GROWTH"
        elif bucket == "VALUATION":
            name, category = "Valuation", "VALUATION"
        else:
            name, category = parts[0]["name"], parts[0]["category"]
        grouped = PillarInput(
            name=name, category=category, description=descriptions.get(bucket, parts[0]["description"]),
            importance="CORE" if any(part["importance"] == "CORE" for part in parts) else "IMPORTANT",
            scope=parts[0]["scope"], created_from="SYSTEM_INFERRED",
            source_analysis_id=parts[0]["source_analysis_id"], evidence_ids=evidence,
            metric_codes=metrics, expectations=tests,
            reason="Generated from verified research")
        result.append((grouped, parts))
    return result


def ensure_thesis(db, company_id, analysis_id, *, today=None):
    """Create once; previous user edits or deactivation always take precedence."""
    today = today or date.today()
    lock_company(db, company_id)
    analysis = db.get(AIAnalysis, analysis_id)
    period = db.get(FinancialPeriod, analysis.financial_period_id) if analysis else None
    if not period or period.company_id != company_id:
        raise ValueError("ANALYSIS_COMPANY_MISMATCH")
    if db.scalar(select(ThesisPillar.id).where(ThesisPillar.company_id == company_id,
                                                 ThesisPillar.scope == period.scope.value).limit(1)):
        return []
    source = bootstrap(db, company_id, analysis_id, today=today)
    grouped = grouped_proposals(source["proposals"], source["evidence"])
    if len(grouped) < 2 or not any(row["kind"] == "FACT" for row in source["evidence"].values()):
        return []
    created = []
    for definition, parts in grouped:
        components = [{"name": part["name"], "created_from": part["created_from"],
                       "description": part["description"], "evidence_ids": part["evidence_ids"]}
                      for part in parts]
        created.append(save_pillar(db, company_id, definition, today=today,
                                   accepted_by_user=False, source_components=components))
    return created
