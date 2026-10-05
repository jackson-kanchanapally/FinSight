"""Reject invented citations, unsupported numeric claims, and unsafe analyst output."""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from pydantic import ValidationError

from app.ai.analyst.schemas import AnalystOutput

NUMBER = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:,\d+)*(?:\.\d+)?")
RECOMMENDATION = re.compile(
    r"\b(?:buy|sell|hold|strong buy|weak buy|conviction score)\b", re.I
)
VALUE_JUDGMENT = re.compile(
    r"\b(?:overvalued|undervalued|overpriced|underpriced|cheap|expensive|fairly valued|fair value|overvaluation|undervaluation)\b",
    re.I,
)
UNSUPPORTED_CAUSE = re.compile(
    r"\b(?:continued demand|weak demand|pricing power|cost control|market saturation|"
    r"operational (?:in)?efficien\w*|strong order book|customer weakness|market.share gain|"
    r"competitive advantage|temporary issue|one.time (?:issue|charge|benefit)|"
    r"operating leverage|demand improvement)\b",
    re.I,
)
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
COMPARISON_WORD = re.compile(
    r"\byoy\b|year.over.year|same quarter last year|"
    r"\bqoq\b|quarter.over.quarter|previous quarter",
    re.I,
)
COMPARISON_SUBJECT = re.compile(r"\b(?:revenue|pat|profit after tax)\b", re.I)


class InvalidAnalysis(ValueError):
    def __init__(
        self,
        reason: str,
        token: str | None = None,
        hint: str | None = None,
        field: str | None = None,
        expected=None,
    ):
        self.reason = reason
        self.token = token
        self.hint = hint
        self.field = field
        self.expected = expected
        super().__init__(reason)

    def diagnostic(self) -> dict:
        code = {
            "INVALID_JSON_OR_SCHEMA": "SCHEMA_VALIDATION_ERROR",
            "UNKNOWN_EVIDENCE_ID": "INVALID_EVIDENCE_ID",
            "NUMERICAL_RELATIONSHIP_MISMATCH": "NUMERIC_MISMATCH",
            "UNSUPPORTED_CAUSAL_EXPLANATION": "UNSUPPORTED_CAUSAL_CLAIM",
            "UNSUPPORTED_VALUATION_JUDGMENT": "UNSUPPORTED_VALUATION_CLAIM",
        }.get(self.reason, self.reason)
        if self.reason.startswith("MISSING_"):
            code = "MISSING_REQUIRED_FIELD"
        return {
            "code": code,
            "reason": self.reason,
            "field": self.field,
            "model_value": self.token,
            "expected": self.expected or self.hint,
        }


def citation_feedback(
    raw: str, registry: dict[str, dict], period_year: int
) -> list[str]:
    """Report numeric and ID mistakes across fields so one retry can fix them together."""
    try:
        result = AnalystOutput.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, TypeError):
        return []
    fields = [("summary", result.summary, result.summary_evidence_ids)]
    for name, claims in (
        ("positives", result.positives),
        ("concerns", result.concerns),
        ("material_changes", result.material_changes),
    ):
        fields.extend(
            (f"{name}[{index}]", _claim_text(claim), claim.evidence_ids)
            for index, claim in enumerate(claims)
        )
    for name in (
        "management_commentary",
        "what_might_happen",
        "result_view",
        "valuation_assessment",
        "research_action",
    ):
        item = getattr(result, name)
        if item is not None:
            fields.append(
                (
                    name,
                    item.text if hasattr(item, "text") else item.reason,
                    item.evidence_ids,
                )
            )
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


def validate_output(
    raw: str,
    registry: dict[str, dict],
    period_year: int,
    require_extended: bool = False,
    model_response: bool = False,
) -> AnalystOutput:
    try:
        parsed = json.loads(raw)
        if (
            model_response
            and isinstance(parsed, dict)
            and parsed.get("investor_report") is not None
        ):
            raise InvalidAnalysis(
                "DETERMINISTIC_FIELD_FORBIDDEN",
                field="investor_report",
                expected="Server-generated deterministic judgments",
            )
        result = AnalystOutput.model_validate(parsed)
    except ValidationError as exc:
        error = exc.errors()[0]
        raise InvalidAnalysis(
            (
                "MISSING_REQUIRED_FIELD"
                if error["type"] == "missing"
                else "INVALID_JSON_OR_SCHEMA"
            ),
            token=str(error.get("input"))[:500],
            field=".".join(str(part) for part in error["loc"]),
            expected=error["msg"],
        ) from exc
    except (json.JSONDecodeError, TypeError) as exc:
        raise InvalidAnalysis("INVALID_JSON_OR_SCHEMA") from exc

    _require_known(result.summary_evidence_ids, registry, "summary")
    _check_numbers(
        result.summary, registry, period_year, result.summary_evidence_ids, "summary"
    )
    _check_semantics(result.summary, result.summary_evidence_ids, registry, "summary")
    all_text = [result.summary, *result.questions, *result.data_quality_notes]
    for note in result.historical_context:
        _require_known(note.evidence_ids, registry, "historical_context")
        sources = [registry[key] for key in note.evidence_ids if registry[key]["kind"] == "MEMORY_CHUNK"]
        if not sources or not any(note.source_quote in item.get("excerpt", "") for item in sources):
            raise InvalidAnalysis("HISTORICAL_QUOTE_NOT_IN_SOURCE", field="historical_context")
        _check_numbers(note.text, registry, period_year, note.evidence_ids, "historical_context")
        _check_semantics(note.text, note.evidence_ids, registry, "historical_context")
        all_text.append(note.text)
    for field, claims in (
        ("positives", result.positives),
        ("concerns", result.concerns),
        ("material_changes", result.material_changes),
    ):
        for claim in claims:
            _validate_claim_numbers(claim, field, registry, period_year)
            _check_semantics(_claim_text(claim), claim.evidence_ids, registry, field)
            all_text.append(_claim_text(claim))
    if require_extended and result.what_might_happen is None:
        raise InvalidAnalysis("MISSING_OUTLOOK")
    for field, note in (
        ("management_commentary", result.management_commentary),
        ("what_might_happen", result.what_might_happen),
    ):
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
        _check_numbers(
            view.reason, registry, period_year, view.evidence_ids, "result_view"
        )
        _check_semantics(view.reason, view.evidence_ids, registry, "result_view")
        comparisons = {
            key: Decimal(item["value"])
            for key, item in registry.items()
            if item["kind"] == "METRIC"
            and item["metric_code"]
            in {"REVENUE_YOY", "REVENUE_QOQ", "PAT_YOY", "PAT_QOQ"}
        }
        values = [
            value for key, value in comparisons.items() if key in view.evidence_ids
        ]
        if comparisons and not values:
            raise InvalidAnalysis("RESULT_VIEW_EVIDENCE_MISSING", field="result_view")
        if values and (
            (all(value > 0 for value in values) and view.sentiment == "NEGATIVE")
            or (all(value < 0 for value in values) and view.sentiment == "POSITIVE")
            or (all(value == 0 for value in values) and view.sentiment != "NEUTRAL")
        ):
            raise InvalidAnalysis("RESULT_VIEW_DIRECTION_MISMATCH", field="result_view")
        all_text.append(view.reason)
    if require_extended and (
        result.valuation_assessment is None or result.research_action is None
    ):
        raise InvalidAnalysis("MISSING_VALUATION_OR_ACTION")
    valuation = result.valuation_assessment
    if valuation is not None:
        _require_known(valuation.evidence_ids, registry, "valuation_assessment")
        _check_numbers(
            valuation.text,
            registry,
            period_year,
            valuation.evidence_ids,
            "valuation_assessment",
        )
        _check_semantics(
            valuation.text, valuation.evidence_ids, registry, "valuation_assessment"
        )
        if (
            valuation.status == "INSUFFICIENT_DATA"
            and valuation.verdict != "INCONCLUSIVE"
        ):
            raise InvalidAnalysis(
                "VALUATION_VERDICT_UNSUPPORTED",
                token=valuation.verdict,
                field="valuation_assessment.verdict",
                expected="INCONCLUSIVE when status is INSUFFICIENT_DATA",
            )
        if valuation.status == "ASSESSED" and (
            not any(
                registry[item]["kind"] == "VALUATION"
                and registry[item].get("metric_code")
                in {"PE_TTM", "PS_TTM", "EV_EBITDA_TTM", "PEG_FORWARD"}
                for item in valuation.evidence_ids
            )
            or not any(
                registry[item]["kind"] in {"FACT", "METRIC"}
                for item in valuation.evidence_ids
            )
        ):
            raise InvalidAnalysis("VALUATION_EVIDENCE_MISSING")
        if (
            valuation.status == "ASSESSED"
            and any(
                item["kind"] == "METRIC"
                and item["metric_code"].endswith(("_YOY", "_QOQ"))
                for item in registry.values()
            )
            and not any(
                registry[item]["kind"] == "METRIC"
                and registry[item]["metric_code"].endswith(("_YOY", "_QOQ"))
                for item in valuation.evidence_ids
            )
        ):
            raise InvalidAnalysis("VALUATION_EVIDENCE_MISSING")
    action = result.research_action
    if action is not None:
        _require_known(action.evidence_ids, registry, "research_action")
        _check_numbers(
            action.reason, registry, period_year, action.evidence_ids, "research_action"
        )
        _check_semantics(
            action.reason, action.evidence_ids, registry, "research_action"
        )
        all_text.append(action.reason)
    if valuation is not None:
        all_text.append(valuation.text)
    quality = result.quality_report
    if quality is not None:
        notes = [
            quality.operating_performance,
            quality.seasonality_context,
            quality.earnings_quality,
            quality.valuation_expectations,
            quality.results_vs_valuation,
            *quality.strengthening_evidence,
            *quality.weakening_evidence,
        ]
        for note in notes:
            _require_known(note.evidence_ids, registry, "quality_report")
            _check_numbers(
                note.text, registry, period_year, note.evidence_ids, "quality_report"
            )
            _check_semantics(note.text, note.evidence_ids, registry, "quality_report")
            all_text.append(note.text)
        for gap in quality.research_gaps:
            _require_known(gap.evidence_ids, registry, "research_gaps")
            _check_numbers(
                gap.question, registry, period_year, gap.evidence_ids, "research_gaps"
            )
            all_text.append(gap.question)
    if result.management_commentary and not any(
        registry[item].get("kind") in {"DOCUMENT_PAGE", "MANAGEMENT_STATEMENT", "MEMORY_CHUNK"}
        and re.search(
            r"\b(?:management|director|ceo|cfo|chairman|guidance|outlook)\b",
            registry[item].get("excerpt", ""),
            re.I,
        )
        for item in result.management_commentary.evidence_ids
    ):
        raise InvalidAnalysis("MANAGEMENT_NOT_IN_SOURCE")
    _check_numbers(
        " ".join([result.summary, *result.questions, *result.data_quality_notes]),
        registry,
        period_year,
        registry.keys(),
        "questions_or_data_quality_notes",
    )
    if RECOMMENDATION.search(" ".join(all_text)):
        raise InvalidAnalysis("RECOMMENDATION_FORBIDDEN")
    if result.quarterly_review is not None:
        validate_quarterly_review(result.quarterly_review, registry, period_year)
    for text in [*result.questions, *result.data_quality_notes]:
        _check_valuation_judgment(text, [], registry, "questions_or_data_quality_notes")
    for phrase in UNSUPPORTED_CAUSE.finditer(" ".join(all_text)):
        if not any(
            item["kind"] in {"DOCUMENT_PAGE", "MANAGEMENT_STATEMENT", "MEMORY_CHUNK"}
            and phrase.group().lower() in item.get("excerpt", "").lower()
            for item in registry.values()
        ):
            raise InvalidAnalysis(
                "UNSUPPORTED_CAUSAL_EXPLANATION", token=phrase.group()
            )
    return result


def _claim_text(claim) -> str:
    if hasattr(claim, "title"):
        return " ".join((claim.title, claim.explanation))
    return " ".join((claim.metric, claim.change, claim.interpretation))


def validate_quarterly_review(review, registry, period_year):
    sections = ("quarterly_investor_view", "key_developments", "management_signals",
                "what_changed", "key_risks", "watch_next_quarter")
    notes = [(section, note) for section in sections for note in getattr(review, section)]
    notes += [("valuation_implication", review.valuation_implication), ("investor_conclusion", review.investor_conclusion)]
    if sum(len(note.text.split()) for _, note in notes) > 750:
        raise InvalidAnalysis("REVIEW_NOT_CONCISE", field="quarterly_review")
    for section, note in notes:
        validate_review_item(note, section, registry, period_year)


def validate_review_item(note, section, registry, period_year):
    """Apply the existing strict checks to one generated investor claim."""
    field = "quarterly_review." + section
    if section == "valuation_implication":
        expected = registry.get("I_REVIEW", {}).get("valuation_implication_status")
        if expected and note.status != expected:
            raise InvalidAnalysis("DETERMINISTIC_FIELD_MISMATCH", field=field, expected=expected)
        if expected and "I_REVIEW" not in note.evidence_ids:
            raise InvalidAnalysis("REVIEW_EVIDENCE_REQUIRED", field=field, expected="I_REVIEW")
    if not note.evidence_ids:
        raise InvalidAnalysis("REVIEW_EVIDENCE_REQUIRED", field=field)
    _require_known(note.evidence_ids, registry, field)
    _check_numbers(note.text, registry, period_year, note.evidence_ids, field)
    _check_semantics(note.text, note.evidence_ids, registry, field)
    if RECOMMENDATION.search(note.text):
        raise InvalidAnalysis("RECOMMENDATION_FORBIDDEN", field=field)
    sources = [registry[k] for k in note.evidence_ids]
    statements = [s for s in sources if s["kind"] == "MANAGEMENT_STATEMENT"]
    linked_statements = [registry[s["source_statement_id"]] for s in sources
        if s["kind"] == "CHECKPOINT_RECORD" and s.get("source_statement_id") in registry]
    attributable = statements + linked_statements
    memory = [s for s in sources if s["kind"] == "MEMORY_CHUNK"]
    if note.source_quote and not any(note.source_quote in s.get("source_quote", s.get("excerpt", "")) for s in attributable + memory):
        raise InvalidAnalysis("MANAGEMENT_QUOTE_NOT_IN_SOURCE" if attributable else "QUOTE_NOT_IN_CITED_EVIDENCE", field=field)
    if memory and not statements and (not note.source_quote or not any(note.source_quote in s.get("excerpt", "") for s in memory)):
        raise InvalidAnalysis("HISTORICAL_QUOTE_NOT_IN_SOURCE", field=field)
    if note.basis == "VERIFIED_FACT" and (not any(s["kind"] in {"FACT", "METRIC", "VALUATION"} for s in sources)
            or any(s["kind"] in {"MANAGEMENT_STATEMENT", "MEMORY_CHUNK", "CHECKPOINT_RECORD"} for s in sources)):
        raise InvalidAnalysis("MANAGEMENT_CLAIM_IS_NOT_FACT", field=field)
    if note.basis == "VERIFIED_FACT" and re.search(
        r"\b(?:suggests?|suggested|suggesting|may|might|could|possible|potential)\b", note.text, re.I
    ):
        raise InvalidAnalysis("INTERPRETATION_MISCLASSIFIED", field=field)
    attributed = bool(re.search(r"management|company (?:says|said|expects|targets|plans)|according to", note.text, re.I))
    attributed = attributed or any(s.get("speaker_name") and re.search(
        r"\b" + re.escape(s["speaker_name"]) + r"\b", note.text, re.I)
        for s in attributable)
    if note.basis == "MANAGEMENT_CLAIM" or statements:
        if not note.source_quote or not any(note.source_quote in s.get("source_quote", s.get("excerpt", "")) for s in attributable):
            raise InvalidAnalysis("MANAGEMENT_QUOTE_NOT_IN_SOURCE", field=field)
        if not attributed:
            raise InvalidAnalysis("MANAGEMENT_ATTRIBUTION_REQUIRED", field=field)
    # Causal language needs a declared evidence basis. Arithmetic PAT bridges
    # establish component contributions, not explanations of operations.
    causal = note.causal_basis
    if causal == "ESTABLISHED_CAUSE":
        if note.basis != "VERIFIED_FACT" or not any(
            s.get("metric_code", "").startswith("PAT_BRIDGE_") for s in sources
        ):
            raise InvalidAnalysis("CAUSAL_BASIS_UNSUPPORTED", field=field)
        if re.search(r"\brevenue\b[^.;]{0,90}\b(?:driven by|due to|because of|aided by|boosted by)\b",
                     note.text, re.I):
            raise InvalidAnalysis("CAUSAL_BASIS_UNSUPPORTED", field=field)
    elif causal == "MANAGEMENT_EXPLANATION":
        if not attributable or not note.source_quote or not attributed:
            raise InvalidAnalysis("CAUSAL_ATTRIBUTION_REQUIRED", field=field)
    elif causal == "INVESTOR_HYPOTHESIS":
        if note.basis != "INTERPRETATION" or not re.search(r"\b(could|may|might|if|would|depends|requires?)\b", note.text, re.I):
            raise InvalidAnalysis("CAUSAL_HYPOTHESIS_NOT_CONDITIONAL", field=field)
    elif causal == "UNKNOWN_CAUSE":
        if not re.search(r"\b(unknown|unverified|not established|not known|cannot establish|unclear)\b", note.text, re.I):
            raise InvalidAnalysis("UNKNOWN_CAUSE_NOT_STATED", field=field)
    elif re.search(r"\b(driven by|due to|because of|resulted from|caused by|aided by|supported by|helped by|boosted by|lifted by)\b", note.text, re.I):
        raise InvalidAnalysis("CAUSAL_BASIS_REQUIRED", field=field)
    for match in re.finditer(r"\b(delivered|missed|delayed|withdrawn|on track)\b", note.text, re.I):
        status = match[1].upper().replace(" ", "_")
        records = [s for s in sources if s["kind"] == "CHECKPOINT_RECORD" and s["status"] == status]
        # A directly quoted reported delay is a claim, not a checkpoint outcome.
        reported_delay = status == "DELAYED" and note.basis == "MANAGEMENT_CLAIM" and re.search(r"delay|postpon", note.source_quote or "", re.I)
        if not records and not reported_delay:
            raise InvalidAnalysis("CHECKPOINT_OUTCOME_UNSUPPORTED", field=field)
        if re.search(r"repeated|consistently|recurring", note.text, re.I) and len({r.get("target_period") for r in records}) < 2:
            raise InvalidAnalysis("HISTORICAL_EXECUTION_UNSUPPORTED", field=field)
    if section == "what_changed":
        known = registry.get("I_REVIEW", {}).get("changes", [])
        supported = any(set(item["evidence_ids"]).issubset(note.evidence_ids) for item in known)
        dated = {s.get("published_at", "")[:10] for s in sources if s["kind"] in {"MEMORY_CHUNK", "MANAGEMENT_STATEMENT"}} - {""}
        if not supported and len(dated) < 2:
            raise InvalidAnalysis("HISTORICAL_CHANGE_UNSUPPORTED", field=field)

def _validate_claim_numbers(
    claim, field: str, registry: dict[str, dict], period_year: int
) -> None:
    _require_known(claim.evidence_ids, registry, field)
    _check_numbers(_claim_text(claim), registry, period_year, claim.evidence_ids, field)


def _require_known(ids: list[str], registry: dict[str, dict], field: str) -> None:
    missing = next((item for item in ids if item not in registry), None)
    if missing:
        hint = (
            "No market snapshot was supplied; use an empty valuation evidence_ids array."
            if field == "valuation_assessment" and missing == "market_snapshot"
            else "Use only evidence IDs listed in the context registry for this field."
        )
        raise InvalidAnalysis(
            "UNKNOWN_EVIDENCE_ID", token=missing, hint=hint, field=field
        )
    if len(set(ids)) != len(ids):
        raise InvalidAnalysis(
            "UNKNOWN_EVIDENCE_ID", hint="Remove duplicate evidence IDs.", field=field
        )


def _check_valuation_judgment(
    text: str, evidence_ids: list[str], registry: dict[str, dict], field: str
) -> None:
    judgments = list(VALUE_JUDGMENT.finditer(text))
    if not judgments:
        return
    verdicts = {
        registry[key].get("valuation_verdict")
        for key in evidence_ids
        if registry[key]["kind"] == "SCENARIO"
    } - {None, "INSUFFICIENT_EVIDENCE"}
    expected = (
        next(iter(verdicts)).lower().replace("_", " ") if len(verdicts) == 1 else None
    )
    spans = list(re.finditer(re.escape(expected), text, re.I)) if expected else []
    supported = (
        spans
        and re.search(
            r"\b(?:scenario|assumption|conditional|tested range)s?\b", text, re.I
        )
        and not re.search(
            r"\b(?:not|never|isn't|isn’t)\b.{0,50}" + re.escape(expected), text, re.I
        )
        and all(
            any(
                span.start() <= match.start() and span.end() >= match.end()
                for span in spans
            )
            for match in judgments
        )
    )
    if not supported:
        raise InvalidAnalysis(
            "UNSUPPORTED_VALUATION_JUDGMENT",
            field=field,
            token=judgments[0].group(),
            expected=f"Cite a scenario ID and reproduce its conditional deterministic verdict: {expected or 'unavailable'}",
        )


def _check_semantics(
    text: str, evidence_ids: list[str], registry: dict[str, dict], field: str
) -> None:
    _check_valuation_judgment(text, evidence_ids, registry, field)
    if any(registry[key]["kind"] in {"MEMORY_CHUNK", "GUIDANCE_RECORD"} for key in evidence_ids):
        for claim in re.finditer(r"\b(missed|achieved|delivered|exceeded)\b", text, re.I):
            required = {"missed": {"MISSED"}, "achieved": {"ACHIEVED", "EXCEEDED"},
                        "delivered": {"ACHIEVED", "EXCEEDED"}, "exceeded": {"EXCEEDED"}}[claim.group().lower()]
            outcomes = [registry[key] for key in evidence_ids if registry[key]["kind"] == "GUIDANCE_RECORD"
                        and registry[key].get("execution_outcome") in required]
            periods = {item["target_period"] for item in outcomes}
            minimum = 2 if re.search(r"\b(?:repeatedly|consistently|recurring)\b", text, re.I) else 1
            if len(periods) < minimum:
                raise InvalidAnalysis("HISTORICAL_EXECUTION_UNSUPPORTED", field=field)
    for phrase in UNSUPPORTED_CAUSE.finditer(text):
        if not any(registry[key]["kind"] in {"DOCUMENT_PAGE", "MANAGEMENT_STATEMENT", "MEMORY_CHUNK"}
                   and phrase.group().lower() in registry[key].get("excerpt", "").lower() for key in evidence_ids):
            raise InvalidAnalysis("UNSUPPORTED_CAUSAL_EXPLANATION", token=phrase.group(), field=field)
    lowered = text.lower()
    if field.startswith("quarterly_review.") and re.search(
        r"\bno (?:(?:prior|previous)\s+)?comparable (?:data|period|results)\b", lowered
    ) and any(item["kind"] == "METRIC" and item.get("metric_code", "").endswith(("_YOY", "_QOQ"))
              for item in registry.values()):
        raise InvalidAnalysis("COMPARABLE_DATA_CONTRADICTION", field=field)
    if field.startswith("quarterly_review.") and re.search(
        r"\b(?:strong|robust|exceptional|weak)\s+(?:(?:yoy|qoq|revenue|pat|earnings)\s+)*growth\b",
        lowered,
    ):
        raise InvalidAnalysis("UNSUPPORTED_QUALITATIVE_STRENGTH", field=field)
    if field.startswith("quarterly_review.") and re.search(
        r"\b(?:unavailable|not (?:separately )?verified|too short|insufficient coverage)\b", lowered
    ):
        gap_text = " ".join(registry.get("I_REVIEW", {}).get("research_gaps", [])).lower()
        gap_topics = ("cash conversion", "working-capital", "historical valuation",
                      "representative p/e range", "peer valuation", "peer or quality premium")
        for topic in gap_topics:
            if topic in lowered:
                if "I_REVIEW" not in evidence_ids or topic not in gap_text:
                    raise InvalidAnalysis("RESEARCH_GAP_UNSUPPORTED", token=topic, field=field)
    codes = {
        registry[item]["metric_code"]
        for item in evidence_ids
        if registry[item]["kind"] == "METRIC"
    }
    pe_reference = registry.get("R_PE")
    if (
        pe_reference
        and pe_reference["relationship"] == "BELOW"
        and re.search(
            r"(?:calculated|almonds|trailing|ttm)\s+p/e.{0,110}(?:higher|above|greater)\s+than.{0,45}reference",
            lowered,
        )
    ):
        raise InvalidAnalysis(
            "NUMERICAL_RELATIONSHIP_MISMATCH",
            hint="Calculated P/E is below the reference P/E; this is only a calculation cross-check.",
            field=field,
        )
    if (
        pe_reference
        and pe_reference["relationship"] == "ABOVE"
        and re.search(
            r"(?:calculated|almonds|trailing|ttm)\s+p/e.{0,110}(?:lower|below|less)\s+than.{0,45}reference",
            lowered,
        )
    ):
        raise InvalidAnalysis(
            "NUMERICAL_RELATIONSHIP_MISMATCH",
            hint="Calculated P/E is above the reference P/E; this is only a calculation cross-check.",
            field=field,
        )
    margin = registry.get("R_MARGIN")
    if margin and re.search(r"ebitda margin", lowered):
        for match in re.finditer(r"([-+]?\d+(?:\.\d+)?)\s+percentage points?", lowered):
            stated = abs(Decimal(match.group(1)))
            actual = abs(Decimal(margin["change_percentage_points"]))
            if abs(stated - actual) > Decimal("0.005"):
                raise InvalidAnalysis(
                    "MARGIN_UNIT_MISMATCH",
                    token=match.group(0),
                    hint=f"Verified change is {margin['change_percentage_points']} percentage points or {margin['change_bps']} basis points.",
                    field=field,
                )
    for match in MARGIN_CHANGE.finditer(lowered):
        phrase = match.group()
        if "pat margin" in phrase:
            raise InvalidAnalysis("UNSUPPORTED_MARGIN_COMPARISON", field=field)
        clause_start = max(
            (
                boundary.end()
                for boundary in CLAUSE_BREAK.finditer(lowered, 0, match.start())
            ),
            default=0,
        )
        following_boundary = CLAUSE_BREAK.search(lowered, match.end())
        clause_end = following_boundary.start() if following_boundary else len(lowered)
        margin_clause = lowered[clause_start:clause_end]
        if "EBITDA_MARGIN_CHANGE_BPS" not in codes or re.search(
            r"\b(?:qoq|quarter.over.quarter|previous quarter)\b", margin_clause
        ):
            raise InvalidAnalysis("UNSUPPORTED_MARGIN_COMPARISON", field=field)
    for clause in _comparison_clauses(lowered):
        for code in _comparison_codes(clause):
            if code in codes:
                continue
            matching = [
                key
                for key, item in registry.items()
                if item["kind"] == "METRIC" and item["metric_code"] == code
            ]
            hint = (
                f"Cite {matching[0]} ({code}) in this field or remove that comparison."
                if matching
                else f"No {code} metric is available; remove that comparison."
            )
            raise InvalidAnalysis("MISSING_COMPARISON_EVIDENCE", hint=hint, field=field)
    comparisons = [
        registry[item]
        for item in evidence_ids
        if registry[item]["kind"] == "METRIC"
        and (registry[item]["metric_code"].endswith(("_YOY", "_QOQ", "_CHANGE_BPS")))
    ]
    if len(comparisons) == 1:
        value = Decimal(comparisons[0]["value"])
        positive = re.search(
            r"\b(?:increas\w*|rose|grew|growth|improv\w*|expand\w*|higher)\b", lowered
        )
        negative = re.search(
            r"\b(?:decreas\w*|declin\w*|dropp?\w*|fell|contract\w*|lower)\b", lowered
        )
        if (
            positive
            and not negative
            and value < 0
            or negative
            and not positive
            and value > 0
        ):
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


def _comparison_codes(clause: str) -> set[str]:
    """Attach each comparison marker to its subject, not every noun in the clause."""
    subjects = [
        (match.start(), match.end(), "REVENUE" if match.group() == "revenue" else "PAT")
        for match in COMPARISON_SUBJECT.finditer(clause)
    ]
    required = set()
    for marker in COMPARISON_WORD.finditer(clause):
        suffix = (
            "YOY"
            if re.fullmatch(
                r"yoy|year.over.year|same quarter last year", marker.group()
            )
            else "QOQ"
        )
        before = [subject for subject in subjects if subject[1] <= marker.start()]
        if before:
            chosen = before[-1]
            required.add(f"{chosen[2]}_{suffix}")
            # "Revenue and PAT grew YoY" makes the comparison about both subjects.
            for prior in reversed(before[:-1]):
                if not re.fullmatch(r"\s+and\s+", clause[prior[1] : chosen[0]]):
                    break
                required.add(f"{prior[2]}_{suffix}")
                chosen = prior
        else:
            after = next(
                (subject for subject in subjects if subject[0] >= marker.end()), None
            )
            if after:
                required.add(f"{after[2]}_{suffix}")
    return required


def _check_numbers(
    text: str, registry: dict[str, dict], period_year: int, evidence_ids, field: str
) -> None:
    allowed = {
        Decimal(period_year),
        Decimal(period_year - 1),
        Decimal(period_year + 1),
        Decimal(period_year % 100),
        Decimal((period_year - 1) % 100),
        Decimal(1),
        Decimal(2),
        Decimal(3),
        Decimal(4),
    }
    negative_allowed: set[Decimal] = set()
    supported_dates = set()
    for evidence_id in evidence_ids:
        item = registry[evidence_id]
        if item.get("period_end"):
            supported_dates.add(item["period_end"])
        if item["kind"] in {"MEMORY_CHUNK", "GUIDANCE_RECORD"} and item.get("published_at"):
            supported_dates.add(item["published_at"][:10])
        if item["kind"] == "GUIDANCE_RECORD":
            for key in ("lower_bound", "upper_bound", "actual_value"):
                if item.get(key) is not None:
                    _add_value(allowed, item[key])
                    _add_negative_value(negative_allowed, item[key])
        if item["kind"] == "CHECKPOINT_RECORD":
            for value in [*item.get("target", {}).values(), item.get("actual_outcome", {}).get("actual_value")]:
                if value is not None:
                    _add_value(allowed, str(value))
                    _add_negative_value(negative_allowed, str(value))
            if item.get("target_date"):
                supported_dates.add(item["target_date"])
        if item["kind"] in {"FACT", "METRIC"}:
            _add_value(allowed, item["value"])
            _add_negative_value(negative_allowed, item["value"])
            if item["kind"] == "METRIC":
                for input_id in item["input_evidence_ids"]:
                    if input_id in registry:
                        input_fact = registry[input_id]
                        _add_value(allowed, input_fact["value"])
                        _add_negative_value(negative_allowed, input_fact["value"])
                        if input_fact.get("period_end"):
                            supported_dates.add(input_fact["period_end"])
        if item["kind"] == "MARKET_SNAPSHOT":
            for key in (
                "price",
                "pe_ratio",
                "market_cap_crore",
                "forward_eps_growth_pct",
                "peg_ratio",
            ):
                if item.get(key) is not None:
                    _add_value(allowed, item[key])
                    _add_negative_value(negative_allowed, item[key])
            supported_dates.add(item["as_of_date"])
            allowed.update(Decimal(part) for part in item["as_of_date"].split("-"))
        if item["kind"] == "VALUATION":
            _add_value(allowed, item["value"])
            _add_negative_value(negative_allowed, item["value"])
            supported_dates.add(item["as_of_date"])
        if item["kind"] == "SCENARIO":
            for value in [
                item["value"],
                *[
                    item["assumptions"][key]
                    for key in (
                        "eps_growth_pct",
                        "terminal_pe",
                        "years",
                        "required_return_pct",
                    )
                ],
                *[
                    value
                    for key, value in (item.get("reverse") or {}).items()
                    if key != "label"
                ],
            ]:
                _add_value(allowed, str(value))
                _add_negative_value(negative_allowed, str(value))
        if item["kind"] == "RELATIONSHIP":
            for key in (
                "primary_value",
                "reference_value",
                "absolute_difference",
                "percentage_difference",
                "previous_margin_pct",
                "current_margin_pct",
                "change_percentage_points",
                "change_bps",
            ):
                if item.get(key) is not None:
                    _add_value(allowed, item[key])
                    _add_negative_value(negative_allowed, item[key])
        if item["kind"] == "SEASONALITY":
            for key in (
                "current_change_pct",
                "median_change_pct",
                "difference_from_median_pct",
            ):
                if item.get(key) is not None:
                    _add_value(allowed, item[key])
                    _add_negative_value(negative_allowed, item[key])
            for value in item.get("range_pct") or []:
                _add_value(allowed, value)
                _add_negative_value(negative_allowed, value)
        for key in ("page", "document_id"):
            if item.get(key) is not None:
                allowed.add(Decimal(item[key]))
        if item.get("period_end"):
            allowed.add(Decimal(item["period_end"][:4]))
    date_spans = _supported_date_spans(text, supported_dates)
    for match in NUMBER.finditer(text):
        if any(
            start <= match.start() and match.end() <= end for start, end in date_spans
        ):
            continue
        try:
            number = Decimal(match.group().replace(",", ""))
        except InvalidOperation as exc:
            raise InvalidAnalysis(
                "UNSUPPORTED_NUMBER", match.group(), field=field
            ) from exc
        if (
            abs(number) not in allowed
            or number < 0
            and abs(number) not in negative_allowed
        ):
            matches = []
            for evidence_id, item in registry.items():
                if evidence_id in evidence_ids or item["kind"] not in {
                    "FACT",
                    "METRIC",
                    "MARKET_SNAPSHOT",
                    "VALUATION",
                }:
                    continue
                values = set()
                if item["kind"] == "MARKET_SNAPSHOT":
                    for key in (
                        "price",
                        "pe_ratio",
                        "market_cap_crore",
                        "forward_eps_growth_pct",
                        "peg_ratio",
                    ):
                        if item.get(key) is not None:
                            _add_value(values, item[key])
                elif item["kind"] == "VALUATION":
                    _add_value(values, item["value"])
                else:
                    _add_value(values, item["value"])
                if abs(number) in values:
                    matches.append(
                        f"{evidence_id} ({item.get('metric_code', item['kind'])})"
                    )
            hint = (
                (
                    "If this value refers to "
                    + ", ".join(matches[:3])
                    + ", cite the matching ID with its correct direction; otherwise remove or correct it."
                )
                if matches
                else None
            )
            if not hint:
                hint = _nearby_comparison_hint(number, registry)
            raise InvalidAnalysis(
                "UNSUPPORTED_NUMBER",
                match.group(),
                hint,
                field,
                expected={
                    "hint": hint,
                    "allowed_values": sorted(str(value) for value in allowed)[:60],
                },
            )


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
            spans.extend(
                (match.start(), match.end())
                for match in re.finditer(pattern, text, re.I)
            )
    return spans


def _add_value(allowed: set[Decimal], value: str) -> None:
    number = abs(Decimal(value))
    allowed.add(
        number
    )  # Exact authoritative values are valid at their stored precision.
    for places in range(5):
        precision = Decimal(1).scaleb(-places)
        allowed.add(number.quantize(precision, rounding=ROUND_HALF_UP))


def _add_negative_value(allowed: set[Decimal], value: str) -> None:
    if Decimal(value) < 0:
        _add_value(allowed, value)


def _nearby_comparison_hint(number: Decimal, registry: dict[str, dict]) -> str | None:
    comparisons = [
        (key, item, Decimal(item["value"]))
        for key, item in registry.items()
        if item["kind"] == "METRIC"
        and item["metric_code"].endswith(("_YOY", "_QOQ", "_CHANGE_BPS"))
    ]
    close = [
        (abs(abs(number) - abs(value)), key, item, value)
        for key, item, value in comparisons
        if abs(abs(number) - abs(value))
        <= max(Decimal("0.2"), abs(value) * Decimal("0.02"))
    ]
    if not close:
        return None
    _, key, item, value = min(close, key=lambda candidate: candidate[0])
    direction = "+" if value > 0 else ""
    return (
        f"Nearest verified comparison: {key} {item['metric_code']} = {direction}{value} "
        f"{item['unit']}. Cite {key} and preserve its direction and supported rounding, or remove the claim."
    )
