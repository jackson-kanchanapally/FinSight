"""Validate Qwen review items independently and repair only rejected items."""

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.ai.analyst.schemas import QuarterlyInvestorReview, ReviewItem, ReviewValuation
from app.ai.analyst.validator import InvalidAnalysis, validate_review_item
from app.services.materiality import materiality_details


SECTIONS = ("quarterly_investor_view", "key_developments", "management_signals",
            "what_changed", "key_risks", "watch_next_quarter")
SINGLES = ("valuation_implication", "investor_conclusion")
BOUNDS = {"quarterly_investor_view": 4, "key_developments": 6, "management_signals": 5,
          "what_changed": 5, "key_risks": 5, "watch_next_quarter": 5}


def _concept_tokens(value):
    """Normalize small wording changes while keeping section-specific verbs."""
    words = re.findall(r"[a-z]{4,}", value.lower())
    stop = {"management", "company", "quarter", "current", "reported", "disclosed",
            "would", "could", "should", "their", "these", "those", "from", "with",
            "that", "this", "into", "next", "future"}
    return {word[:-1] if word.endswith("s") else word for word in words if word not in stop}


@dataclass
class ReviewAssessment:
    review: QuarterlyInvestorReview
    accepted_count: int
    rejected: list[dict]
    diagnostics: list[dict]
    fallback_items: int


def _diagnostic(section, index, reason, detail=None, expected=None):
    return {"code": reason, "reason": reason, "field": f"quarterly_review.{section}",
            "item_index": index, "model_value": detail, "expected": expected}


def assess_review(raw, registry, period_year, deterministic_review, allowed_ids=None):
    """Reject unsafe rows, keep validated rows, fill required gaps from Python."""
    if not isinstance(raw, dict):
        raise InvalidAnalysis("INVALID_JSON_OR_SCHEMA", field="quarterly_review")
    result = {}
    rejected, diagnostics = [], []
    accepted, fallback_items = 0, 0
    accepted_objects = set()
    seen_text = set()
    seen_concepts = []

    def check(raw_item, section, index, model):
        nonlocal accepted
        try:
            item = model.model_validate(raw_item)
            if allowed_ids is not None and any(key not in allowed_ids for key in item.evidence_ids):
                raise InvalidAnalysis("EVIDENCE_NOT_IN_PACKET", field=f"quarterly_review.{section}")
            validate_review_item(item, section, registry, period_year)
            normalized = re.sub(r"\W+", " ", item.text.lower()).strip()
            if normalized in seen_text:
                raise InvalidAnalysis("DUPLICATE_REVIEW_ITEM", field=f"quarterly_review.{section}")
            tokens = _concept_tokens(item.text)
            if any((set(item.evidence_ids) & ids) and tokens and (
                   len(tokens & prior) / len(tokens | prior) >= 0.68 or
                   section == "key_developments" and prior_section == "quarterly_investor_view" and
                   len(tokens & prior) / min(len(tokens), len(prior)) >= 0.72)
                   for prior_section, ids, prior in seen_concepts if prior):
                raise InvalidAnalysis("DUPLICATE_REVIEW_ITEM", field=f"quarterly_review.{section}")
            seen_text.add(normalized)
            seen_concepts.append((section, set(item.evidence_ids), tokens))
            accepted += 1
            accepted_objects.add(id(item))
            return item
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, InvalidAnalysis):
                detail = exc.token
                reason = exc.reason
                expected = exc.expected or exc.hint
            else:
                detail = str(exc)[:250]
                reason = "INVALID_JSON_OR_SCHEMA"
                expected = None
            entry = {"section": section, "index": index, "reason": reason,
                     "expected": expected, "original": raw_item}
            rejected.append(entry)
            diagnostics.append(_diagnostic(section, index, reason, detail, expected))
            return None

    for section in SECTIONS:
        raw_rows = raw.get(section, [])
        if not isinstance(raw_rows, list):
            diagnostics.append(_diagnostic(section, -1, "INVALID_JSON_OR_SCHEMA"))
            raw_rows = []
        rows = []
        for index, raw_item in enumerate(raw_rows):
            if index >= BOUNDS[section]:
                diagnostics.append(_diagnostic(section, index, "REVIEW_ITEM_LIMIT"))
                continue
            item = check(raw_item, section, index, ReviewItem)
            if item is not None:
                rows.append(item)
        result[section] = rows

    for section in SINGLES:
        model = ReviewValuation if section == "valuation_implication" else ReviewItem
        item = check(raw.get(section), section, -1, model)
        if item is None:
            item = model.model_validate(deterministic_review[section])
            fallback_items += 1
        result[section] = item

    # Let a validated attributed development occupy a missing second opening
    # slot before filling it with deterministic prose.
    if len(result["quarterly_investor_view"]) == 1 and result["quarterly_investor_view"][0].basis == "VERIFIED_FACT":
        development = next((index for index, row in enumerate(result["key_developments"])
            if row.basis != "VERIFIED_FACT" and any(
                registry[key]["kind"] in {"CHECKPOINT_RECORD", "MANAGEMENT_STATEMENT"}
                for key in row.evidence_ids)), None)
        if development is not None:
            result["quarterly_investor_view"].append(result["key_developments"].pop(development))

    while len(result["quarterly_investor_view"]) < 2:
        index = len(result["quarterly_investor_view"])
        result["quarterly_investor_view"].append(
            ReviewItem.model_validate(deterministic_review["quarterly_investor_view"][index]))
        fallback_items += 1

    fallback_limits = {"what_changed": 2, "key_developments": 2,
                       "management_signals": 2, "key_risks": 2, "watch_next_quarter": 3}
    for section in ("what_changed", "key_developments", "management_signals",
                    "key_risks", "watch_next_quarter"):
        if not result[section] and deterministic_review.get(section):
            result[section] = [ReviewItem.model_validate(row)
                               for row in deterministic_review[section][:fallback_limits[section]]]
            fallback_items += len(result[section])

    # A valid material checkpoint should lead the investor reading when the
    # generated opening contains only two separate financial recaps. Move the
    # already validated items; never compose or alter their claims.
    if (len(result["quarterly_investor_view"]) >= 2
            and all(row.basis == "VERIFIED_FACT" for row in result["quarterly_investor_view"][:2])):
        development = next((index for index, row in enumerate(result["key_developments"])
            if row.basis != "VERIFIED_FACT" and any(
                registry[key]["kind"] in {"CHECKPOINT_RECORD", "MANAGEMENT_STATEMENT"}
                for key in row.evidence_ids)), None)
        if development is not None:
            result["quarterly_investor_view"][1], result["key_developments"][development] = (
                result["key_developments"][development], result["quarterly_investor_view"][1])

    # A safe optional section can still omit a central disclosure. Preserve
    # accepted model prose and use open slots for already evidence-checked
    # deterministic items. This is factor coverage, not a second parser.
    def factors(item):
        found = set()
        for key in item.evidence_ids:
            source = registry.get(key, {})
            if source.get("kind") == "CHECKPOINT_RECORD":
                source = registry.get(source.get("source_statement_id"), {})
            if source.get("kind") == "MANAGEMENT_STATEMENT":
                found.update(materiality_details(source.get("source_quote", ""))["factors"])
        return found

    candidates = [ReviewItem.model_validate(row) for section in (
        "key_developments", "management_signals", "key_risks")
        for row in deterministic_review.get(section, [])]
    for section, factor, maximum in (
        ("key_developments", "CAPITAL_COMMITMENT", 5),
        ("key_developments", "VERIFIED_RESULTS", 5),
        ("key_developments", "DEMAND_CLAIM", 5),
        ("management_signals", "FUNDING", 3),
        ("key_risks", "ASSET_ASSUMPTION", 3),
    ):
        rows = result[section]
        if len(rows) >= maximum:
            continue
        def matches(item):
            return (item.basis == "VERIFIED_FACT" and
                    "Revenue" in item.text) if factor == "VERIFIED_RESULTS" else factor in factors(item)
        if any(matches(item) for item in rows):
            continue
        candidate = next((item for item in candidates if matches(item)
                          and not any(item.text == existing.text for existing in rows)), None)
        if candidate is None:
            continue
        try:
            validate_review_item(candidate, section, registry, period_year)
        except InvalidAnalysis:
            continue
        rows.append(candidate)
        fallback_items += 1

    if len(result["watch_next_quarter"]) < 4:
        for raw_item in deterministic_review.get("watch_next_quarter", []):
            if len(result["watch_next_quarter"]) >= 4:
                break
            candidate = ReviewItem.model_validate(raw_item)
            if any(set(candidate.evidence_ids) & set(existing.evidence_ids)
                   for existing in result["watch_next_quarter"]):
                continue
            try:
                validate_review_item(candidate, "watch_next_quarter", registry, period_year)
            except InvalidAnalysis:
                continue
            result["watch_next_quarter"].append(candidate)
            fallback_items += 1

    # Keep the model's strongest valid claims if its total prose is excessive.
    def word_count():
        return sum(len(item.text.split()) for section in SECTIONS for item in result[section]) + sum(
            len(result[section].text.split()) for section in SINGLES)

    for section in ("management_signals", "key_risks", "key_developments", "watch_next_quarter", "what_changed"):
        while word_count() > 750 and result[section]:
            removed = result[section].pop()
            if id(removed) in accepted_objects:
                accepted -= 1
            else:
                fallback_items -= 1
            diagnostics.append(_diagnostic(section, -1, "REVIEW_NOT_CONCISE"))
    if word_count() > 750:
        raise InvalidAnalysis("REVIEW_NOT_CONCISE", field="quarterly_review")
    return ReviewAssessment(QuarterlyInvestorReview.model_validate(result), accepted,
                            rejected, diagnostics, fallback_items)


def repair_schema(rejected, allowed_ids):
    item = ReviewItem.model_json_schema()
    item["required"] = ["text", "evidence_ids", "basis", "source_quote"]
    item["properties"]["evidence_ids"]["items"] = {"type": "string", "enum": allowed_ids}
    item["properties"]["evidence_ids"]["minItems"] = 1
    item["properties"]["status"] = {"anyOf": [
        {"type": "string", "enum": ["MORE_CREDIBLE", "UNCHANGED", "LESS_CREDIBLE", "INSUFFICIENT_EVIDENCE"]},
        {"type": "null"}]}
    return {"type": "object", "additionalProperties": False, "required": ["repairs"],
        "properties": {"repairs": {"type": "array", "maxItems": min(len(rejected), 6),
            "items": {"type": "object", "additionalProperties": False,
                "required": ["section", "index", "item"],
                "properties": {"section": {"type": "string", "enum": list(dict.fromkeys(r["section"] for r in rejected))},
                    "index": {"type": "integer", "minimum": -1, "maximum": 6}, "item": item}}}}}


def repair_prompt(packet, raw, rejected, allowed_ids):
    evidence = json.loads(packet)
    cited = {key for row in rejected if isinstance(row.get("original"), dict)
             for key in row["original"].get("evidence_ids", []) if isinstance(key, str)}
    evidence["management_statements"] = [row for row in evidence.get("management_statements", [])
                                          if row["evidence_id"] in cited]
    evidence["checkpoints"] = [row for row in evidence.get("checkpoints", [])
                               if row["evidence_id"] in cited or row.get("source_statement_id") in cited]
    history = evidence.get("historical_memory") or {}
    history["passages"] = [row for row in history.get("passages", []) if row["evidence_id"] in cited]
    history["guidance"] = [row for row in history.get("guidance", [])
                           if row.get("evidence_id") in cited]
    specific = {
        "CAUSAL_BASIS_REQUIRED": "Delete the entire unsupported causal clause beginning with driven by, aided by, because of, or similar words; keep only the cited observed change.",
        "CAUSAL_BASIS_UNSUPPORTED": "Delete the causal explanation; a PAT bridge does not explain revenue or an operating cause.",
        "UNSUPPORTED_CAUSAL_EXPLANATION": "Delete the unsupported operating-cause phrase rather than rewording it.",
        "REVIEW_EVIDENCE_REQUIRED": "Cite I_REVIEW for valuation credibility or the listed research gap; copy the exact Python status.",
        "UNSUPPORTED_QUALITATIVE_STRENGTH": "Replace strong, robust, exceptional, or weak growth with the cited direction only.",
        "COMPARABLE_DATA_CONTRADICTION": "Remove the claim that prior comparable financial data are unavailable; YoY or QoQ metrics prove a comparison exists.",
        "MANAGEMENT_ATTRIBUTION_REQUIRED": "Attribute a quoted S statement explicitly to management or its named speaker.",
        "UNSUPPORTED_NUMBER": "Remove the unsupported number and any claim that depends on it.",
    }
    rules = " ".join(specific[reason] for reason in dict.fromkeys(row["reason"] for row in rejected)
                     if reason in specific)
    return ("Replace each rejected item with a DIFFERENT, shorter sentence that resolves its listed error. "
        "Do not return its original text. Return only a repairs array of section, index and replacement item. "
        "Each item needs text, evidence_ids, basis and source_quote (string or null); use causal_basis only for an actual cause claim. "
        "If citing an S management statement, copy an exact substring from its statement in the packet as source_quote "
        "and attribute it to management in text. Otherwise remove the S citation and set source_quote to null. "
        "For unsupported causes, remove the causal claim; when the cause is unknown, say so and use UNKNOWN_CAUSE. "
        "For unsupported numbers, omit the number. For valuation, remove cheap/expensive/overvaluation language "
        "and cite I_REVIEW with the packet's exact assumption_credibility_status. "
        "Do not invent a value, margin trend, commitment outcome, or valuation judgment. " + rules + "\n"
        + "REJECTED_ITEMS:" + json.dumps(rejected, ensure_ascii=False, separators=(",", ":"))
        + "\nORIGINAL_STRUCTURED_OUTPUT:" + json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        + "\nALLOWED_EVIDENCE_IDS:" + json.dumps(allowed_ids)
        + "\nRELEVANT_EVIDENCE_PACKET:" + json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))


def apply_repairs(raw, response, rejected):
    """Never let a repair change a previously validated item."""
    if not isinstance(response, dict) or not isinstance(response.get("repairs"), list):
        return raw
    allowed = {(item["section"], item["index"]) for item in rejected}
    copy = json.loads(json.dumps(raw))
    for repair in response["repairs"]:
        if not isinstance(repair, dict):
            continue
        section, index = repair.get("section"), repair.get("index")
        if (section, index) not in allowed or not isinstance(repair.get("item"), dict):
            continue
        item = dict(repair["item"])
        if section == "valuation_implication":
            if item.get("status") is None:
                continue
        else:
            item.pop("status", None)
        if index == -1:
            copy[section] = item
        elif isinstance(copy.get(section), list) and index < len(copy[section]):
            copy[section][index] = item
    return copy
