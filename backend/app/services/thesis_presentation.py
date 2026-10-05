"""A short investor view of stored thesis definitions and verified outcomes."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re

SUPPORTIVE = {"STRENGTHENING", "ON_TRACK"}
CHALLENGED = {"WEAKENING", "BROKEN"}


def _label(value):
    return (value or "").replace("_", " ").title()


def _question(source):
    metric = source.get("metric")
    quote = source.get("source_quote", "")
    if metric == "REVENUE_START":
        place = re.search(r"\b(AI factory|GPU facility|cloud service) in ([A-Z][a-zA-Z]+)\b", quote)
        return (f"Did the {place.group(1)} in {place.group(2)} begin generating revenue?"
                if place else "Did the announced business begin generating reported revenue?")
    if metric == "GPU_DEPLOYMENT":
        return "Was the disclosed capacity deployment completed?"
    if metric in {"PAT_MARGIN", "EBITDA_MARGIN"}:
        return f"Was the disclosed {_label(metric)} target met?"
    if metric in {"CAPEX", "CAPEX_SCOPE_UNCLEAR"}:
        return "Was the planned capital spending clarified and delivered?"
    return "Was the linked management commitment verified as delivered?"


def _target(source):
    period = source.get("target_period")
    if period:
        return re.sub(r"^(Q[1-4])FY", r"\1 FY", period)
    return source.get("target_date")


def _tests(pillar, as_of):
    evidence = {**pillar.get("source_evidence", {}), **pillar.get("status_evidence", {})}
    result = []
    for item in pillar.get("expectations", []):
        if not item.get("is_active", True):
            continue
        status = (item.get("outcome") or {}).get("status")
        if status in {"DELIVERED", "WITHDRAWN"}:
            continue
        if item.get("checkpoint_id"):
            source = evidence.get(f"K{item['checkpoint_id']}", {})
            if not source:
                continue
            statement = evidence.get(source.get("source_statement_id"), {})
            question, target = _question({**source, "source_quote": statement.get("source_quote", "")}), _target(source)
            evidence_ids = [f"K{item['checkpoint_id']}"]
        elif item.get("expectation_type") == "METRIC":
            target = item.get("target_period_end")
            question = f"Did {_label(item.get('metric_code'))} meet the stated requirement?"
            evidence_ids = []
        else:
            target, question, evidence_ids = None, item["description"], []
        target_date = source.get("target_date") if item.get("checkpoint_id") else item.get("target_period_end")
        dated = bool(target_date)
        try:
            due = date.fromisoformat(str(target_date)[:10]) if dated else date.max
        except ValueError:
            due = date.max
        result.append({"pillar_id": pillar["id"], "pillar": pillar["name"],
                       "question": question, "target": target, "target_date": str(target_date) if dated else None,
                       "evidence_ids": evidence_ids, "status": status or "UNPROVEN",
                       "sort_key": (0 if pillar["importance"] == "CORE" else 1, due, question)})
    return result


def present_thesis(view, *, analysis=None, period=None, registry=None):
    as_of = date.fromisoformat(view["as_of_date"])
    active = [p for p in view["pillars"] if p["is_active"]]
    if not active:
        return {"summary": None, "cards": [], "what_working": [], "needs_proof": [],
                "worries": [], "next_big_test": None, "additional_tests": [], "changes": [],
                "generated_from_research": False}
    tests = [test for pillar in active for test in _tests(pillar, as_of)]
    tests.sort(key=lambda row: row["sort_key"])
    for test in tests:
        del test["sort_key"]
    next_by_id = {test["pillar_id"]: test for test in reversed(tests)}
    working, needs_proof, worries, cards = [], [], [], []
    gap = view["expectation_gap"]
    for pillar in active:
        raw_state = pillar["status"]
        display_state = "UNPROVEN" if raw_state in {"NOT_YET_TESTABLE", "INSUFFICIENT_EVIDENCE"} else raw_state
        next_test = next_by_id.get(pillar["id"])
        outcomes = [row.get("outcome") or {} for row in pillar["expectations"]]
        if raw_state in SUPPORTIVE and any(row.get("status") == "DELIVERED" for row in outcomes):
            interpretation = "A linked investor requirement was verified as delivered."
            working.append(f"{pillar['name']}: a linked requirement was delivered.")
        elif raw_state in CHALLENGED:
            interpretation = pillar["status_reason"]
            worries.append(f"{pillar['name']}: {pillar['status_reason']}")
        elif pillar["category"] == "VALUATION" and gap.get("required_eps_cagr_pct") is not None:
            try:
                required = f"{Decimal(str(gap['required_eps_cagr_pct'])):.2f}"
            except InvalidOperation:
                required = str(gap["required_eps_cagr_pct"])
            interpretation = (f"The saved base scenario requires {required}% EPS growth per year over "
                              f"{gap.get('horizon_years')} years. Comparable verified growth is unavailable."
                              if gap.get("classification") == "INSUFFICIENT_EVIDENCE" else
                              "The saved valuation scenario is compared with verified earnings growth over the same horizon.")
            needs_proof.append(f"{pillar['name']}: verified earnings growth over the scenario horizon.")
        else:
            interpretation = ("A management milestone remains pending; reported outcomes have not verified it."
                              if any(row.get("status") in {"PENDING", "NOT_YET_ASSESSABLE"} for row in outcomes)
                              else "The linked investment requirement has not yet been verified as delivered.")
            needs_proof.append(pillar["name"])
        cards.append({"id": pillar["id"], "name": pillar["name"], "status": display_state,
                      "interpretation": interpretation, "next_test": next_test,
                      "importance": pillar["importance"], "raw_state": raw_state,
                      "research_gaps": pillar.get("research_gaps", []), "evidence_ids": pillar.get("status_evidence_ids", [])})
    if period and registry:
        for row in registry.values():
            if (row.get("kind") == "FACT" and row.get("period_id") == period.id
                    and row.get("metric_code") == "PAT" and Decimal(str(row.get("value", 0))) > 0):
                working.insert(0, f"Verified PAT of ₹{Decimal(str(row['value'])):,.2f} crore was reported for Q{period.quarter} {period.fiscal_year}.")
                break
    for pillar in active:
        if pillar["category"] in {"PROFITABILITY", "CAPITAL_ALLOCATION", "BALANCE_SHEET"}:
            for gap_text in pillar.get("research_gaps", []):
                if ("unclear" in gap_text.lower() or "unavailable" in gap_text.lower()) and gap_text not in worries:
                    worries.append(gap_text)
                    break
    core = next((p for p in active if p["category"] == "GROWTH" and p["importance"] == "CORE"),
                next((p for p in active if p["importance"] == "CORE"), active[0]))
    summary = (f"Verified research frames {core['name']} as a core investment opportunity. "
               if core.get("source_evidence") else
               f"Your investment thesis includes {core['name'].lower()} as a core requirement. ")
    summary += ("A linked investor test has been verified as delivered. " if working and any(p["status"] in SUPPORTIVE for p in active)
                else "The linked operating milestones have not yet established that opportunity. ")
    if needs_proof:
        subject = " and ".join(needs_proof[:2])
        summary += f"{subject} still {'needs' if len(needs_proof) == 1 else 'need'} proof. "
    if tests:
        target = f" by {tests[0]['target']}" if tests[0]["target"] else ""
        question = tests[0]["question"].rstrip("?")
        summary += f"The next important question is: {question}{target}?"
    changes = []
    if analysis:
        for row in view["history"]:
            if (row["kind"] == "EVALUATED" and row.get("analysis_id") == analysis.id
                    and row.get("new_status") in SUPPORTIVE | CHALLENGED
                    and row.get("new_status") != row.get("previous_status")):
                changes.append({"pillar": row["name"], "previous_status": row["previous_status"],
                                "new_status": row["new_status"], "reason": row["reason"],
                                "period": f"Q{period.quarter} {period.fiscal_year}" if period and period.quarter else
                                          period.fiscal_year if period else None,
                                "evidence_ids": row["evidence_ids"]})
    return {"summary": summary.strip(), "cards": cards, "what_working": working[:3],
            "needs_proof": needs_proof[:4], "worries": worries[:3],
            "next_big_test": tests[0] if tests else None, "additional_tests": tests[1:3],
            "changes": changes, "generated_from_research": any(
                p["created_from"] == "SYSTEM_INFERRED" and not p["accepted_by_user"] for p in active)}
