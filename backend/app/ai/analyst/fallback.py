"""Build a cited results summary when model output fails validation."""

from decimal import Decimal, ROUND_HALF_UP

from app.ai.analyst.schemas import AnalystOutput
from app.ai.analyst.validator import validate_output
from app.models.financial import FinancialPeriod


FACT_LABELS = {
    "REVENUE_FROM_OPERATIONS": "revenue from operations",
    "PAT": "profit after tax",
}
COMPARISONS = (
    ("REVENUE_YOY", "Revenue", "YoY"),
    ("REVENUE_QOQ", "Revenue", "QoQ"),
    ("PAT_YOY", "PAT", "YoY"),
    ("PAT_QOQ", "PAT", "QoQ"),
)


def _display(value: str) -> str:
    rounded = abs(Decimal(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:,.2f}"


def verified_fallback(registry: dict[str, dict], period: FinancialPeriod) -> AnalystOutput:
    facts = [(key, item) for key, item in registry.items()
             if item["kind"] == "FACT" and item["period_id"] == period.id]
    selected = [next(((key, item) for key, item in facts if item["metric_code"] == code), None)
                for code in FACT_LABELS]
    selected = [item for item in selected if item is not None]
    if not selected:
        selected = facts[:1]
    if not selected:
        raise ValueError("NO_VERIFIED_FACTS")

    statements = []
    for _, item in selected:
        label = FACT_LABELS.get(item["metric_code"], item["metric_code"].replace("_", " ").lower())
        if Decimal(item["value"]) < 0:
            label = "loss after tax" if item["metric_code"] == "PAT" else f"negative {label}"
        unit = "per share" if item["unit"] == "INR_PER_SHARE" else "crore"
        statements.append(f"{label} of ₹{_display(item['value'])} {unit}")
    summary = (f"The {period.scope.value.lower()} result for the period ended "
               f"{period.period_end.isoformat()} reports " + " and ".join(statements) + ".")
    summary_ids = [key for key, _ in selected]

    positives = []
    concerns = []
    changes = []
    trend_parts = []
    trend_values: dict[str, list[str]] = {"Revenue": [], "PAT": []}
    trend_ids = []
    for code, subject, comparison in COMPARISONS:
        found = next(((key, item) for key, item in registry.items()
                      if item["kind"] == "METRIC" and item["metric_code"] == code), None)
        if found is None:
            continue
        key, item = found
        value = Decimal(item["value"])
        direction = "increased" if value > 0 else "decreased" if value < 0 else "was unchanged"
        label = f"{subject} {comparison}"
        implication = (
            "This supports year-on-year operating progress, subject to the sequential trend."
            if value > 0 and code == "REVENUE_YOY" else
            "This shows earnings growth against the comparable quarter."
            if value > 0 and code == "PAT_YOY" else
            "The sequential decline warrants checking whether the next quarter stabilizes."
            if value < 0 and comparison == "QoQ" else
            "This weakens the comparison with the prior-year quarter."
            if value < 0 else
            "The comparison alone does not establish a durable trend.")
        explanation = f"{subject} {direction} {_display(item['value'])}% {comparison}. {implication}"
        summary_ids.append(key)
        trend_parts.append(f"{subject} {comparison} {direction}")
        trend_values[subject].append(f"{direction} {_display(item['value'])}% {comparison}")
        trend_ids.append(key)
        claim = {"title": f"{label} {direction}", "explanation": explanation,
                 "evidence_ids": [key]}
        if value > 0:
            positives.append(claim)
        elif value < 0:
            concerns.append(claim)
        changes.append({"metric": label, "change": f"{subject} {direction} {_display(item['value'])}% {comparison}",
                        "interpretation": implication,
                        "evidence_ids": [key]})

    if trend_ids:
        favorable = len(positives)
        unfavorable = len(concerns)
        sentiment = ("POSITIVE" if favorable and not unfavorable else
                     "NEGATIVE" if unfavorable and not favorable else "NEUTRAL")
        central = ("Year-on-year progress alongside sequential weakness makes earnings durability the central question. "
                   if favorable and unfavorable else
                   "The reported comparisons improved, with durability still unproven. " if favorable else
                   "The reported comparisons weakened and need an explanation. " if unfavorable else
                   "The available comparisons do not show a clear change in momentum. ")
        summary = central + summary
        summary += " " + "; ".join(f"{subject} {' but '.join(trend_values[subject])}"
                                    for subject in ("Revenue", "PAT") if trend_values[subject]) + "."
        view_reason = "; ".join(trend_parts) + ". " + (
            "The reported comparison signals are mixed." if favorable and unfavorable else
            "The available comparisons are unchanged." if sentiment == "NEUTRAL" else
            "The available comparisons are favorable." if sentiment == "POSITIVE" else
            "The available comparisons are unfavorable.")
    else:
        sentiment = "NEUTRAL"
        view_reason = "No verified comparison is available to judge the results trend."
    view = {"sentiment": sentiment, "reason": view_reason,
            "evidence_ids": trend_ids or summary_ids[:1]}
    valuation_evidence = [summary_ids[0], *trend_ids]
    trend_assessment = (
        "The mixed result trends do not establish whether the estimated growth can be sustained."
        if positives and concerns else
        "Unchanged results do not establish whether the estimated growth can be sustained."
        if sentiment == "NEUTRAL" and trend_ids else
        "The favorable result trends are directionally supportive, but do not prove the estimate."
        if sentiment == "POSITIVE" else
        "The unfavorable result trends weaken support for the estimate, but do not settle valuation."
        if sentiment == "NEGATIVE" else
        "The available results do not establish whether the estimate can be met."
    )
    multiple_assessment = (
        "Year-on-year gains offer some support, but sequential weakness raises a question about sustaining earnings growth."
        if positives and concerns else
        "The favorable comparisons offer directional support for the multiple, but one result does not establish durability."
        if sentiment == "POSITIVE" else
        "The unfavorable comparisons weaken support for the multiple; the next results will matter."
        if sentiment == "NEGATIVE" else
        "The available comparisons do not establish whether earnings can support the multiple."
    )

    snapshot = next(((key, item) for key, item in registry.items()
                     if item["kind"] == "MARKET_SNAPSHOT"), None)
    if snapshot:
        key, quote = snapshot
        price = f"₹{_display(quote['price'])} on {quote['as_of_date']}"
        calculated = {item["metric_code"]: (evidence_id, item) for evidence_id, item in registry.items()
                      if item["kind"] == "VALUATION"}
        pe = calculated.get("PE_TTM")
        peg = calculated.get("PEG_FORWARD")
        if pe:
            summary += (f" At the dated close of {price}, calculated TTM P/E was "
                        f"{_display(pe[1]['value'])}; this result alone does not establish support for that multiple.")
            summary_ids.extend([key, pe[0]])
        reference_note = (f" The entered source P/E reference is {_display(quote['pe_ratio'])}; "
                          "its EPS basis should be reconciled with Almonds' verified TTM EPS."
                          if pe and quote.get("pe_ratio") and
                          Decimal(pe[1]["value"]).quantize(Decimal(".01")) !=
                          Decimal(quote["pe_ratio"]).quantize(Decimal(".01")) else "")
        if pe and peg:
            valuation = {
                "status": "ASSESSED", "verdict": "INCONCLUSIVE",
                "text": (f"At the dated close of {price}, Almonds calculated TTM P/E of "
                         f"{_display(pe[1]['value'])} and forward PEG of "
                         f"{_display(peg[1]['value'])} using a sourced forward annual EPS growth "
                         f"estimate of {_display(quote['forward_eps_growth_pct'])}%."
                         f"{reference_note} "
                         f"{trend_assessment} One results period cannot establish whether "
                         "the current valuation is justified."),
                "evidence_ids": [key, pe[0], peg[0], *valuation_evidence],
            }
        elif pe:
            growth_note = ("a sourced forward annual EPS growth estimate has not been entered"
                           if quote.get("forward_eps_growth_pct") is None else
                           "the supplied forward annual EPS growth estimate is not positive")
            valuation = {
                "status": "ASSESSED", "verdict": "INCONCLUSIVE",
                "text": (f"At the dated close of {price}, Almonds calculated TTM P/E of "
                         f"{_display(pe[1]['value'])}.{reference_note} {multiple_assessment} "
                         f"PEG is unavailable because {growth_note}. These results alone cannot establish whether "
                         "the valuation is justified."),
                "evidence_ids": [key, pe[0], *valuation_evidence],
            }
        else:
            valuation = {
                "status": "INSUFFICIENT_DATA", "verdict": "INCONCLUSIVE",
                "text": (f"A dated close of {price} is available, but reliable TTM P/E and "
                         "forward PEG are unavailable. Four compatible verified quarters and diluted EPS are needed."),
                "evidence_ids": [key, *valuation_evidence],
            }
    else:
        valuation = {"status": "INSUFFICIENT_DATA", "verdict": "INCONCLUSIVE",
                     "text": "No fresh sourced market snapshot is available for a valuation assessment.",
                     "evidence_ids": []}
    has_cash_flow = any(item["kind"] in {"FACT", "VALUATION"} and item.get("metric_code") in
                        {"OPERATING_CASH_FLOW", "TTM_OPERATING_CASH_FLOW", "TTM_FREE_CASH_FLOW"}
                        for item in registry.values())
    has_balance = any(item["kind"] == "FACT" and item.get("metric_code") in
                      {"TOTAL_DEBT", "CASH_AND_CASH_EQUIVALENTS"} for item in registry.values())
    questions = (["What explains the sequential PAT decline despite year-on-year growth?"
                  if sentiment == "NEUTRAL" and trend_ids else
                  "What explains the reported change, and does it persist next quarter?"])
    if not has_cash_flow:
        questions.append("How much of reported profit converted into operating cash flow?")
    quality_notes = ["This interpretation uses verified facts and deterministic metrics."]
    if not has_cash_flow:
        quality_notes.append("Verified cash-flow data is unavailable; earnings quality cannot be judged from profit alone.")
    if not has_balance:
        quality_notes.append("Verified debt and cash balances are unavailable; balance-sheet risk remains unassessed.")
    output = {
        "summary": summary,
        "summary_evidence_ids": summary_ids,
        "positives": positives,
        "concerns": concerns,
        "material_changes": changes,
        "questions": questions,
        "data_quality_notes": quality_notes,
        "management_commentary": None,
        "what_might_happen": {
            "text": ("If the mixed year-on-year and sequential signals persist, the next validated period "
                     "could clarify whether the weakness is temporary."
                     if sentiment == "NEUTRAL" and trend_ids else
                     "If the reported performance continues, the next validated results could confirm the trend."),
            "evidence_ids": trend_ids or summary_ids[:1],
        },
        "result_view": view,
        "valuation_assessment": valuation,
        "research_action": {
            "action": "INVESTIGATE",
            "reason": "Investigate the drivers of the reported results in the next company filing.",
            "evidence_ids": summary_ids[:1],
        },
    }
    return validate_output(AnalystOutput.model_validate(output).model_dump_json(),
                           registry, period.period_end.year, require_extended=True)


def complete_comparison_claims(output: AnalystOutput, registry: dict[str, dict],
                               period: FinancialPeriod) -> AnalystOutput:
    """Keep grounded prose and supply a substantive summary when it omits key trends."""
    fallback = verified_fallback(registry, period)
    additions = {}
    required_summary = {key for key, item in registry.items() if
                        (item["kind"] == "METRIC" and item["metric_code"] in
                         {"REVENUE_YOY", "REVENUE_QOQ", "PAT_YOY", "PAT_QOQ"}) or
                        (item["kind"] == "VALUATION" and item["metric_code"] == "PE_TTM")}
    if not required_summary.issubset(output.summary_evidence_ids) or len(output.summary) < 140:
        additions["summary"] = fallback.summary
        additions["summary_evidence_ids"] = fallback.summary_evidence_ids
    for field, limit in (("positives", 8), ("concerns", 8), ("material_changes", 10)):
        current = list(getattr(output, field))
        cited = {item for claim in current for item in claim.evidence_ids}
        for claim in getattr(fallback, field):
            if len(current) >= limit:
                break
            if claim.evidence_ids[0] not in cited:
                current.append(claim)
                cited.update(claim.evidence_ids)
        additions[field] = current
    enriched = output.model_copy(update=additions)
    return validate_output(enriched.model_dump_json(), registry, period.period_end.year,
                           require_extended=True)
