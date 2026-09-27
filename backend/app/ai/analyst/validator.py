"""Reject invented citations, unsupported numeric claims, and unsafe analyst output."""

import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from pydantic import ValidationError

from app.ai.analyst.schemas import AnalystOutput

NUMBER = re.compile(r"(?<![A-Za-z0-9])[-+]?\d[\d,]*(?:\.\d+)?")
RECOMMENDATION = re.compile(r"\b(?:buy|sell|hold|strong buy|weak buy|conviction score)\b", re.I)


class InvalidAnalysis(ValueError):
    def __init__(self, reason: str, token: str | None = None):
        self.reason = reason
        self.token = token
        super().__init__(reason)


def validate_output(raw: str, registry: dict[str, dict], period_year: int,
                    require_extended: bool = False) -> AnalystOutput:
    try:
        result = AnalystOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise InvalidAnalysis("INVALID_JSON_OR_SCHEMA") from exc

    if not _known(result.summary_evidence_ids, registry):
        raise InvalidAnalysis("UNKNOWN_EVIDENCE_ID")
    _check_numbers(result.summary, registry, period_year, result.summary_evidence_ids)
    _check_semantics(result.summary, result.summary_evidence_ids, registry)
    all_text = [result.summary, *result.questions, *result.data_quality_notes]
    for claim in [*result.positives, *result.concerns, *result.material_changes]:
        if not _known(claim.evidence_ids, registry):
            raise InvalidAnalysis("UNKNOWN_EVIDENCE_ID")
        if hasattr(claim, "title"):
            claim_text = " ".join((claim.title, claim.explanation))
        else:
            claim_text = " ".join((claim.metric, claim.change, claim.interpretation))
        _check_numbers(claim_text, registry, period_year, claim.evidence_ids)
        _check_semantics(claim_text, claim.evidence_ids, registry)
        all_text.append(claim_text)
    if require_extended and result.what_might_happen is None:
        raise InvalidAnalysis("MISSING_OUTLOOK")
    for note in (result.management_commentary, result.what_might_happen):
        if note is None:
            continue
        if not _known(note.evidence_ids, registry):
            raise InvalidAnalysis("UNKNOWN_EVIDENCE_ID")
        _check_numbers(note.text, registry, period_year, note.evidence_ids)
        _check_semantics(note.text, note.evidence_ids, registry)
        all_text.append(note.text)
    if require_extended and (result.valuation_assessment is None or result.research_action is None):
        raise InvalidAnalysis("MISSING_VALUATION_OR_ACTION")
    valuation = result.valuation_assessment
    if valuation is not None:
        if not _known(valuation.evidence_ids, registry):
            raise InvalidAnalysis("UNKNOWN_EVIDENCE_ID")
        _check_numbers(valuation.text, registry, period_year, valuation.evidence_ids)
        _check_semantics(valuation.text, valuation.evidence_ids, registry)
        if valuation.status == "ASSESSED" and (
            not any(registry[item]["kind"] == "MARKET_SNAPSHOT" and registry[item].get("pe_ratio")
                    for item in valuation.evidence_ids)
            or not any(registry[item]["kind"] in {"FACT", "METRIC"}
                       for item in valuation.evidence_ids)):
            raise InvalidAnalysis("VALUATION_EVIDENCE_MISSING")
    action = result.research_action
    if action is not None:
        if not _known(action.evidence_ids, registry):
            raise InvalidAnalysis("UNKNOWN_EVIDENCE_ID")
        _check_numbers(action.reason, registry, period_year, action.evidence_ids)
        _check_semantics(action.reason, action.evidence_ids, registry)
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
                   registry, period_year, registry.keys())
    if RECOMMENDATION.search(" ".join(all_text)):
        raise InvalidAnalysis("RECOMMENDATION_FORBIDDEN")
    return result


def _known(ids: list[str], registry: dict[str, dict]) -> bool:
    return len(set(ids)) == len(ids) and all(item in registry for item in ids)


def _check_semantics(text: str, evidence_ids: list[str], registry: dict[str, dict]) -> None:
    lowered = text.lower()
    codes = {registry[item]["metric_code"] for item in evidence_ids
             if registry[item]["kind"] == "METRIC"}
    change_words = re.search(r"\b(?:increas\w*|decreas\w*|dropp?\w*|fell|rose|improv\w*|"
                             r"contract\w*|expand\w*|higher|lower|growth|decline\w*)\b", lowered)
    if "pat margin" in lowered and (change_words or "compared" in lowered):
        raise InvalidAnalysis("UNSUPPORTED_MARGIN_COMPARISON")
    if "ebitda margin" in lowered and (change_words or "compared" in lowered):
        if "EBITDA_MARGIN_CHANGE_BPS" not in codes or re.search(r"\b(?:qoq|quarter.over.quarter|previous quarter)\b", lowered):
            raise InvalidAnalysis("UNSUPPORTED_MARGIN_COMPARISON")
    for name, prefix in (("revenue", "REVENUE"), ("pat", "PAT")):
        if name not in lowered:
            continue
        for marker, suffix in ((r"\byoy\b|year.over.year|same quarter last year", "YOY"),
                               (r"\bqoq\b|quarter.over.quarter|previous quarter", "QOQ")):
            if re.search(marker, lowered) and f"{prefix}_{suffix}" not in codes:
                raise InvalidAnalysis("MISSING_COMPARISON_EVIDENCE")
    comparisons = [registry[item] for item in evidence_ids if registry[item]["kind"] == "METRIC"
                   and (registry[item]["metric_code"].endswith(("_YOY", "_QOQ", "_CHANGE_BPS")))]
    if len(comparisons) == 1:
        value = Decimal(comparisons[0]["value"])
        positive = re.search(r"\b(?:increas\w*|rose|grew|growth|improv\w*|expand\w*|higher)\b", lowered)
        negative = re.search(r"\b(?:decreas\w*|declin\w*|dropp?\w*|fell|contract\w*|lower)\b", lowered)
        if positive and not negative and value < 0 or negative and not positive and value > 0:
            raise InvalidAnalysis("COMPARISON_DIRECTION_MISMATCH")


def _check_numbers(text: str, registry: dict[str, dict], period_year: int, evidence_ids) -> None:
    allowed = {Decimal(period_year), Decimal(period_year - 1), Decimal(period_year + 1),
               Decimal(period_year % 100), Decimal((period_year - 1) % 100),
               Decimal(1), Decimal(2), Decimal(3), Decimal(4)}
    for evidence_id in evidence_ids:
        item = registry[evidence_id]
        if item["kind"] in {"FACT", "METRIC"}:
            _add_value(allowed, item["value"])
            if item["kind"] == "METRIC":
                for input_id in item["input_evidence_ids"]:
                    if input_id in registry:
                        _add_value(allowed, registry[input_id]["value"])
        if item["kind"] == "MARKET_SNAPSHOT":
            for key in ("price", "pe_ratio", "market_cap_crore"):
                if item.get(key) is not None:
                    _add_value(allowed, item[key])
            allowed.update(Decimal(part) for part in item["as_of_date"].split("-"))
        for key in ("page", "document_id"):
            if item.get(key) is not None:
                allowed.add(Decimal(item[key]))
        if item.get("period_end"):
            allowed.add(Decimal(item["period_end"][:4]))
    for match in NUMBER.finditer(text):
        try:
            number = Decimal(match.group().replace(",", ""))
        except InvalidOperation as exc:
            raise InvalidAnalysis("UNSUPPORTED_NUMBER", match.group()) from exc
        if abs(number) not in allowed:
            raise InvalidAnalysis("UNSUPPORTED_NUMBER", match.group())


def _add_value(allowed: set[Decimal], value: str) -> None:
    number = abs(Decimal(value))
    for places in range(5):
        precision = Decimal(1).scaleb(-places)
        allowed.add(number.quantize(precision, rounding=ROUND_HALF_UP))
