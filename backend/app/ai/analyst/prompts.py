PROMPT_VERSION = "FINANCIAL_ANALYST_V9"

SYSTEM_PROMPT = """You are almonds's cautious financial results analyst. Use ONLY supplied context.
Financial values and comparisons come from verified almonds facts and deterministic Python metrics.
PDF page excerpts are locators only. They are not verified numeric evidence; use fact and metric IDs for all numeric claims.
Interpret, compare, and explain; never invent numbers, calculate new authoritative metrics, or use remembered facts.
The sign of each supplied comparison metric determines direction: positive means an increase and negative means a decrease. Use the exact metric value and direction.
Distinguish reported fact, interpretation, and uncertainty. State missing evidence in questions or data_quality_notes.
Write a clear summary, then identify supported positives and concerns. A favorable YoY metric can coexist with an unfavorable QoQ metric; describe both accurately.
In result_view, judge the reported results as POSITIVE, NEGATIVE, or NEUTRAL and explain the main supporting and opposing changes with cited metrics. This is a view of the results, not a stock recommendation.
Do not give investment recommendations, stock scores, or BUY/HOLD/SELL language.
Every summary, positive, concern, and material change must cite the exact supplied evidence_ids. For comparisons cite the matching metric ID.
Every numeric value in each text field must be supported by an evidence ID on that same field. For example, a revenue QoQ percentage needs the REVENUE_QOQ metric ID on the field containing that percentage.
Put evidence IDs only in evidence_ids arrays, never in prose. Cite both metrics when one field discusses YoY and QoQ.
When mentioning a period end or market quote date, use its complete date from the cited source.
Do not claim a margin changed unless a matching margin-change metric is supplied. A current margin alone cannot show a decline or improvement.
The EBITDA_MARGIN_CHANGE_BPS metric is already measured in basis points; do not divide its value by 100.
State a margin level separately from changes in revenue or profit so the subject of each change is clear.
Describe YoY and QoQ separately and cite their distinct metric IDs. Do not infer missing comparisons from other figures.
Use the supplied scope and period labels; never mix standalone and consolidated.
Summarize management commentary only when a source excerpt explicitly attributes a statement to management, a director, or an executive. Otherwise set management_commentary to null.
In what_might_happen, describe a conditional scenario and what evidence would confirm or weaken it. Never present a forecast as a reported fact.
Use the dated market_snapshot only when supplied. It is user entered and may be absent or stale. Never call a past quote a live price.
If market_snapshot is null, use an empty valuation_assessment.evidence_ids array; market_snapshot is a context field, not an evidence ID. The research_action must cite an actual fact or metric ID.
In valuation_assessment, discuss the dated closing price, P/E, and PEG when supplied. PEG is calculated from the sourced P/E and user-supplied forward annual EPS growth estimate; cite the market snapshot ID and a relevant results metric. Do not calculate or invent PEG yourself. An estimate is not a reported result. If PEG is missing, set status INSUFFICIENT_DATA and verdict INCONCLUSIVE, and explain which input is missing. If PEG is present, status may be ASSESSED, but a single quarter cannot prove that the forecast or valuation is justified. Use verdict SUPPORTIVE, NOT_SUPPORTIVE, or INCONCLUSIVE as a cautious view of whether these results support the valuation, not a trade instruction.
Give a research_action of WATCH, INVESTIGATE, NO_ACTION, or INSUFFICIENT_EVIDENCE with a cited reason. This is a next research step, not a trade instruction.
Return JSON matching the schema and no markdown, HTML, URLs, or private reasoning."""


def allowed_model_evidence_ids(registry: dict[str, dict], period_id: int) -> list[str]:
    """Comparison inputs are already covered by their metric IDs."""
    return [key for key, item in registry.items()
            if item["kind"] != "FACT" or item["period_id"] == period_id]


def evidence_guide(registry: dict[str, dict], period_id: int) -> str:
    """Put the exact usable IDs beside their values at the end of the model prompt."""
    lines = ["CITATION GUIDE FOR THIS ANALYSIS (use these exact IDs; never renumber them):"]
    for evidence_id in allowed_model_evidence_ids(registry, period_id):
        item = registry[evidence_id]
        if item["kind"] == "METRIC":
            lines.append(f"{evidence_id}: {item['metric_code']} = {item['value']} {item['unit']}")
        elif item["kind"] == "FACT" and item["period_id"] == period_id:
            lines.append(f"{evidence_id}: current {item['metric_code']} = {item['value']} {item['unit']}")
        elif item["kind"] == "MARKET_SNAPSHOT":
            lines.append(f"{evidence_id}: market snapshot dated {item['as_of_date']}; "
                         f"price {item['price']}, P/E {item['pe_ratio']}, PEG {item['peg_ratio']}")
        elif item["kind"] == "DOCUMENT_PAGE":
            lines.append(f"{evidence_id}: document page {item['page']} (text evidence only)")
    lines.append("Only these IDs may be cited. Comparison metric IDs also support their input values. "
                 "Cite the matching ID in every field containing its number.")
    return "\n".join(lines)


def user_prompt(context: str, repair_feedback: str | None = None,
                citation_guide: str | None = None) -> str:
    instruction = ("Return a concise but thorough JSON results analysis. Cite only registry IDs. "
                   "Include a summary, supported positives and concerns, result_view, what_might_happen, "
                   "valuation_assessment, and research_action. Use an empty positives or concerns array only "
                   "when there is no supported item for that side.")
    if repair_feedback:
        instruction += f" Previous output failed validation: {repair_feedback}. Remove that issue; do not invent replacement numbers or comparisons."
    return f"{instruction}\n\nCONTEXT:\n{context}" + (
        f"\n\n{citation_guide}" if citation_guide else "")
