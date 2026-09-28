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
    summary = (f"Verified {period.scope.value.lower()} results for the period ended "
               f"{period.period_end.isoformat()} report " + " and ".join(statements) + ".")
    summary_ids = [key for key, _ in selected]

    positives = []
    concerns = []
    changes = []
    trend_parts = []
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
        explanation = f"{subject} {direction} {_display(item['value'])}% {comparison}."
        summary += " " + explanation
        summary_ids.append(key)
        trend_parts.append(f"{subject} {comparison} {direction}")
        trend_ids.append(key)
        claim = {"title": f"{label} {direction}", "explanation": explanation,
                 "evidence_ids": [key]}
        if value > 0:
            positives.append(claim)
        elif value < 0:
            concerns.append(claim)
        changes.append({"metric": label, "change": explanation[:-1],
                        "interpretation": "This comparison uses verified financial facts.",
                        "evidence_ids": [key]})

    if trend_ids:
        favorable = len(positives)
        unfavorable = len(concerns)
        sentiment = ("POSITIVE" if favorable and not unfavorable else
                     "NEGATIVE" if unfavorable and not favorable else "NEUTRAL")
        summary += (" The available comparisons point in different directions."
                    if favorable and unfavorable else
                    " The available comparisons show improvement." if favorable else
                    " The available comparisons show deterioration." if unfavorable else
                    " The available comparisons are unchanged.")
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

    snapshot = next(((key, item) for key, item in registry.items()
                     if item["kind"] == "MARKET_SNAPSHOT"), None)
    if snapshot:
        key, quote = snapshot
        price = f"₹{_display(quote['price'])} on {quote['as_of_date']}"
        if quote.get("pe_ratio") and quote.get("peg_ratio"):
            valuation = {
                "status": "ASSESSED", "verdict": "INCONCLUSIVE",
                "text": (f"At the user-entered close of {price}, sourced P/E was "
                         f"{_display(quote['pe_ratio'])} and calculated PEG was "
                         f"{_display(quote['peg_ratio'])} using a sourced forward annual EPS growth "
                         f"estimate of {_display(quote['forward_eps_growth_pct'])}%. "
                         f"{trend_assessment} One results period cannot establish whether "
                         "the current valuation is justified."),
                "evidence_ids": [key, *valuation_evidence],
            }
        elif quote.get("pe_ratio"):
            growth_note = ("a sourced forward annual EPS growth estimate has not been entered"
                           if quote.get("forward_eps_growth_pct") is None else
                           "the supplied forward annual EPS growth estimate is not positive")
            valuation = {
                "status": "INSUFFICIENT_DATA", "verdict": "INCONCLUSIVE",
                "text": (f"At the user-entered close of {price}, sourced P/E was "
                         f"{_display(quote['pe_ratio'])}. PEG is unavailable because {growth_note}. These results "
                         "alone cannot establish whether the valuation is justified."),
                "evidence_ids": [key, *valuation_evidence],
            }
        else:
            valuation = {
                "status": "INSUFFICIENT_DATA", "verdict": "INCONCLUSIVE",
                "text": (f"A user-entered close of {price} is available, but sourced P/E and "
                         "calculated PEG are unavailable. The results alone cannot establish valuation."),
                "evidence_ids": [key, *valuation_evidence],
            }
    else:
        valuation = {"status": "INSUFFICIENT_DATA", "verdict": "INCONCLUSIVE",
                     "text": "No fresh sourced market snapshot is available for a valuation assessment.",
                     "evidence_ids": []}
    output = {
        "summary": summary,
        "summary_evidence_ids": summary_ids,
        "positives": positives,
        "concerns": concerns,
        "material_changes": changes,
        "questions": ["What explains the reported changes in the next company filing?"],
        "data_quality_notes": ["The model response did not pass validation; this summary uses verified facts and calculated metrics."],
        "management_commentary": None,
        "what_might_happen": {
            "text": ("If the mixed annual and sequential signals persist, the next validated period "
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
    """Keep the model's prose while filling omitted verified result comparisons."""
    fallback = verified_fallback(registry, period)
    additions = {}
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
