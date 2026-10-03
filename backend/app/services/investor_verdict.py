"""Explicit, deterministic results and scenario valuation judgments."""

from decimal import Decimal, ROUND_HALF_UP

from app.market.valuation import reverse_growth, scenario

RESULT_RULES = {
    "material_growth_pct": Decimal("5"),
    "strong_growth_pct": Decimal("20"),
    "severe_decline_pct": Decimal("20"),
    "material_margin_bps": Decimal("100"),
    "severe_revenue_qoq_pct": Decimal("-15"),
    "severe_pat_qoq_pct": Decimal("-40"),
}
VALUATION_RULES = {"near_range_pct": Decimal("10"), "extreme_gap_pct": Decimal("35")}


def results_verdict(registry: dict[str, dict]) -> dict:
    metrics = {
        item["metric_code"]: (key, Decimal(item["value"]))
        for key, item in registry.items()
        if item.get("kind") == "METRIC"
    }
    revenue, pat = metrics.get("REVENUE_YOY"), metrics.get("PAT_YOY")
    basis = "YoY"
    if revenue is None or pat is None:
        if metrics.get("REVENUE_QOQ") and metrics.get("PAT_QOQ"):
            revenue, pat, basis = metrics["REVENUE_QOQ"], metrics["PAT_QOQ"], "QoQ"
        else:
            observed = (
                revenue or pat or metrics.get("REVENUE_QOQ") or metrics.get("PAT_QOQ")
            )
            return {
                "verdict": (
                    (
                        "POSITIVE"
                        if observed[1] >= RESULT_RULES["material_growth_pct"]
                        else (
                            "NEGATIVE"
                            if observed[1] <= -RESULT_RULES["material_growth_pct"]
                            else "NEUTRAL"
                        )
                    )
                    if observed
                    else "INSUFFICIENT_EVIDENCE"
                ),
                "confidence": "LOW",
                "reasons": [
                    (
                        "The available comparable trend supports a provisional direction; the missing revenue or profit comparison limits confidence."
                        if observed
                        else "No verified comparable operating trend is available."
                    )
                ],
                "evidence_ids": [observed[0]] if observed else [],
                "signals": {"PARTIAL_COMPARISON": str(observed[1])} if observed else {},
                "methodology": "Material operating trends; an incomplete comparison limits conviction rather than erasing the available evidence.",
            }
    margin = metrics.get("EBITDA_MARGIN_CHANGE_BPS")
    cash = next(
        (
            (key, Decimal(item["value"]))
            for key, item in registry.items()
            if item.get("kind") == "VALUATION"
            and item.get("metric_code") == "TTM_OPERATING_CASH_FLOW"
        ),
        None,
    )
    ttm_pat = next(
        (
            (key, Decimal(item["value"]))
            for key, item in registry.items()
            if item.get("kind") == "VALUATION" and item.get("metric_code") == "TTM_PAT"
        ),
        None,
    )
    cash_weak = bool(
        cash and ttm_pat and ttm_pat[1] > 0 and cash[1] < ttm_pat[1] * Decimal("0.5")
    )
    cash_severe = bool(cash and ttm_pat and ttm_pat[1] > 0 and cash[1] < 0)
    rg, pg = revenue[1], pat[1]
    material = RESULT_RULES["material_growth_pct"]
    strong = RESULT_RULES["strong_growth_pct"]
    severe = RESULT_RULES["severe_decline_pct"]
    margin_bad = bool(margin and margin[1] <= -RESULT_RULES["material_margin_bps"])
    margin_good = bool(margin and margin[1] >= RESULT_RULES["material_margin_bps"])
    revenue_qoq, pat_qoq = metrics.get("REVENUE_QOQ"), metrics.get("PAT_QOQ")
    severe_sequential = bool(
        revenue_qoq
        and pat_qoq
        and revenue_qoq[1] <= RESULT_RULES["severe_revenue_qoq_pct"]
        and pat_qoq[1] <= RESULT_RULES["severe_pat_qoq_pct"]
        and registry.get("H_REVENUE", {}).get("status")
        != "CONSISTENT_WITH_HISTORICAL_PATTERN"
    )
    if (
        rg <= -severe
        and pg <= -severe
        or cash_severe
        and (rg < -material or pg < -material)
    ):
        verdict = "VERY_NEGATIVE"
    elif rg <= -material and pg <= -material or pg <= -severe or cash_severe:
        verdict = "NEGATIVE"
    elif (
        rg >= strong
        and pg >= strong
        and not cash_weak
        and not margin_bad
        and margin_good
        and not severe_sequential
    ):
        verdict = "VERY_POSITIVE"
    elif (
        rg >= material
        and pg >= material
        and not cash_weak
        and not margin_bad
        and not severe_sequential
    ):
        verdict = "POSITIVE"
    elif rg < -material or pg < -material or cash_weak or margin_bad:
        verdict = "NEGATIVE" if rg <= 0 and pg <= 0 or cash_weak else "NEUTRAL"
    else:
        verdict = "NEUTRAL"
    if basis == "QoQ" and verdict in {"VERY_POSITIVE", "VERY_NEGATIVE"}:
        verdict = "POSITIVE" if verdict == "VERY_POSITIVE" else "NEGATIVE"
    reasons = [f"Revenue {basis} {rg:+.2f}%", f"PAT {basis} {pg:+.2f}%"]
    ids = [revenue[0], pat[0]]
    signals = {"REVENUE_GROWTH_SIGNAL": str(rg), "EARNINGS_GROWTH_SIGNAL": str(pg)}
    if margin:
        reasons.append(f"EBITDA margin change {margin[1]:+.2f} basis points YoY")
        ids.append(margin[0])
        signals["MARGIN_SIGNAL_BPS"] = str(margin[1])
    if severe_sequential:
        reasons.append(
            "Revenue and PAT both fell sharply QoQ; verified equivalent-quarter seasonality does not establish a normal pattern"
        )
        ids += [revenue_qoq[0], pat_qoq[0]]
        signals["SEQUENTIAL_SIGNAL"] = "SEVERE_UNEXPLAINED_DECLINE"
    if cash and ttm_pat:
        reasons.append(
            "TTM operating cash flow is below half of TTM PAT"
            if cash_weak
            else "TTM operating cash flow is at least half of TTM PAT"
        )
        ids += [cash[0], ttm_pat[0]]
        signals["CASH_CONVERSION_SIGNAL"] = (
            "WEAK" if cash_weak else "NOT_WEAK_BY_GUARDRAIL"
        )
    else:
        reasons.append("Cash conversion is unavailable")
        signals["CASH_CONVERSION_SIGNAL"] = "UNKNOWN"
    for code in ("EPS_YOY", "EBITDA_YOY", "EPS_QOQ", "EBITDA_QOQ"):
        if code in metrics:
            signals[code] = str(metrics[code][1])
            ids.append(metrics[code][0])
    confidence = (
        "HIGH"
        if basis == "YoY" and margin and cash and ttm_pat and "EPS_YOY" in metrics
        else "MEDIUM" if basis == "YoY" and margin else "LOW"
    )
    return {
        "verdict": verdict,
        "confidence": confidence,
        "reasons": reasons,
        "evidence_ids": list(dict.fromkeys(ids)),
        "signals": signals,
        "methodology": "Comparable revenue and PAT trends; severe cash weakness and margin contraction override positive growth. "
        f"Material growth: {material}%; strong: {strong}%; severe decline: {severe}%; "
        f"material margin move: {RESULT_RULES['material_margin_bps']} bps. "
        f"Revenue QoQ at or below {RESULT_RULES['severe_revenue_qoq_pct']}% plus PAT QoQ at or below "
        f"{RESULT_RULES['severe_pat_qoq_pct']}% caps a positive annual "
        "verdict at neutral until seasonality is established.",
    }


def required_terminal_pe(
    price: Decimal,
    eps: Decimal,
    growth_pct: Decimal,
    years: int,
    required_return_pct: Decimal,
) -> Decimal:
    if (
        min(price, eps) <= 0
        or years < 1
        or growth_pct <= -100
        or required_return_pct <= -100
    ):
        raise ValueError("REVERSE_INPUT_NOT_MEANINGFUL")
    future_eps = eps * (1 + growth_pct / 100) ** years
    target_price = price * (1 + required_return_pct / 100) ** years
    return target_price / future_eps


def classify_valuation(
    current_pe: Decimal | None,
    lower: Decimal | None,
    upper: Decimal | None,
    *,
    near_pct: Decimal = VALUATION_RULES["near_range_pct"],
    extreme_pct: Decimal = VALUATION_RULES["extreme_gap_pct"],
) -> str:
    if (
        current_pe is None
        or lower is None
        or upper is None
        or min(current_pe, lower, upper) <= 0
        or lower > upper
    ):
        return "INSUFFICIENT_EVIDENCE"
    if near_pct < 0 or extreme_pct <= near_pct:
        raise ValueError("INVALID_VALUATION_TOLERANCE")
    if current_pe < lower * (1 - extreme_pct / 100):
        return "EXTREMELY_UNDERVALUED"
    if current_pe < lower * (1 - near_pct / 100):
        return "UNDERVALUED"
    if current_pe <= upper * (1 + near_pct / 100):
        return "FAIRLY_VALUED"
    if current_pe <= upper * (1 + extreme_pct / 100):
        return "OVERVALUED"
    return "EXTREMELY_OVERVALUED"


def tested_pe_range(
    *,
    current_eps: Decimal | None,
    current_price: Decimal | None,
    growth_pct: Decimal | None,
    terminal_pe_low: Decimal | None,
    terminal_pe_high: Decimal | None,
    years: int | None,
    required_return_pct: Decimal | None,
    assumption_source: str | None,
    assumption_date: str | None,
    near_range_pct: Decimal = VALUATION_RULES["near_range_pct"],
    extreme_gap_pct: Decimal = VALUATION_RULES["extreme_gap_pct"],
) -> dict:
    missing = [
        name
        for name, value in (
            ("positive verified TTM EPS", current_eps),
            ("dated price", current_price),
            ("EPS growth assumption", growth_pct),
            ("lower terminal P/E", terminal_pe_low),
            ("upper terminal P/E", terminal_pe_high),
            ("time horizon", years),
            ("required return", required_return_pct),
            ("assumption source", assumption_source),
            ("assumption date", assumption_date),
        )
        if value is None
    ]
    if missing:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "verdict": "INSUFFICIENT_EVIDENCE",
            "confidence": "NOT_ASSESSED",
            "missing": missing,
            "lenses": {
                "forward_earnings": "UNAVAILABLE",
                "historical_valuation": "UNAVAILABLE",
                "peer_valuation": "UNAVAILABLE",
                "growth_adjusted": "UNAVAILABLE",
            },
        }
    if (
        current_eps <= 0
        or current_price <= 0
        or terminal_pe_low <= 0
        or terminal_pe_high < terminal_pe_low
        or years < 1
        or years > 20
        or growth_pct <= -100
        or required_return_pct <= -100
    ):
        raise ValueError("INVALID_TESTED_PE_ASSUMPTIONS")
    factor = ((1 + growth_pct / 100) / (1 + required_return_pct / 100)) ** years
    lower = terminal_pe_low * factor
    upper = terminal_pe_high * factor
    central = (lower + upper) / 2
    current_pe = current_price / current_eps
    round_pe = lambda value: str(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    reverse = reverse_growth(
        current_eps, current_price, terminal_pe_high, years, required_return_pct
    )
    forward = scenario(current_eps, current_price, growth_pct, terminal_pe_high, years)
    verdict = classify_valuation(
        current_pe, lower, upper, near_pct=near_range_pct, extreme_pct=extreme_gap_pct
    )
    return {
        "status": "CALCULATED",
        "verdict": verdict,
        "confidence": "LOW",
        "current_pe": str(current_pe.quantize(Decimal("0.01"))),
        "supported_pe_range": {
            "lower": round_pe(lower),
            "central": round_pe(central),
            "upper": round_pe(upper),
            "unrounded_lower": str(lower),
            "unrounded_upper": str(upper),
        },
        "current_position": (
            "BELOW_RANGE"
            if current_pe < lower
            else "ABOVE_RANGE" if current_pe > upper else "WITHIN_RANGE"
        ),
        "methodology": "Present value of terminal P/E × future EPS growth, expressed as a multiple of current EPS. "
        "Supported current P/E = terminal P/E × ((1 + EPS CAGR)/(1 + required return))^years. "
        f"Within {near_range_pct}% of range is fairly valued; beyond "
        f"{extreme_gap_pct}% is extreme. This tests selected assumptions, not intrinsic value.",
        "assumptions": {
            "eps_growth_pct": str(growth_pct),
            "terminal_pe_low": str(terminal_pe_low),
            "terminal_pe_high": str(terminal_pe_high),
            "years": years,
            "required_return_pct": str(required_return_pct),
            "near_range_pct": str(near_range_pct),
            "extreme_gap_pct": str(extreme_gap_pct),
            "source": assumption_source,
            "type": "USER_ASSUMPTION",
            "date": assumption_date,
        },
        "calculation": {
            "future_eps": forward["future_eps"],
            "future_price_at_upper_terminal_pe": forward["implied_future_price"],
            "annualized_return_at_upper_terminal_pe_pct": forward[
                "annualized_return_pct"
            ],
            "required_terminal_pe": str(
                required_terminal_pe(
                    current_price, current_eps, growth_pct, years, required_return_pct
                ).quantize(Decimal("0.01"))
            ),
            "required_eps_cagr_at_upper_terminal_pe_pct": reverse[
                "required_eps_cagr_pct"
            ],
        },
        "lenses": {
            "forward_earnings": "CALCULATED",
            "historical_valuation": "UNAVAILABLE",
            "peer_valuation": "UNAVAILABLE",
            "growth_adjusted": "GROWTH_ASSUMPTION_ONLY",
        },
        "missing": [
            "Historical or peer valuation evidence",
            "Cash conversion and capital quality may be unavailable",
        ],
    }
