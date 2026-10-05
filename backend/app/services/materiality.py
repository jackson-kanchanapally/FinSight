"""Explainable investor priority for attributed management commentary."""
import re
from decimal import Decimal

NOISE = re.compile(r"good morning|thanks for joining|thank you|hand over|am i audible|you are audible|repeat your question|lost your audio|annual general meeting|companies act|sebi lodr|no obligation to publicly update|forward-looking statements, actual results", re.I)
RULES = (
    ("GUIDANCE_BOUNDARY", r"(?:not supposed|cannot|can't|will not|not planned).{0,65}(?:guidance|forward-looking numbers|disclose)|not.{0,30}giving.{0,30}guidance"),
    ("EXECUTION_RISK", r"delay|postpon|constraint|concentration|not signing.{0,30}customer|before.{0,40}contract|once.{0,25}approv"),
    ("COMMITMENT", r"target|we(?:'ll| will).{0,35}(?:deploy|grow|invest)|revenue.{0,35}(?:from|by) Q[1-4]|from Q[1-4].{0,55}revenue"),
    ("CAPITAL_ALLOCATION", r"capex|capital expenditure|debt|funding|ipo proceed|customer advance"),
    ("OPERATING_CONTEXT", r"seasonality|primarily because|one.time.{0,40}(?:revenue|service)|shift will be"),
    ("ORDER_CONVERSION", r"order book|funnel|pipeline|contracts? (?:will be|have been|was) signed"),
    ("BUSINESS_CHANGE", r"launch|new product|new business|new market|advanced.{0,40}platform|go live|gone live|AI factor|ramp"),
    ("ECONOMICS", r"margin|cash flow|working capital|pricing|lease|billing|contract.*(?:locked|years)"),
)
FACTORS = (
    ("TIMELINE_REVISION", r"\b(?:delay(?:ed)?|postpon\w*|defer\w*|slipp\w*|withdrawn)\b"),
    ("CAPITAL_COMMITMENT", r"\b(?:capex|capital expenditure|capital spend|investment plan)\b"),
    ("FUNDING", r"\b(?:debt|borrow\w*|customer advance|ipo proceed|dilut\w*|funding)\b"),
    ("REVENUE_MILESTONE", r"\b(?:revenue|billing|commercial)\b.{0,75}\b(?:Q[1-4]|FY\d\d|start|begin|commenc|expect|target)"),
    ("CAPACITY", r"\b(?:deploy\w*|capacity|GPU|plant|factory|facility)\b"),
    ("MARGIN_TARGET", r"\b(?:PAT|EBITDA|profit)\b.{0,35}\bmargin\b|\bmargin\b.{0,35}\b(?:target|expect)\b"),
    ("DEMAND_CLAIM", r"\b(?:order book|funnel|pipeline|backlog|customer contract)\b"),
    ("SUPPLY_RISK", r"\b(?:shortage|supply constraint|delivery timeline|allocation|availability of)\b"),
    ("PRICING_ASSUMPTION", r"\b(?:pricing|price[s]? (?:will|go|increase)|rates? (?:will|are)|billing upwards)\b"),
    ("ASSET_ASSUMPTION", r"\b(?:appreciating assets?|asset appreciation|not depreciat\w*|useful life|permanent shortage|guaranteed price)\b"),
    ("APPRECIATION_CLAIM", r"\b(?:appreciating assets?|asset appreciation|not depreciat\w*)\b"),
    ("CUSTOMER_RISK", r"\b(?:customer concentration|single customer|major contract)\b"),
    ("STRATEGY", r"\b(?:new business|new market|shift will be|strategic|AI factor|new platform)\b"),
)
HIGH_FACTORS = {"CAPITAL_COMMITMENT", "FUNDING", "REVENUE_MILESTONE", "MARGIN_TARGET",
                "DEMAND_CLAIM", "SUPPLY_RISK", "PRICING_ASSUMPTION", "ASSET_ASSUMPTION", "CUSTOMER_RISK"}
TIER_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "NOISE": 4}


def materiality(text: str) -> str | None:
    text = " ".join(text.split())
    if len(text) < 45:
        return None
    if re.search(r"revenue from operations came in|our .{0,12}revenue was|total income stands|total expenses of", text, re.I) and not re.search(r"because|target|delay|expect", text, re.I):
        return None
    for category, pattern in RULES:
        if re.search(pattern, text, re.I):
            if category not in {"GUIDANCE_BOUNDARY", "EXECUTION_RISK", "OPERATING_CONTEXT"} and NOISE.search(text):
                continue
            return category
    if not NOISE.search(text):
        for factor, category in (("ASSET_ASSUMPTION", "ECONOMICS"),
                                 ("REVENUE_MILESTONE", "COMMITMENT"),
                                 ("SUPPLY_RISK", "EXECUTION_RISK"),
                                 ("DEMAND_CLAIM", "ORDER_CONVERSION")):
            if re.search(dict(FACTORS)[factor], text, re.I):
                return category
    return None


def materiality_details(text: str, *, annual_revenue_crore=None):
    """Return explicit priority factors; a proposed amount remains a claim."""
    category = materiality(text)
    if category is None:
        return {"category": None, "tier": "NOISE", "factors": []}
    factors = [name for name, pattern in FACTORS if re.search(pattern, text, re.I)]
    if re.search(r"\b(?:target|expect|plan|intend|will|aim|guidance)\b", text, re.I) and re.search(r"\d", text):
        factors.append("QUANTIFIED_COMMITMENT")
    if re.search(r"\b(?:Q[1-4]|FY\s*\d{2,4}|January|February|March|April|May|June|July|August|September|October|November|December)\b", text, re.I):
        factors.append("DATED_MILESTONE")
    if annual_revenue_crore and "CAPITAL_COMMITMENT" in factors:
        match = re.search(r"(?:INR|₹|Rs\.?)[\s]*([\d,]+(?:\.\d+)?)\s*(?:crore|cr\b)", text, re.I)
        if match and Decimal(match[1].replace(",", "")) >= Decimal(str(annual_revenue_crore)):
            factors.append("MATERIAL_TO_VERIFIED_ANNUAL_REVENUE")
    critical = "TIMELINE_REVISION" in factors and bool({"REVENUE_MILESTONE", "CAPACITY", "CAPITAL_COMMITMENT"} & set(factors))
    critical |= "MATERIAL_TO_VERIFIED_ANNUAL_REVENUE" in factors and "DATED_MILESTONE" in factors
    high = bool(HIGH_FACTORS & set(factors)) or ("CAPACITY" in factors and "QUANTIFIED_COMMITMENT" in factors)
    tier = "CRITICAL" if critical else "HIGH" if high else "MEDIUM" if factors else "LOW"
    return {"category": category, "tier": tier, "factors": factors}


def select_material(statements, limit=14, *, annual_revenue_crore=None, required_ids=()):
    """Rank before the packet cap; retain deterministic source-order ties."""
    required_ids = set(required_ids)
    ranked, seen = [], set()
    for statement in statements:
        details = materiality_details(statement.raw_statement, annual_revenue_crore=annual_revenue_crore)
        if statement.evidence_id in required_ids and details["category"] is None:
            details = {"category": "COMMITMENT", "tier": "HIGH", "factors": ["TRACKED_CHECKPOINT"]}
        key = re.sub(r"\W+", " ", statement.raw_statement.lower()).strip()
        if details["category"] is None or key in seen:
            continue
        seen.add(key)
        factors = set(details["factors"])
        rank = (TIER_ORDER[details["tier"]], 0 if statement.evidence_id in required_ids else 1,
                -len(factors & {"TIMELINE_REVISION", "CAPITAL_COMMITMENT", "FUNDING", "REVENUE_MILESTONE",
                                "MARGIN_TARGET", "DEMAND_CLAIM", "SUPPLY_RISK", "ASSET_ASSUMPTION",
                                "APPRECIATION_CLAIM"}),
                0 if "QUANTIFIED_COMMITMENT" in factors else 1,
                0 if "DATED_MILESTONE" in factors else 1, statement.page_number, statement.id)
        ranked.append((rank, statement, details))
    ranked.sort(key=lambda row: row[0])
    selected = [row for row in ranked if row[1].evidence_id in required_ids][:limit]
    remaining = [row for row in ranked if row[1].evidence_id not in required_ids]
    covered = {factor for _, _, details in selected for factor in details["factors"]}
    distinct = set(HIGH_FACTORS) | {"TIMELINE_REVISION", "CAPACITY"}
    while remaining and len(selected) < limit:
        next_row = min(remaining, key=lambda row: (
            row[0][0], -len((set(row[2]["factors"]) & distinct) - covered), row[0]))
        selected.append(next_row)
        covered.update(next_row[2]["factors"])
        remaining.remove(next_row)
    return [(statement, details["category"]) for _, statement, details in selected]


def short_quote(text, limit=260):
    """Use a complete source sentence where possible; never synthesize a fact."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    details = materiality_details(text)
    factor_patterns = dict(FACTORS)
    preferred = next((factor for factor in ("ASSET_ASSUMPTION", "CAPITAL_COMMITMENT",
        "TIMELINE_REVISION", "SUPPLY_RISK", "FUNDING", "DEMAND_CLAIM", "MARGIN_TARGET")
        if factor in details["factors"]), None)
    pattern = factor_patterns[preferred] if preferred else next(
        (rule for name, rule in RULES if name == details["category"]), r"target|risk")
    start = next((i for i, sentence in enumerate(sentences) if re.search(pattern, sentence, re.I)), 0)
    chosen = sentences[start]
    for sentence in sentences[start + 1:]:
        if len(chosen) + len(sentence) + 1 > limit:
            break
        chosen += " " + sentence
    return chosen if len(chosen) <= limit else chosen[:limit].rsplit(" ", 1)[0] + "…"
