"""Evidence-led investor sections assembled from verified deterministic context."""

from decimal import Decimal, ROUND_HALF_UP

from app.ai.analyst.schemas import InvestorQuality
from app.models.financial import FinancialPeriod


def _display(value: str | Decimal, places: int = 2) -> str:
    rounded = Decimal(value).quantize(
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
    )
    return f"{rounded:,.{places}f}"


def _movement(subject: str, value: Decimal, basis: str) -> str:
    direction = "rose" if value > 0 else "fell" if value < 0 else "was unchanged"
    return f"{subject} {direction} {_display(abs(value))}% {basis}"


def build_quality_report(
    registry: dict[str, dict], reasoning: dict | None, period: FinancialPeriod
) -> InvestorQuality:
    reasoning = reasoning or {}
    metrics = {
        item["metric_code"]: (key, Decimal(item["value"]))
        for key, item in registry.items()
        if item["kind"] == "METRIC"
    }
    valuations = {
        item["metric_code"]: (key, Decimal(item["value"]))
        for key, item in registry.items()
        if item["kind"] == "VALUATION"
    }
    relationships = reasoning.get("relationships", {})
    patterns = reasoning.get("seasonality", {})
    revenue_yoy = metrics.get("REVENUE_YOY")
    pat_yoy = metrics.get("PAT_YOY")
    revenue_qoq = metrics.get("REVENUE_QOQ")
    pat_qoq = metrics.get("PAT_QOQ")
    pe = valuations.get("PE_TTM")
    ps = valuations.get("PS_TTM")
    ev_ebitda = valuations.get("EV_EBITDA_TTM")
    peg = valuations.get("PEG_FORWARD")
    earnings_yield = valuations.get("EARNINGS_YIELD_PCT")
    pat_ttm = valuations.get("TTM_PAT")
    cash_ttm = valuations.get("TTM_OPERATING_CASH_FLOW")
    margin = relationships.get("ebitda_margin_change")
    growth = relationships.get("pat_vs_revenue_growth")
    cash_relation = relationships.get("cash_vs_pat")
    quote = next(
        (
            (key, item)
            for key, item in registry.items()
            if item["kind"] == "MARKET_SNAPSHOT"
        ),
        None,
    )

    operating_ids = [item[0] for item in (revenue_yoy, pat_yoy) if item]
    if revenue_yoy and pat_yoy:
        operating = (
            _movement("Revenue", revenue_yoy[1], "YoY")
            + " and "
            + _movement("PAT", pat_yoy[1], "YoY")
            + " against the equivalent prior-year quarter."
        )
        if growth:
            operating += (
                f" PAT growth was {growth['relationship'].lower().replace('_', ' ')} revenue "
                "growth; this identifies a divergence, not its cause."
            )
            operating_ids.append(growth["evidence_id"])
    elif revenue_yoy or pat_yoy:
        item = revenue_yoy or pat_yoy
        operating = f"The verified year-on-year comparison is {_display(item[1])}%; the remaining operating comparison is unavailable."
    elif revenue_qoq and pat_qoq:
        operating = (
            _movement("Revenue", revenue_qoq[1], "QoQ")
            + " and "
            + _movement("PAT", pat_qoq[1], "QoQ")
            + ". This is a sequential comparison; a verified YoY pair is unavailable."
        )
        operating_ids = [revenue_qoq[0], pat_qoq[0]]
    else:
        operating = "Verified comparable growth data is insufficient to establish the operating trend."

    seasonal_ids = [item[0] for item in (revenue_qoq, pat_qoq) if item]
    revenue_pattern = patterns.get("revenue", {})
    status = revenue_pattern.get("status", "INSUFFICIENT_HISTORY")
    if revenue_pattern.get("evidence_id"):
        seasonal_ids.append(revenue_pattern["evidence_id"])
    if revenue_qoq:
        revenue_direction = (
            "fell"
            if revenue_qoq[1] < 0
            else "rose" if revenue_qoq[1] > 0 else "was unchanged"
        )
        seasonal = f"Revenue {revenue_direction} {_display(abs(revenue_qoq[1]))}% QoQ, which compares adjacent quarters. "
        if status == "INSUFFICIENT_HISTORY":
            seasonal += (
                "Fewer than three verified equivalent-quarter transitions are available; "
                "the supplied history cannot determine whether this change is normal seasonality."
            )
        elif status == "NO_CLEAR_PATTERN":
            seasonal += "Equivalent-quarter history has no clear recurring pattern."
        else:
            median = revenue_pattern.get("median_change_pct")
            seasonal += (
                f"The historical median equivalent transition was {_display(median)}% across "
                f"{revenue_pattern['historical_count']} periods. The current transition is "
                f"{status.lower().replace('_', ' ')}."
            )
    else:
        seasonal = "No verified adjacent-quarter revenue comparison is available for a seasonality check."
    if pat_qoq:
        pat_direction = (
            "fell" if pat_qoq[1] < 0 else "rose" if pat_qoq[1] > 0 else "was unchanged"
        )
        seasonal += f" PAT {pat_direction} {_display(abs(pat_qoq[1]))}% QoQ; its cause is not established by these figures."

    earnings_ids = [item[0] for item in (pat_yoy, revenue_yoy) if item]
    earnings = (
        (
            "PAT growth should be reconciled with operating margins, other income, finance costs "
            "and tax before attributing it to operating performance."
        )
        if pat_yoy
        else ("Reported profit alone cannot establish earnings quality.")
    )
    if margin:
        margin_bps = Decimal(margin["change_bps"])
        direction = (
            "rose" if margin_bps > 0 else "fell" if margin_bps < 0 else "was unchanged"
        )
        earnings += (
            f" Verified EBITDA margin {direction} {_display(abs(margin_bps))} basis points "
            f"({_display(abs(Decimal(margin['change_percentage_points'])), 4)} percentage points) YoY."
        )
        earnings_ids.extend([margin["evidence_id"], *margin["input_evidence_ids"]])
    if pat_ttm and cash_ttm:
        earnings += (
            f" TTM operating cash flow of ₹{_display(cash_ttm[1])} crore "
            f"was {cash_relation['relationship'].lower().replace('_', ' ') if cash_relation else 'compared with'} "
            f"TTM PAT of ₹{_display(pat_ttm[1])} crore; cash conversion warrants investigation."
        )
        earnings_ids.extend([cash_ttm[0], pat_ttm[0]])
        if cash_relation:
            earnings_ids.append(cash_relation["evidence_id"])
    elif not cash_ttm:
        earnings += " Verified operating cash flow is unavailable, so cash conversion remains unassessed."

    valuation_ids = [pe[0]] if pe else []
    if quote:
        valuation_ids.append(quote[0])
    if pe and quote:
        valuation = (
            f"At the dated close of ₹{_display(quote[1]['price'])} on {quote[1]['as_of_date']}, "
            f"Almonds calculated TTM P/E of {_display(pe[1])}. Future earnings growth matters "
            "to the case being tested at this multiple; the latest quarter alone cannot establish "
            "whether that trajectory is achievable."
        )
        reference = relationships.get("pe_reference")
        if reference:
            valuation += (
                f" Almonds P/E is {reference['relationship'].lower().replace('_', ' ')} the "
                f"external reference P/E of {_display(reference['reference_value'])}. "
                "This is a calculation cross-check, not a valuation benchmark; reconcile the "
                "price date, EPS basis, scope and methodology."
            )
            valuation_ids.append(reference["evidence_id"])
        if quote[1].get("forward_eps_growth_pct") is None:
            valuation += " No sourced forward EPS growth assumption is available."
        available = [
            ("P/S", ps, "×"),
            ("EV/EBITDA", ev_ebitda, "×"),
            ("forward PEG", peg, "×"),
            ("earnings yield", earnings_yield, "%"),
        ]
        for label, item, unit in available:
            if item:
                valuation += f" Calculated {label} is {_display(item[1])}{unit}."
                valuation_ids.append(item[0])
    else:
        valuation = "A comparable calculated TTM P/E and dated price are unavailable; valuation expectations cannot be quantified."

    positive = [item for item in (revenue_yoy, pat_yoy) if item and item[1] > 0]
    negative = [
        item
        for item in (revenue_yoy, pat_yoy, revenue_qoq, pat_qoq)
        if item and item[1] < 0
    ]
    if not pe:
        assessment = "INSUFFICIENT_EVIDENCE"
        support_text = "Calculated valuation is unavailable, so the latest result cannot be tested against a current multiple."
    elif positive and negative:
        assessment = "MIXED_EVIDENCE"
        support_text = (
            "Positive comparable-quarter growth and negative adjacent-quarter changes provide mixed "
            "evidence. The seasonal context and cash conversion needed to judge durability are limited."
        )
    elif positive and cash_ttm and quote and quote[1].get("forward_eps_growth_pct"):
        assessment = "MODERATE_SUPPORT"
        support_text = "Verified growth and available cash and forward inputs offer directional support, subject to their reliability."
    elif positive:
        assessment = "LIMITED_SUPPORT"
        support_text = "Comparable-quarter growth offers limited support, while durability and forward expectations remain unverified."
    else:
        assessment = "INSUFFICIENT_EVIDENCE"
        support_text = "The available operating comparisons do not establish support for the valuation assumptions being tested."
    support_ids = list(
        dict.fromkeys(
            [
                *valuation_ids,
                *operating_ids,
                *[item[0] for item in (revenue_qoq, pat_qoq) if item],
            ]
        )
    )

    strengthening = []
    weakening = []
    if revenue_yoy:
        strengthening.append(
            {
                "text": "Sustained revenue growth against the next comparable prior-year quarter would strengthen evidence of durability.",
                "evidence_ids": [revenue_yoy[0]],
            }
        )
        weakening.append(
            {
                "text": "A reversal of verified year-on-year revenue growth would weaken the operating case.",
                "evidence_ids": [revenue_yoy[0]],
            }
        )
    if pat_qoq and pat_qoq[1] < 0:
        weakening.append(
            {
                "text": "Another steep sequential PAT decline would require comparison with equivalent-quarter history and an explanation.",
                "evidence_ids": [pat_qoq[0]],
            }
        )
    if not cash_ttm and pat_yoy:
        strengthening.append(
            {
                "text": "Verified operating cash flow alongside profit growth would strengthen the earnings-quality evidence.",
                "evidence_ids": [pat_yoy[0]],
            }
        )
    if cash_relation and cash_relation["relationship"] == "BELOW":
        weakening.append(
            {
                "text": "Operating cash flow below reported PAT weakens cash-conversion evidence and needs investigation.",
                "evidence_ids": [cash_relation["evidence_id"], cash_ttm[0], pat_ttm[0]],
            }
        )

    gaps = []

    def gap(
        kind: str, question: str, priority: str, ids: list[str] | None = None
    ) -> None:
        gaps.append(
            {
                "type": kind,
                "question": question,
                "priority": priority,
                "evidence_ids": ids or [],
            }
        )

    if status == "INSUFFICIENT_HISTORY" and revenue_qoq:
        gap(
            "SEASONALITY",
            "How have equivalent-quarter revenue transitions behaved across earlier years?",
            "HIGH",
            seasonal_ids,
        )
    if not cash_ttm and pat_yoy:
        gap(
            "CASH_CONVERSION",
            "How much of reported PAT converted into operating cash flow?",
            "HIGH",
            [pat_yoy[0]],
        )
    elif cash_relation and cash_relation["relationship"] == "BELOW":
        gap(
            "CASH_CONVERSION",
            "Why is verified operating cash flow below reported PAT?",
            "HIGH",
            [cash_relation["evidence_id"], cash_ttm[0], pat_ttm[0]],
        )
    if quote and quote[1].get("forward_eps_growth_pct") is None:
        gap(
            "FORWARD_GROWTH",
            "What sourced forward EPS growth assumption would be used to test the dated P/E?",
            "HIGH",
            valuation_ids,
        )
    if pat_yoy and revenue_yoy and growth and growth["relationship"] == "ABOVE":
        gap(
            "EARNINGS_DRIVERS",
            "Which verified margin, other-income, finance-cost and tax changes explain PAT growing faster than revenue?",
            "MEDIUM",
            operating_ids,
        )
    if pe:
        gap(
            "HISTORICAL_VALUATION",
            "How does the calculated P/E compare with a date-consistent historical P/E series?",
            "MEDIUM",
            [pe[0]],
        )
    if not any(
        (
            item["kind"] == "DOCUMENT_PAGE"
            and "guidance" in item.get("excerpt", "").lower()
        )
        or (
            item["kind"] == "MANAGEMENT_STATEMENT"
            and item.get("statement_type") == "GUIDANCE"
        )
        for item in registry.values()
    ):
        gap(
            "MANAGEMENT_GUIDANCE",
            "What relevant growth or margin guidance has management actually provided?",
            "MEDIUM",
        )
    if not any(
        item["kind"] == "FACT" and item.get("metric_code") == "TOTAL_DEBT"
        for item in registry.values()
    ):
        gap(
            "DEBT",
            "What verified debt and cash balances constrain the valuation case?",
            "MEDIUM",
        )
    priority = (
        "HIGH"
        if sum(item["priority"] == "HIGH" for item in gaps) >= 2
        else "MEDIUM" if gaps else "LOW"
    )
    confidence = "LOW" if not cash_ttm or status == "INSUFFICIENT_HISTORY" else "MEDIUM"
    return InvestorQuality.model_validate(
        {
            "operating_performance": {
                "text": operating,
                "evidence_ids": list(dict.fromkeys(operating_ids)),
            },
            "seasonality_context": {
                "status": status,
                "text": seasonal,
                "evidence_ids": list(dict.fromkeys(seasonal_ids)),
            },
            "earnings_quality": {
                "text": earnings,
                "evidence_ids": list(dict.fromkeys(earnings_ids)),
            },
            "valuation_expectations": {
                "text": valuation,
                "evidence_ids": list(dict.fromkeys(valuation_ids)),
            },
            "results_vs_valuation": {
                "assessment": assessment,
                "text": support_text,
                "evidence_ids": support_ids,
            },
            "strengthening_evidence": strengthening,
            "weakening_evidence": weakening,
            "research_gaps": gaps,
            "research_priority": priority,
            "confidence": confidence,
        }
    )
