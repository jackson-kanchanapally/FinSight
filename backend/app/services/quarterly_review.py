"""Material investor evidence packet and a concise deterministic review fallback."""
import re
from decimal import Decimal
from sqlalchemy import select
from app.models.commentary import ManagementStatement
from app.models.financial import (FinancialPeriod, FinancialStatus, FinancialFact, FactStatus,
                                  PeriodType, MetricCode, FinancialUnit)
from app.services.materiality import materiality, materiality_details, select_material, short_quote
from app.services.checkpoints import checkpoint_review, review_as_of


def statement_evidence(statement, category):
    return {"kind": "MANAGEMENT_STATEMENT", "document_id": statement.document_id,
        "page": statement.page_number, "speaker_name": statement.speaker_name,
        "speaker_role": statement.speaker_role, "statement_type": statement.statement_type,
        "topic": statement.topic, "materiality_reason": category,
        "published_at": statement.published_at.isoformat(),
        "excerpt": re.sub(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?", "[number omitted]", statement.raw_statement),
        "source_quote": statement.raw_statement,
        "authority": "MANAGEMENT_CLAIM",
        "document_url": f"/api/documents/{statement.document_id}/file#page={statement.page_number}"}


def note(text, ids, basis="INTERPRETATION", quote=None, signal="WATCH"):
    return {"text": text, "evidence_ids": list(dict.fromkeys(ids))[:8], "basis": basis,
            "source_quote": quote, "signal": signal}


def build_review(db, period, registry, report):
    statements = list(db.scalars(select(ManagementStatement).where(ManagementStatement.financial_period_id == period.id)))
    checkpoints = checkpoint_review(db, period)
    required = {c["source_statement_id"] for c in checkpoints if not c["is_previous"]}
    # Compare only with a verified annual revenue fact of the same company and
    # scope, published by this review's as-of date. No inferred TTM denominator.
    as_of = review_as_of(db, period)
    annual = list(db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.company_id == period.company_id, FinancialPeriod.scope == period.scope,
        FinancialPeriod.period_type == PeriodType.ANNUAL, FinancialPeriod.period_end < period.period_end,
        FinancialPeriod.status == FinancialStatus.VALIDATED).order_by(FinancialPeriod.period_end.desc())))
    annual_revenue = None
    for candidate in annual:
        if review_as_of(db, candidate) > as_of:
            continue
        fact = db.scalar(select(FinancialFact).where(
            FinancialFact.financial_period_id == candidate.id,
            FinancialFact.metric_code == MetricCode.REVENUE_FROM_OPERATIONS,
            FinancialFact.unit == FinancialUnit.INR_CRORE,
            FinancialFact.status.in_([FactStatus.VERIFIED_PDF, FactStatus.VERIFIED_STRUCTURED, FactStatus.MANUALLY_VERIFIED])))
        if fact and fact.value > 0:
            annual_revenue = fact.value
            break
    selected = select_material(statements, annual_revenue_crore=annual_revenue, required_ids=required)
    for statement, category in selected:
        registry[statement.evidence_id] = statement_evidence(statement, category)
    for row in checkpoints:
        registry[row["evidence_id"]] = row
        statement = db.get(ManagementStatement, int(row["source_statement_id"][1:]))
        registry.setdefault(statement.evidence_id, statement_evidence(statement, "COMMITMENT"))
    # Include source and outcome locators in the full registry for drill-down.
    for statement in statements:
        registry.setdefault(statement.evidence_id, statement_evidence(statement, "EVIDENCE_ONLY"))
    metrics = {v["metric_code"]: (k, v) for k, v in registry.items() if v["kind"] == "METRIC" and v.get("period_id") == period.id}
    developments = []
    for basis in ("YOY",):
        pieces, ids = [], []
        for metric, label in (("REVENUE", "Revenue"), ("PAT", "PAT")):
            pair = metrics.get(metric + "_" + basis)
            if pair:
                key, value = pair
                number = Decimal(value["value"])
                pieces.append(f"{label} {'rose' if number > 0 else 'fell' if number < 0 else 'was unchanged at'} {abs(number):.2f}% {basis}")
                ids.append(key)
        if pieces:
            developments.append(note("; ".join(pieces) + ".", ids, "VERIFIED_FACT", signal="MIXED"))
    signals, risks, used_themes = [], [], set()
    current_checkpoints = {c['source_statement_id']: c for c in checkpoints if not c['is_previous']}
    for statement, category in selected:
        quote = short_quote(statement.raw_statement)
        lower = statement.raw_statement.lower()
        factors = set(materiality_details(statement.raw_statement)["factors"])
        cp = current_checkpoints.get(statement.evidence_id)
        ids = [statement.evidence_id]
        theme = cp['metric'] if cp else next((factor for factor in (
            "ASSET_ASSUMPTION", "SUPPLY_RISK", "FUNDING", "DEMAND_CLAIM", "PRICING_ASSUMPTION")
            if factor in factors), category)
        if theme in used_themes:
            continue
        used_themes.add(theme)
        if cp:
            ids.append(cp['evidence_id'])
            target = cp['target_period'] or 'an unspecified reporting period'
            value = cp['target'].get('lower')
            upper = cp['target'].get('upper')
            amount = f"{value}–{upper}" if value != upper else value
            if cp['metric'] == 'REVENUE_START':
                subject = re.search(r'AI factory(?: in [A-Z][A-Za-z]+)?|GPU|data cent(?:er|re)', statement.raw_statement, re.I)
                business = subject[0] if subject else 'the named new business'
                text = f"Management expects {business} to begin contributing revenue from {target}; this remains an operating milestone to verify."
                if re.search(r'delay|postpon', lower):
                    text += " Management also reports a delay to the earlier schedule."
            elif cp['metric'] == 'CAPEX_SCOPE_UNCLEAR':
                text = f"Management outlined capex for {target}, but total versus additional spending is unclear. Confirm the budget before comparing execution."
            else:
                text = f"Management targets {cp['metric'].lower().replace('_', ' ')} of {amount} {cp['unit'].lower().replace('_', ' ')} for {target}; this is a commitment, not a verified outcome."
        elif 'ASSET_ASSUMPTION' in factors:
            text = ("Management treats the asset's future value as favorable; test this claim against utilization, "
                    "realized pricing, depreciation and cash returns as the investment is deployed.")
        elif 'SUPPLY_RISK' in factors:
            text = ("Management reports supply or delivery constraints; check whether capacity arrives in time "
                    "for the disclosed revenue milestones.")
        elif 'FUNDING' in factors:
            text = ("Management describes proposed funding sources; verify actual customer advances, debt "
                    "and cash outlays before assuming the expansion is financed.")
        elif category == 'GUIDANCE_BOUNDARY':
            text = ('Management declined to provide numerical guidance; future growth expectations require follow-up.'
                    if re.search(r'guidance|forward-looking numbers', lower) else
                    'Management declined to disclose the requested detail; the missing disclosure remains a research question.')
        elif category == 'EXECUTION_RISK':
            issue = 'delays' if re.search(r'delay|postpon', lower) else 'customer concentration' if 'concentration' in lower else 'execution dependencies'
            text = f"Management describes {issue}; check the effect on operating milestones against subsequent disclosures."
        elif category == 'CAPITAL_ALLOCATION':
            sources = [label for pattern, label in ((r'customer advance|advance.{0,25}customer', 'customer advances'),
                (r'\bipo\b', 'IPO proceeds'), (r'\bdebt\b|lending', 'debt')) if re.search(pattern, lower)]
            text = (f"Management identifies {', '.join(sources)} as funding sources; track actual funding alongside spending."
                    if sources else 'Management discusses capital allocation; compare the spending commitment with subsequent cash-flow disclosures.')
        elif category == 'OPERATING_CONTEXT' and 'seasonality' in lower:
            text = 'Management describes seasonality in quarterly performance; its explanation needs to be checked against comparable historical results.'
        elif category == 'ORDER_CONVERSION':
            text = ('Management describes a demand funnel; verify conversion into signed contracts, '
                    'customer advances and reported revenue.' if 'funnel' in lower else
                    'Management cites an order book; verify conversion into reported revenue and cash collection.')
        elif category == 'BUSINESS_CHANGE':
            text = 'Management describes a product or business expansion; monitor the contribution to reported revenue and earnings.'
        elif category == 'ECONOMICS':
            text = 'Management discusses commercial terms and economics; follow up on their effect on realized margins and cash collection.'
        else:
            continue  # Ambiguous timing or an unmeasurable promise stays in source drill-down.
        signal = "NEGATIVE" if category == "EXECUTION_RISK" else "WATCH" if category in {"COMMITMENT", "CAPITAL_ALLOCATION", "GUIDANCE_BOUNDARY"} else "MIXED"
        entry = note(text, ids, "MANAGEMENT_CLAIM", quote.rstrip("…"), signal)
        if ('ASSET_ASSUMPTION' in factors or 'SUPPLY_RISK' in factors or
                category in {"EXECUTION_RISK", "CAPITAL_ALLOCATION", "GUIDANCE_BOUNDARY"}) and len(risks) < 4:
            risks.append(entry)
        elif category in {"COMMITMENT", "BUSINESS_CHANGE", "OPERATING_CONTEXT", "ORDER_CONVERSION"} and len(developments) < 6:
            developments.append(entry)
        elif len(signals) < 4:
            signals.append(entry)
    previous = list(db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.company_id == period.company_id, FinancialPeriod.scope == period.scope,
        FinancialPeriod.period_end < period.period_end, FinancialPeriod.period_type == period.period_type,
        FinancialPeriod.status == FinancialStatus.VALIDATED).order_by(FinancialPeriod.period_end.desc())))
    previous = [p for p in previous if review_as_of(db, p) <= as_of]
    previous_statements = list(db.scalars(select(ManagementStatement).where(
        ManagementStatement.financial_period_id == previous[0].id))) if previous else []
    changes = []
    # A reported revision is useful even before a prior transcript is in our
    # database, but remains an attributed claim about the earlier schedule.
    for statement in statements:
        if re.search(r"\b(?:delay|postpon|defer|slipp)\w*\b", statement.raw_statement, re.I) and re.search(
                r"\b(?:earlier|previous|original|date|schedule|timeline)\b", statement.raw_statement, re.I):
            changes.append({"type": "TIMELINE_DELAYED", "text":
                "Management reports a revision to its earlier execution timeline; verify the new milestone against subsequent disclosures.",
                "evidence_ids": [statement.evidence_id], "source_quote": short_quote(statement.raw_statement, 220).rstrip("…"),
                "basis": "MANAGEMENT_CLAIM"})
            break
    for cp in checkpoints:
        change = cp["actual_outcome"].get("change")
        if change in {"GUIDANCE_RAISED", "GUIDANCE_CUT", "GUIDANCE_MAINTAINED"}:
            changes.append({"type": change, "text": f"The comparable {cp['metric'].lower().replace('_', ' ')} guidance was {change[9:].lower()}.", "evidence_ids": [cp["evidence_id"]]})
        if cp["is_previous"] and cp["status"] in {"DELAYED", "WITHDRAWN"}:
            changes.append({"type": "TIMELINE_DELAYED" if cp["status"] == "DELAYED" else "GUIDANCE_WITHDRAWN",
                "text": cp["actual_outcome"]["note"], "evidence_ids": [cp["evidence_id"], *cp["actual_outcome"]["evidence_ids"]]})
    # A single quarter's commentary cannot establish a change in strategy or a recurring problem.
    history_note = ("Baseline established this quarter. Prior comparable commentary is unavailable, so repeated execution patterns cannot yet be established."
                    if not previous_statements else "Prior comparable commentary is available; only supported changes are listed.")
    assessed = [c for c in checkpoints if c["is_previous"] and c["status"] in {"DELIVERED", "MISSED", "DELAYED", "WITHDRAWN", "PARTIALLY_DELIVERED"}]
    delivered = [c for c in assessed if c["status"] == "DELIVERED"]
    missed = [c for c in assessed if c["status"] in {"MISSED", "DELAYED"}]
    credibility = "There are no assessable prior commitments yet; management credibility cannot be judged from this quarter's tone."
    if assessed:
        credibility = ("Observed delivery is mixed; some commitments were delivered while others were missed or delayed." if delivered and missed else
                       "The assessed commitments were delivered; this is limited to the cited outcomes." if delivered else
                       "The assessed commitments include misses or delays; the cited deadlines require follow-up.")
    # Spending or deployment alone cannot validate an earnings-growth assumption.
    earnings_outcomes = [c for c in assessed if c["metric"] in {
        "REVENUE_GROWTH", "PAT_GROWTH", "EPS_GROWTH", "PAT_MARGIN", "EBITDA_MARGIN"}]
    earnings_missed = [c for c in earnings_outcomes if c["status"] in {"MISSED", "DELAYED"}]
    earnings_delivered = [c for c in earnings_outcomes if c["status"] == "DELIVERED"]
    valuation_status = "INSUFFICIENT_EVIDENCE"
    if report["tested_pe"].get("reverse_expectations") and earnings_outcomes:
        valuation_status = "LESS_CREDIBLE" if earnings_missed else "MORE_CREDIBLE" if earnings_delivered else "UNCHANGED"
    valuation_ids = ["I_REVIEW"] + (["A_BASE"] if "A_BASE" in registry else []) + [c["evidence_id"] for c in earnings_outcomes[:3]]
    valuation_text = ("The disclosed scenarios require future earnings execution. Management plans identify possible growth drivers, but promises alone do not establish that the required growth will be achieved."
                      if report["tested_pe"].get("reverse_expectations") else "Valuation inputs are insufficient to judge how these developments affect the required growth assumptions.")
    if earnings_missed:
        valuation_text += " Missed or delayed commitments reduce support for timely execution."
    elif earnings_delivered:
        valuation_text += " Verified delivery adds support for execution of the disclosed assumptions."
    watch = []
    watch_priority = {"REVENUE_START": 0, "CAPEX_SCOPE_UNCLEAR": 1, "GPU_DEPLOYMENT": 2}
    for cp in sorted(checkpoints, key=lambda item: (
            watch_priority.get(item["metric"], 3), item["target_period"] is None and item["target_date"] is None)):
        if cp["status"] in {"PENDING", "NOT_YET_ASSESSABLE", "NOT_DISCLOSED", "INSUFFICIENT_EVIDENCE"} and len(watch) < 3:
            target = cp["target_period"] or cp["target_date"] or "an explicit reporting deadline"
            if cp["metric"] == "REVENUE_START":
                text = f"Verify whether the disclosed business began earning revenue by {target}."
            elif cp["metric"] == "CAPEX_SCOPE_UNCLEAR":
                text = f"Verify {target} capex spending and whether the disclosed amount is total or additional."
            elif cp["metric"] == "GPU_DEPLOYMENT":
                text = f"Verify deployed GPU count against management's {target} target."
            else:
                text = f"Verify {cp['metric'].lower().replace('_', ' ')} against {target}; confirm the measurement basis and obtain the outcome disclosure."
            matching = next((n for n in watch if n['text'] == text), None)
            if matching:
                matching['evidence_ids'].append(cp['evidence_id'])
            else:
                watch.append(note(text, [cp["evidence_id"]]))
    for statement, _ in selected:
        if len(watch) >= 5:
            break
        factors = set(materiality_details(statement.raw_statement)["factors"])
        if "ASSET_ASSUMPTION" in factors:
            watch.append(note("Compare realized asset utilization, pricing and depreciation with management's stated economics.",
                              [statement.evidence_id], "MANAGEMENT_CLAIM", short_quote(statement.raw_statement, 220).rstrip("…")))
        elif "DEMAND_CLAIM" in factors and not any("contracts" in row["text"] for row in watch):
            watch.append(note("Check whether management's demand funnel converts into signed contracts and reported revenue.",
                              [statement.evidence_id], "MANAGEMENT_CLAIM", short_quote(statement.raw_statement, 220).rstrip("…")))
    if not watch:
        watch.append(note("Check whether the next comparable results confirm the operating trend and provide cash-conversion evidence.", report["results"]["evidence_ids"][:4]))
    result_text = report["investor_conclusion"]["text"].split("The dated P/E")[0].split("The valuation cannot")[0].strip()
    result_ids = report["results"]["evidence_ids"][:4]
    if checkpoints:
        first = checkpoints[0]
        target = first["target_period"] or first["target_date"] or "an unspecified period"
        next_text = (f"Management's {first['metric'].lower().replace('_', ' ')} commitment for {target} "
                     f"remains {first['status'].lower().replace('_', ' ')}; verified delivery is the next test. "
                     + history_note)
        next_ids, next_quote = [first["evidence_id"], "I_REVIEW"], None
    else:
        next_text = (("The material management disclosures create execution questions for future quarters. " if selected else
                      "No material management commentary is available for this period. ") + history_note)
        next_ids = [s.evidence_id for s, _ in selected[:3]] or result_ids
        next_quote = short_quote(selected[0][0].raw_statement, 300).rstrip("…") if selected else None
    paragraphs = [note(result_text, result_ids), note(next_text, next_ids, quote=next_quote)]
    if checkpoints:
        dated = next((c for c in checkpoints if c["target_period"] or c["target_date"]), None)
        capex = next((c for c in checkpoints if c["metric"] == "CAPEX_SCOPE_UNCLEAR"), None)
        cash_gap = any("cash conversion" in gap.lower() for gap in report.get("research_gaps", []))
        tests = ["the next dated operating milestone" if dated else "the next reported operating milestone"]
        if capex:
            tests.append("clarification of the capital-spending plan")
        if cash_gap:
            tests.append("verified cash conversion")
        test_text = tests[0] if len(tests) == 1 else ", ".join(tests[:-1]) + " and " + tests[-1]
        conclusion_text = ("Verified results establish this quarter's operating baseline; management's material "
            "commitments still need measured outcomes. " + credibility + " Evidence on " +
            test_text + " would materially change confidence in the disclosed growth assumptions.")
        conclusion_ids = list(dict.fromkeys(result_ids[:2] + [c["evidence_id"] for c in (dated, capex)
            if c] + valuation_ids))[:8]
    else:
        conclusion_text = "There are no source-backed management commitments to evaluate for this period. " + credibility + " " + valuation_text
        conclusion_ids = list(dict.fromkeys(result_ids[:2] + valuation_ids))[:8]
    conclusion = note(conclusion_text, conclusion_ids)
    review = {"quarterly_investor_view": paragraphs, "key_developments": developments[:7],
        "management_signals": signals, "what_changed": [note(c["text"], c["evidence_ids"],
            c.get("basis", "INTERPRETATION"), c.get("source_quote")) for c in changes[:5]],
        "valuation_implication": {"status": valuation_status, **note(valuation_text, valuation_ids)},
        "key_risks": risks[:4], "watch_next_quarter": watch[:6], "investor_conclusion": conclusion}
    def priority(statement, category):
        details = materiality_details(statement.raw_statement, annual_revenue_crore=annual_revenue)
        return details if details["category"] else {
            "category": category, "tier": "HIGH", "factors": ["TRACKED_CHECKPOINT"]}

    packet = {"material_statements": [{"evidence_id": s.evidence_id, "category": category,
        "tier": priority(s, category)["tier"],
        "priority_factors": priority(s, category)["factors"],
        "source_quote": short_quote(s.raw_statement, 300).rstrip("…"),
        "speaker": s.speaker_name, "page": s.page_number} for s, category in selected],
        "checkpoints": [{k: c[k] for k in ("evidence_id", "source_statement_id", "metric", "target_period", "target_date", "target", "unit", "status", "actual_outcome", "is_previous", "measurement_note")} for c in checkpoints],
        "changes": changes[:6], "history_note": history_note, "execution_summary": credibility,
        "valuation_implication_status": valuation_status}
    return {"quarterly_review": review, "checkpoints": checkpoints, "review_packet": packet,
        "execution_summary": credibility, "history_note": history_note,
        "material_statement_count": len(selected), "full_statement_count": len(statements)}


def refresh_quarterly_reviews(db, company_id):
    """New results/commentary refresh the latest review; Qwen remains optional."""
    import logging
    from app.ai.analyst.service import analyze_period
    from app.models.company import Company
    from app.models.financial import PeriodType, FinancialFact, FactStatus
    company = db.get(Company, company_id)
    if db.scalar(select(ManagementStatement.id).where(ManagementStatement.company_id == company_id).limit(1)) is None:
        # Results-only companies retain the existing on-demand analysis flow.
        return
    periods = list(db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.company_id == company_id, FinancialPeriod.status == FinancialStatus.VALIDATED,
        FinancialPeriod.period_type == PeriodType.QUARTERLY).order_by(FinancialPeriod.period_end.desc())))
    seen = set()
    for period in periods:
        if period.scope in seen:
            continue
        seen.add(period.scope)
        if db.scalar(select(FinancialFact.id).where(FinancialFact.financial_period_id == period.id,
            FinancialFact.status.in_([FactStatus.VERIFIED_PDF, FactStatus.VERIFIED_STRUCTURED, FactStatus.MANUALLY_VERIFIED])).limit(1)) is None:
            continue
        try:
            analyze_period(db, company, period, deterministic_only=True)
        except ValueError as exc:
            logging.getLogger(__name__).warning("quarterly_review.unavailable: %s", exc)
