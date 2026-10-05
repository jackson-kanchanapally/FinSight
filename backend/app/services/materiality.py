"""Transparent investor relevance rules over existing attributed commentary."""
import re
from collections import defaultdict

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


def materiality(text: str) -> str | None:
    text = " ".join(text.split())
    if len(text) < 45:
        return None
    if re.search(r"revenue from operations came in|our .{0,12}revenue was|total income stands|total expenses of", text, re.I) and not re.search(r"because|target|delay|expect", text, re.I):
        return None
    for category, pattern in RULES:
        if re.search(pattern, text, re.I):
            # A risk or an explicit refusal remains useful even inside a polite answer.
            if category not in {"GUIDANCE_BOUNDARY", "EXECUTION_RISK", "OPERATING_CONTEXT"} and NOISE.search(text):
                continue
            return category
    return None


def select_material(statements, limit=14):
    """Round-robin material categories; no numeric quality or credibility score."""
    buckets = defaultdict(list)
    seen = set()
    for statement in sorted(statements, key=lambda s: (s.page_number, s.id)):
        category = materiality(statement.raw_statement)
        key = re.sub(r"\W+", " ", statement.raw_statement.lower()).strip()
        if category and key not in seen:
            buckets[category].append(statement)
            seen.add(key)
    priorities = {
        "CAPITAL_ALLOCATION": r"capex|\bdebt\b|IPO|funding",
        "EXECUTION_RISK": r"delay|postpon|concentration",
        "OPERATING_CONTEXT": r"seasonality",
        "COMMITMENT": r"target.{0,60}\d|\d.{0,30}target|deploy.{0,30}(?:by|from)",
    }
    for category, pattern in priorities.items():
        buckets[category].sort(key=lambda s: not bool(re.search(pattern, s.raw_statement, re.I)))
    selected = []
    while len(selected) < limit and any(buckets.values()):
        for category, _ in RULES:
            if buckets[category] and len(selected) < limit:
                selected.append((buckets[category].pop(0), category))
    return selected


def short_quote(text, limit=260):
    """Use a complete source sentence where possible; never synthesize a fact."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
    category = materiality(text)
    pattern = next((pattern for name, pattern in RULES if name == category), r"target|risk")
    start = next((i for i, sentence in enumerate(sentences) if re.search(pattern, sentence, re.I)), 0)
    chosen = sentences[start]
    for sentence in sentences[start + 1:]:
        if len(chosen) + len(sentence) + 1 > limit:
            break
        chosen += " " + sentence
    return chosen if len(chosen) <= limit else chosen[:limit].rsplit(" ", 1)[0] + "…"
