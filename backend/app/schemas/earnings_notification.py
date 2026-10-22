"""Versioned presentation contract shared by Telegram and authenticated reports."""
from datetime import date
from decimal import Decimal
from typing import Literal
import ipaddress
import re
from urllib.parse import parse_qs, urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator


def public_https_url(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or not host or parsed.username or parsed.password
                or parsed.port not in (None, 443) or host == "localhost"
                or host.endswith((".localhost", ".local", ".internal"))):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return "." in host
    except ValueError:
        return False


class NotificationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceText(NotificationModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    basis: Literal["REPORTED_FACT", "CALCULATED_METRIC", "AI_INTERPRETATION", "DETERMINISTIC_INTERPRETATION", "MISSING_EVIDENCE", "HYPOTHESIS", "MANAGEMENT_CLAIM"]


class EarningsMetric(NotificationModel):
    code: str
    label: str
    value: Decimal
    unit: str
    evidence_ids: list[str]
    basis: Literal["REPORTED_FACT", "CALCULATED_METRIC"]
    formula: str | None = None
    qoq: "EarningsMetric | None" = None
    yoy: "EarningsMetric | None" = None


class ReportLink(NotificationModel):
    url: str | None = None
    unavailable_reason: str | None = None

    @field_validator("url")
    @classmethod
    def authenticated_remote_url(cls, value):
        if value is None:
            return None
        parsed = urlsplit(value)
        query = parse_qs(parsed.query)
        if (not public_https_url(value) or parsed.fragment or set(query) != {"expires", "signature"}
                or len(query["expires"]) != 1 or not query["expires"][0].isdigit()
                or len(query["signature"]) != 1 or not re.fullmatch(r"[a-f0-9]{64}", query["signature"][0])
                or "/api/reports/earnings/" not in parsed.path):
            raise ValueError("A scoped, authenticated HTTPS report URL is required")
        return value


class EarningsNotification(NotificationModel):
    schema_version: str = "EARNINGS_ALERT_V2"
    company_id: int
    company_symbol: str
    company_name: str
    period_id: int
    reporting_period: str
    period_end: date
    reporting_basis: str
    analysis_id: int | None
    ai_status: str
    snapshot: list[EarningsMetric]
    additional_metrics: list[EarningsMetric] = Field(default_factory=list)
    verdict: EvidenceText
    positives: list[EvidenceText] = Field(default_factory=list)
    negatives: list[EvidenceText] = Field(default_factory=list)
    risks: list[EvidenceText] = Field(default_factory=list)
    management_highlights: list[EvidenceText] = Field(default_factory=list)
    drivers: list[EvidenceText] = Field(default_factory=list)
    contradictions: list[EvidenceText] = Field(default_factory=list)
    watch_next: list[EvidenceText] = Field(max_length=3)
    valuation: EvidenceText
    confidence: dict
    previous_thesis: dict
    verification: dict
    freshness: dict
    unassessed: dict[str, str]
    full_report: ReportLink
    evidence_registry: dict
