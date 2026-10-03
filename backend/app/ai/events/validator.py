"""Check citations and numeric grounding for short event insights."""

import json
import re
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from app.ai.analyst.validator import InvalidAnalysis
from app.ai.events.context import MANAGEMENT_WORDS
from app.ai.events.schemas import EventInsight

NUMBER = re.compile(r"(?<![A-Za-z0-9])[-+]?\d[\d,]*(?:\.\d+)?")
TRADING_CALL = re.compile(r"\b(?:buy|sell|hold|target price|price target)\b", re.I)


def validate_event_insight(raw: str, registry: dict[str, dict]) -> EventInsight:
    try:
        insight = EventInsight.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise InvalidAnalysis("INVALID_JSON_OR_SCHEMA") from exc
    claims = [insight.summary, insight.why_it_matters, insight.what_next]
    if insight.management_says:
        claims.append(insight.management_says)
    texts = []
    for claim in claims:
        _check_claim(claim.text, claim.evidence_ids, registry)
        texts.append(claim.text)
    _check_claim(
        insight.recommendation.reason, insight.recommendation.evidence_ids, registry
    )
    texts.append(insight.recommendation.reason)
    if TRADING_CALL.search(" ".join(texts)):
        raise InvalidAnalysis("TRADING_CALL_UNSUPPORTED")
    if insight.management_says and not any(
        MANAGEMENT_WORDS.search(registry[item]["text"])
        for item in insight.management_says.evidence_ids
    ):
        raise InvalidAnalysis("MANAGEMENT_NOT_IN_SOURCE")
    return insight


def _check_claim(text: str, ids: list[str], registry: dict[str, dict]) -> None:
    if len(ids) != len(set(ids)) or any(item not in registry for item in ids):
        raise InvalidAnalysis("UNKNOWN_EVIDENCE_ID")
    allowed = set()
    for item in ids:
        for match in NUMBER.finditer(registry[item]["text"]):
            try:
                allowed.add(abs(Decimal(match.group().replace(",", ""))))
            except InvalidOperation:
                continue
    for match in NUMBER.finditer(text):
        try:
            number = abs(Decimal(match.group().replace(",", "")))
        except InvalidOperation as exc:
            raise InvalidAnalysis("UNSUPPORTED_NUMBER", match.group()) from exc
        if number not in allowed:
            raise InvalidAnalysis("UNSUPPORTED_NUMBER", match.group())
