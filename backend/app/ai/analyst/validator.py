"""Reject invented citations, unsupported numeric claims, and unsafe analyst output."""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from pydantic import ValidationError

from app.ai.analyst.schemas import AnalystOutput

NUMBER = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:,\d+)*(?:\.\d+)?")
RECOMMENDATION = re.compile(r"\b(?:buy|sell|hold|strong buy|weak buy|conviction score)\b", re.I)
MARGIN_CHANGE = re.compile(
    r"\b(?:pat|ebitda) margin\b(?:\s+(?:has|had|was|is|were|been))?\s+"
    r"(?:increas\w*|decreas\w*|dropp?\w*|fell|rose|improv\w*|"
    r"contract\w*|expand\w*|higher|lower|growth|decline\w*|up|down)\b"
    r"|\b(?:increas\w*|decreas\w*|dropp?\w*|fell|rose|improv\w*|"
    r"contract\w*|expand\w*|higher|lower|growth|decline\w*)\s+"
    r"(?:in\s+)?\b(?:pat|ebitda) margin\b"
    r"|\b(?:pat|ebitda) margin\s+(?:change|movement|comparison|contraction|expansion|improvement|decline)\b"
    r"|\b(?:pat|ebitda) margin\b\s+(?:(?:was|is|at|of|stood at)\s+)?"
    r"\d+(?:\.\d+)?\s*%?\s*,?\s*(?:up|down|higher|lower|increas\w*|decreas\w*)\b"
    r"|\b(?:pat|ebitda) margin\b"
    r"(?:(?!\b(?:and|while|whereas|but|revenue|sales|profit|pat|ebitda)\b)[^,;.!?])*"
    r"\b(?:vs\.?|versus|compared (?:to|with))\b",
    re.I,
)
CLAUSE_BREAK = re.compile(r"[;!?]|(?<!\d)\.(?!\d)|\b(?:while|whereas|but|and)\b", re.I)
COMPARISON_BREAK = re.compile(r"[;!?]|(?<!\d)\.(?!\d)|\b(?:while|whereas|but)\b", re.I)
COMPARISON_WORD = re.compile(r"\byoy\b|year.over.year|same quarter last year|"
                             r"\bqoq\b|quarter.over.quarter|previous quarter", re.I)
COMPARISON_SUBJECT = re.compile(r"\b(?:revenue|pat|profit after tax)\b", re.I)


class InvalidAnalysis(ValueError):
    def __init__(self, reason: str, token: str | None = None, hint: str | None = None,
                 field: str | None = None):
        self.reason = reason
        self.token = token
        self.hint = hint
        self.field = field
        super().__init__(reason)


def citation_feedback(raw: str, registry: dict[str, dict], period_year: int) -> list[str]:
    """Report numeric and ID mistakes across fields so one retry can fix them together."""
    try:
        result = AnalystOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, TypeError):
        return []
    fields = [("summary", result.summary, result.summary_evidence_ids)]
    for name, claims in (("positives", result.positives), ("concerns", result.concerns),
                         ("material_changes", result.material_changes)):
        fields.extend((f"{name}[{index}]", _claim_text(claim), claim.evidence_ids)
                      for index, claim in enumerate(claims))
    for name in ("management_commentary", "what_might_happen", "result_view",
                 "valuation_assessment", "research_action"):
        item = getattr(result, name)
        if item is not None:
            fields.append((name, item.text if hasattr(item, "text") else item.reason,
                           item.evidence_ids))
    issues = []
    for name, claim_text, ids in fields:
        try:
            _require_known(ids, registry, name)
            _check_numbers(claim_text, registry, period_year, ids, name)
        except InvalidAnalysis as exc:
            issue = f"{name}: {exc.reason}"
            if exc.token:
                issue += f" {exc.token}"
            if exc.hint:
                issue += f". {exc.hint}"
            issues.append(issue)
    return issues[:8]


def validate_output(raw: str, registry: dict[str, dict], period_year: int,
                    require_extended: bool = False) -> AnalystOutput:
    try:
        result = AnalystOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise InvalidAnalysis("INVALID_JSON_OR_SCHEMA") from exc

    _require_known(result.summary_evidence_ids, registry, "summary")
    _check_numbers(result.summary, registry, period_year, result.summary_evidence_ids, "summary")
    _check_semantics(result.summary, result.summary_evidence_ids, registry, "summary")
    all_text = [result.summary, *result.questions, *result.data_quality_notes]
    for field, claims in (("positives", result.positives), ("concerns", result.concerns),
                          ("material_changes", result.material_changes)):
        for claim in claims:
            _validate_claim_numbers(claim, field, registry, period_year)
            _check_semantics(_claim_text(claim), claim.evidence_ids, registry, field)
            all_text.append(_claim_text(claim))
    if require_extended and result.what_might_happen is None:
        raise InvalidAnalysis("MISSING_OUTLOOK")
    for field, note in (("management_commentary", result.management_commentary),
                        ("what_might_happen", result.what_might_happen)):
        if note is None:
            continue
        _require_known(note.evidence_ids, registry, field)
        _check_numbers(note.text, registry, period_year, note.evidence_ids, field)
        _check_semantics(note.text, note.evidence_ids, registry, field)
        all_text.append(note.text)
    if require_extended and result.result_view is None:
        raise InvalidAnalysis("MISSING_RESULT_VIEW")
    if result.result_view is not None:
        view = result.result_view
        _require_known(view.evidence_ids, registry, "result_view")
        _check_numbers(view.reason, registry, period_year, view.evidence_ids, "result_view")
        _check_semantics(view.reason, view.evidence_ids, registry, "result_view")
        comparisons = {key: Decimal(item["value"]) for key, item in registry.items()
                       if item["kind"] == "METRIC" and item["metric_code"] in {
                           "REVENUE_YOY", "REVENUE_QOQ", "PAT_YOY", "PAT_QOQ"}}
        values = [value for key, value in comparisons.items() if key in view.evidence_ids]
        if comparisons and not values:
            raise InvalidAnalysis("RESULT_VIEW_EVIDENCE_MISSING", field="result_view")
        if values and ((all(value > 0 for value in values) and view.sentiment == "NEGATIVE")
                       or (all(value < 0 for value in values) and view.sentiment == "POSITIVE")
                       or (all(value == 0 for value in values) and view.sentiment != "NEUTRAL")):
            raise InvalidAnalysis("RESULT_VIEW_DIRECTION_MISMATCH", field="result_view")
        all_text.append(view.reason)
    if require_extended and (result.valuation_assessment is None or result.research_action is None):
        raise InvalidAnalysis("MISSING_VALUATION_OR_ACTION")
    valuation = result.valuation_assessment
    if valuation is not None:
        _require_known(valuation.evidence_ids, registry, "valuation_assessment")
        _check_numbers(valuation.text, registry, period_year, valuation.evidence_ids, "valuation_assessment")
        _check_semantics(valuation.text, valuation.evidence_ids, registry, "valuation_assessment")
        if valuation.status == "INSUFFICIENT_DATA" and valuation.verdict != "INCONCLUSIVE":
            raise InvalidAnalysis("VALUATION_VERDICT_UNSUPPORTED")
        if valuation.status == "ASSESSED" and (
            not any(registry[item]["kind"] == "MARKET_SNAPSHOT" and registry[item].get("peg_ratio")
                    for item in valuation.evidence_ids)
            or not any(registry[item]["kind"] in {"FACT", "METRIC"}
                       for item in valuation.evidence_ids)):
            raise InvalidAnalysis("VALUATION_EVIDENCE_MISSING")
        if valuation.status == "ASSESSED" and any(
            item["kind"] == "METRIC" and item["metric_code"].endswith(("_YOY", "_QOQ"))
            for item in registry.values()) and not any(
            registry[item]["kind"] == "METRIC" and
            registry[item]["metric_code"].endswith(("_YOY", "_QOQ"))
            for item in valuation.evidence_ids):
            raise InvalidAnalysis("VALUATION_EVIDENCE_MISSING")
    action = result.research_action
    if action is not None:
        _require_known(action.evidence_ids, registry, "research_action")
        _check_numbers(action.reason, registry, period_year, action.evidence_ids, "research_action")
        _check_semantics(action.reason, action.evidence_ids, registry, "research_action")
        all_text.append(action.reason)
    if valuation is not None:
        all_text.append(valuation.text)
    if result.management_commentary and not any(
        registry[item].get("kind") == "DOCUMENT_PAGE" and
        re.search(r"\b(?:management|director|ceo|cfo|chairman|guidance|outlook)\b",
                  registry[item].get("excerpt", ""), re.I)
        for item in result.management_commentary.evidence_ids):
        raise InvalidAnalysis("MANAGEMENT_NOT_IN_SOURCE")
    _check_numbers(" ".join([result.summary, *result.questions, *result.data_quality_notes]),
                   registry, period_year, registry.keys(), "questions_or_data_quality_notes")
    if RECOMMENDATION.search(" ".join(all_text)):
        raise InvalidAnalysis("RECOMMENDATION_FORBIDDEN")
    return result


def _claim_text(claim) -> str:
    if hasattr(claim, "title"):
        return " ".join((claim.title, claim.explanation))
    return " ".join((claim.metric, claim.change, claim.interpretation))


def _validate_claim_numbers(claim, field: str, registry: dict[str, dict], period_year: int) -> None:
    _require_known(claim.evidence_ids, registry, field)
    _check_numbers(_claim_text(claim), registry, period_year, claim.evidence_ids, field)


def _require_known(ids: list[str], registry: dict[str, dict], field: str) -> None:
    missing = next((item for item in ids if item not in registry), None)
    if missing:
        hint = ("No market snapshot was supplied; use an empty valuation evidence_ids array."
                if field == "valuation_assessment" and missing == "market_snapshot" else
                "Use only evidence IDs listed in the context registry for this field.")
        raise InvalidAnalysis("UNKNOWN_EVIDENCE_ID", token=missing, hint=hint, field=field)
    if len(set(ids)) != len(ids):
        raise InvalidAnalysis("UNKNOWN_EVIDENCE_ID", hint="Remove duplicate evidence IDs.", field=field)


def _check_semantics(text: str, evidence_ids: list[str], registry: dict[str, dict], field: str) -> None:
    lowered = text.lower()
    codes = {registry[item]["metric_code"] for item in evidence_ids
             if registry[item]["kind"] == "METRIC"}
    for match in MARGIN_CHANGE.finditer(lowered):
        phrase = match.group()
        if "pat margin" in phrase:
            raise InvalidAnalysis("UNSUPPORTED_MARGIN_COMPARISON", field=field)
        clause_start = max((boundary.end() for boundary in CLAUSE_BREAK.finditer(lowered, 0, match.start())),
                           default=0)
        following_boundary = CLAUSE_BREAK.search(lowered, match.end())
        clause_end = following_boundary.start() if following_boundary else len(lowered)
        margin_clause = lowered[clause_start:clause_end]
        if "EBITDA_MARGIN_CHANGE_BPS" not in codes or re.search(
            r"\b(?:qoq|quarter.over.quarter|previous quarter)\b", margin_clause
        ):
            raise InvalidAnalysis("UNSUPPORTED_MARGIN_COMPARISON", field=field)
    for clause in _comparison_clauses(lowered):
        for subject, prefix in ((r"\brevenue\b", "REVENUE"),
                                (r"\b(?:pat|profit after tax)\b", "PAT")):
            if not re.search(subject, clause):
                continue
            for marker, suffix in ((r"\byoy\b|year.over.year|same quarter last year", "YOY"),
                                   (r"\bqoq\b|quarter.over.quarter|previous quarter", "QOQ")):
                if re.search(marker, clause) and f"{prefix}_{suffix}" not in codes:
                    code = f"{prefix}_{suffix}"
                    matching = [key for key, item in registry.items()
                                if item["kind"] == "METRIC" and item["metric_code"] == code]
                    hint = (f"Cite {matching[0]} ({code}) in this field or remove that comparison."
                            if matching else f"No {code} metric is available; remove that comparison.")
                    raise InvalidAnalysis("MISSING_COMPARISON_EVIDENCE", hint=hint, field=field)
    comparisons = [registry[item] for item in evidence_ids if registry[item]["kind"] == "METRIC"
                   and (registry[item]["metric_code"].endswith(("_YOY", "_QOQ", "_CHANGE_BPS")))]
    if len(comparisons) == 1:
        value = Decimal(comparisons[0]["value"])
        positive = re.search(r"\b(?:increas\w*|rose|grew|growth|improv\w*|expand\w*|higher)\b", lowered)
        negative = re.search(r"\b(?:decreas\w*|declin\w*|dropp?\w*|fell|contract\w*|lower)\b", lowered)
        if positive and not negative and value < 0 or negative and not positive and value > 0:
            raise InvalidAnalysis("COMPARISON_DIRECTION_MISMATCH", field=field)


def _comparison_clauses(text: str) -> list[str]:
    clauses = []
    for sentence in COMPARISON_BREAK.split(text):
        parts = re.split(r"\band\b", sentence)
        current = parts[0]
        for part in parts[1:]:
            if COMPARISON_WORD.search(current) and COMPARISON_SUBJECT.search(part):
                clauses.append(current)
                current = part
            else:
                current += " and " + part
        clauses.append(current)
    return clauses


def _check_numbers(text: str, registry: dict[str, dict], period_year: int, evidence_ids,
                   field: str) -> None:
    allowed = {Decimal(period_year), Decimal(period_year - 1), Decimal(period_year + 1),
               Decimal(period_year % 100), Decimal((period_year - 1) % 100),
               Decimal(1), Decimal(2), Decimal(3), Decimal(4)}
    supported_dates = set()
    for evidence_id in evidence_ids:
        item = registry[evidence_id]
        if item.get("period_end"):
            supported_dates.add(item["period_end"])
        if item["kind"] in {"FACT", "METRIC"}:
            _add_value(allowed, item["value"])
            if item["kind"] == "METRIC":
                for input_id in item["input_evidence_ids"]:
                    if input_id in registry:
                        input_fact = registry[input_id]
                        _add_value(allowed, input_fact["value"])
                        if input_fact.get("period_end"):
                            supported_dates.add(input_fact["period_end"])
        if item["kind"] == "MARKET_SNAPSHOT":
            for key in ("price", "pe_ratio", "market_cap_crore", "forward_eps_growth_pct", "peg_ratio"):
                if item.get(key) is not None:
                    _add_value(allowed, item[key])
            supported_dates.add(item["as_of_date"])
            allowed.update(Decimal(part) for part in item["as_of_date"].split("-"))
        for key in ("page", "document_id"):
            if item.get(key) is not None:
                allowed.add(Decimal(item[key]))
        if item.get("period_end"):
            allowed.add(Decimal(item["period_end"][:4]))
    date_spans = _supported_date_spans(text, supported_dates)
    for match in NUMBER.finditer(text):
        if any(start <= match.start() and match.end() <= end for start, end in date_spans):
            continue
        try:
            number = Decimal(match.group().replace(",", ""))
        except InvalidOperation as exc:
            raise InvalidAnalysis("UNSUPPORTED_NUMBER", match.group(), field=field) from exc
        if abs(number) not in allowed:
            matches = []
            for evidence_id, item in registry.items():
                if evidence_id in evidence_ids or item["kind"] not in {"FACT", "METRIC", "MARKET_SNAPSHOT"}:
                    continue
                values = set()
                if item["kind"] == "MARKET_SNAPSHOT":
                    for key in ("price", "pe_ratio", "market_cap_crore", "forward_eps_growth_pct", "peg_ratio"):
                        if item.get(key) is not None:
                            _add_value(values, item[key])
                else:
                    _add_value(values, item["value"])
                if abs(number) in values:
                    matches.append(f"{evidence_id} ({item.get('metric_code', item['kind'])})")
            hint = ("If this value refers to " + ", ".join(matches[:3])
                    + ", cite the matching ID in this field; otherwise remove or correct it.") if matches else None
            raise InvalidAnalysis("UNSUPPORTED_NUMBER", match.group(), hint, field)


def _supported_date_spans(text: str, iso_dates: set[str]) -> list[tuple[int, int]]:
    spans = []
    for iso_date in iso_dates:
        parsed = date.fromisoformat(iso_date)
        month = parsed.strftime("%B")
        short_month = parsed.strftime("%b")
        day = parsed.day
        year = parsed.year
        patterns = (
            rf"(?<!\w){re.escape(iso_date)}(?!\w)",
            rf"(?<!\w)(?:{month}|{short_month}\.?)\s+{day}(?:st|nd|rd|th)?\s*,?\s*{year}(?!\w)",
            rf"(?<!\w){day}(?:st|nd|rd|th)?\s+(?:{month}|{short_month}\.?)\s+{year}(?!\w)",
        )
        for pattern in patterns:
            spans.extend((match.start(), match.end()) for match in re.finditer(pattern, text, re.I))
    return spans


def _add_value(allowed: set[Decimal], value: str) -> None:
    number = abs(Decimal(value))
    for places in range(5):
        precision = Decimal(1).scaleb(-places)
        allowed.add(number.quantize(precision, rounding=ROUND_HALF_UP))
