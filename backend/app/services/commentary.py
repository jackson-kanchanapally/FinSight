"""Conservative extraction of attributed statements from official, associated documents."""

import re
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.commentary import (
    CommentaryDocument,
    ManagementGuidance,
    ManagementStatement,
)
from app.models.event import (
    CompanyEvent,
    DataOrigin,
    Document,
    DocumentPage,
    EventType,
    ProcessingStatus,
)
from app.models.financial import FinancialPeriod

COMMENTARY_EVENTS = {
    EventType.EARNINGS_CALL,
    EventType.INVESTOR_PRESENTATION,
    EventType.FINANCIAL_RESULTS,
    EventType.OTHER,
}
OFFICIAL_SOURCES = {"NSE", "BSE", "ESDS_IR", "COMPANY_IR"}
INDIA = ZoneInfo("Asia/Kolkata")
COMMENTARY_TITLE = re.compile(
    r"concall|earnings call|transcript|investor presentation|corporate presentation|"
    r"earnings release|business update",
    re.I,
)
SPEAKER_LINE = re.compile(
    r"^(?P<speaker>[A-Z][A-Za-z .]{2,70})\s*\((?P<role>CEO|CFO|MD|Managing Director|Chairman|Director|Management)[^)]*\)\s*[:–-]\s*(?P<text>.+)$",
    re.I,
)
ROLE_LINE = re.compile(
    r"^(?P<role>Management|CEO|CFO|Managing Director|Chairman|Director)\s*[:–-]\s*(?P<text>.+)$",
    re.I,
)
ATTRIBUTED = re.compile(
    r"\b(?:management|company|CEO|CFO|Managing Director|Chairman|Director)\s+(?:said|stated|explained|expects|guided|indicated|reported|noted)\b",
    re.I,
)
GUIDANCE = re.compile(
    r"\b(?:expect|expects|guidance|target|forecast|project|anticipate|guided|outlook)\b",
    re.I,
)
RANGE = re.compile(
    r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*(?:%|percent)?\s*(?:-|–|—|to)\s*(\d{1,3}(?:\.\d+)?)\s*(?:%|percent\b)",
    re.I,
)
SINGLE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*(?:%|percent\b)", re.I)
AMOUNT_RANGE = re.compile(
    r"(?:₹|INR|Rs\.?\s*)?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:-|–|—|to)\s*(\d[\d,]*(?:\.\d+)?)\s*(?:crore|cr\b)",
    re.I,
)
AMOUNT_SINGLE = re.compile(
    r"(?:₹|INR|Rs\.?\s*)?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:crore|cr\b)", re.I
)
FY = re.compile(r"\bFY\s*'?((?:20)?\d{2})\b", re.I)
TOPICS = (
    ("REVENUE", ("revenue", "sales", "turnover")),
    ("DEMAND", ("demand",)),
    ("MARGINS", ("margin",)),
    ("EBITDA", ("ebitda",)),
    ("PAT", ("profit after tax", "pat")),
    ("EPS", ("eps", "earnings per share")),
    ("ORDER_BOOK", ("order book", "backlog")),
    ("CAPACITY", ("capacity", "utilization")),
    ("CAPEX", ("capex", "capital expenditure")),
    ("CASH_FLOW", ("cash flow",)),
    ("DEBT", ("debt", "borrowings")),
    ("CUSTOMERS", ("customer", "client")),
    ("PRICING", ("pricing",)),
    ("RISKS", ("risk",)),
)


def parse_statement(line: str) -> dict | None:
    """Only explicit management attribution qualifies; no inferred causes or facts."""
    line = " ".join(line.split()).strip()[:1200]
    if len(line) < 25:
        return None
    speaker = role = None
    match = SPEAKER_LINE.match(line)
    if match:
        speaker, role, raw = match.group("speaker", "role", "text")
    else:
        match = ROLE_LINE.match(line)
        if match:
            role, raw = match.group("role", "text")
        elif ATTRIBUTED.search(line):
            raw = line
        else:
            return None
    if len(raw) < 20:
        return None
    lower = raw.lower()
    topic = next(
        (name for name, words in TOPICS if any(word in lower for word in words)),
        "STRATEGY",
    )
    kind = (
        "GUIDANCE_WITHDRAWN"
        if re.search(
            r"\b(?:withdraw(?:n|ing)?|suspend(?:ed)?)\b.{0,30}\bguidance\b|\bguidance\b.{0,30}\bwithdrawn\b",
            raw,
            re.I,
        )
        else (
            "GUIDANCE"
            if GUIDANCE.search(raw)
            and (
                RANGE.search(raw)
                or SINGLE.search(raw)
                or AMOUNT_RANGE.search(raw)
                or AMOUNT_SINGLE.search(raw)
            )
            else (
                "OUTLOOK"
                if GUIDANCE.search(raw)
                else (
                    "RISK"
                    if topic == "RISKS"
                    else (
                        "EXPLANATION"
                        if re.search(
                            r"\b(?:because|due to|driven by|attribut|impacted by)\b",
                            raw,
                            re.I,
                        )
                        else "MANAGEMENT_CLAIM"
                    )
                )
            )
        )
    )
    return {
        "speaker_name": speaker,
        "speaker_role": role,
        "statement_type": kind,
        "topic": topic,
        "raw_statement": raw,
        "normalized_summary": raw,
        "extraction_confidence": "HIGH" if match else "MEDIUM",
    }


def parse_guidance(statement: dict) -> dict | None:
    if statement["statement_type"] != "GUIDANCE":
        return None
    raw = statement["raw_statement"]
    target = FY.search(raw)
    if not target:
        return None
    metric = {
        "REVENUE": "REVENUE_GROWTH",
        "EBITDA": "EBITDA_GROWTH",
        "PAT": "PAT_GROWTH",
        "EPS": "EPS_GROWTH",
        "MARGINS": "PAT_MARGIN" if "PAT MARGIN" in raw.upper() else "EBITDA_MARGIN",
        "CAPEX": "CAPEX",
        "CAPACITY": "CAPACITY",
        "ORDER_BOOK": "ORDER_BOOK",
        "DEBT": "DEBT",
        "CASH_FLOW": "CASH_FLOW",
    }.get(statement["topic"])
    if not metric:
        return None
    match = RANGE.search(raw)
    unit = "PERCENT"
    if match:
        lower, upper = Decimal(match.group(1)), Decimal(match.group(2))
    else:
        match = SINGLE.search(raw)
        if match:
            lower = upper = Decimal(match.group(1))
        else:
            match = AMOUNT_RANGE.search(raw)
            unit = "INR_CRORE"
            if match:
                lower, upper = (
                    Decimal(match.group(1).replace(",", "")),
                    Decimal(match.group(2).replace(",", "")),
                )
            else:
                match = AMOUNT_SINGLE.search(raw)
                if not match:
                    return None
                lower = upper = Decimal(match.group(1).replace(",", ""))
    if lower > upper or lower < 0 or (unit == "PERCENT" and upper > 100):
        return None
    if unit == "INR_CRORE" and metric not in {
        "CAPEX",
        "ORDER_BOOK",
        "DEBT",
        "CASH_FLOW",
    }:
        return None
    year = target.group(1)[-2:]
    return {
        "metric": metric,
        "target_period": f"FY{year}",
        "lower_bound": lower,
        "upper_bound": upper,
        "unit": unit,
    }


def guidance_change(previous: ManagementGuidance, current: ManagementGuidance) -> str:
    if (
        previous.metric != current.metric
        or previous.target_period != current.target_period
        or previous.unit != current.unit
    ):
        return "NOT_COMPARABLE"
    old = (previous.lower_bound + previous.upper_bound) / 2
    new = (current.lower_bound + current.upper_bound) / 2
    return (
        "GUIDANCE_RAISED"
        if new > old
        else "GUIDANCE_CUT" if new < old else "GUIDANCE_MAINTAINED"
    )


def guidance_vs_actual(
    guidance: ManagementGuidance, actual: Decimal | None, actual_period: str | None
) -> str:
    if actual is None:
        return "INSUFFICIENT_DATA"
    if actual_period != guidance.target_period:
        return "TOO_EARLY_TO_ASSESS"
    return (
        "ABOVE_GUIDANCE"
        if actual > guidance.upper_bound
        else "BELOW_GUIDANCE" if actual < guidance.lower_bound else "WITHIN_GUIDANCE"
    )


def _associated(event: CompanyEvent, period: FinancialPeriod) -> bool:
    if event.company_id != period.company_id or event.data_origin != DataOrigin.LIVE:
        return False
    if event.source.upper() not in OFFICIAL_SOURCES:
        return False
    if event.event_type not in COMMENTARY_EVENTS or not COMMENTARY_TITLE.search(
        event.title
    ):
        return False
    published_date = event.published_at.astimezone(INDIA).date()
    if (
        published_date < period.period_end
        or published_date > period.period_end + timedelta(days=120)
    ):
        return False
    if event.quarter is not None and event.quarter != period.quarter:
        return False
    if event.fiscal_year and event.fiscal_year[-2:] != period.fiscal_year[-2:]:
        return False
    if event.period_end_date and event.period_end_date != period.period_end:
        return False
    if (
        event.quarter is None
        and event.fiscal_year is None
        and event.period_end_date is None
    ):
        short_fy = "FY" + period.fiscal_year[-2:]
        if not (
            (
                short_fy.lower() in event.title.lower()
                or period.fiscal_year.lower() in event.title.lower()
            )
            and (
                period.quarter is None
                or re.search(rf"\bQ{period.quarter}\b", event.title, re.I)
            )
        ):
            return False
    return True


def ingest_commentary(
    db: Session, period: FinancialPeriod
) -> list[ManagementStatement]:
    events = list(
        db.scalars(
            select(CompanyEvent).where(CompanyEvent.company_id == period.company_id)
        )
    )

    def source_priority(event: CompanyEvent) -> tuple:
        source_rank = 0 if event.source.upper() in {"NSE", "BSE"} else 1
        type_rank = (
            0
            if event.event_type == EventType.EARNINGS_CALL
            else 1 if event.event_type == EventType.INVESTOR_PRESENTATION else 2
        )
        return source_rank, type_rank, event.published_at

    events.sort(key=source_priority)
    for event in events:
        if not _associated(event, period):
            continue
        documents = list(
            db.scalars(
                select(Document).where(
                    Document.event_id == event.id,
                    Document.processing_status == ProcessingStatus.PROCESSED,
                )
            )
        )
        for document in documents:
            association = db.scalar(
                select(CommentaryDocument).where(
                    CommentaryDocument.financial_period_id == period.id,
                    CommentaryDocument.document_id == document.id,
                )
            )
            if association is None:
                rank = source_priority(event)
                db.add(
                    CommentaryDocument(
                        company_id=period.company_id,
                        financial_period_id=period.id,
                        event_id=event.id,
                        document_id=document.id,
                        fiscal_year=period.fiscal_year,
                        quarter=period.quarter,
                        published_at=document.published_at or event.published_at,
                        document_type=event.event_type.value,
                        source=event.source,
                        source_priority=rank[0] * 10 + rank[1],
                    )
                )
            pages = list(
                db.scalars(
                    select(DocumentPage).where(DocumentPage.document_id == document.id)
                )
            )
            for page in pages:
                qa_mode = False
                answer_to_analyst = False
                for line in page.text.splitlines():
                    if re.search(
                        r"\b(?:question and answer|questions and answers|Q\s*&\s*A)\b",
                        line,
                        re.I,
                    ):
                        qa_mode = True
                    if re.match(r"^\s*(?:analyst|operator)\s*[:–-]", line, re.I):
                        answer_to_analyst = True
                        continue
                    parsed = parse_statement(line)
                    if parsed is None:
                        continue
                    existing = db.scalar(
                        select(ManagementStatement).where(
                            ManagementStatement.financial_period_id == period.id,
                            ManagementStatement.document_id == document.id,
                            ManagementStatement.page_number == page.page_number,
                            ManagementStatement.raw_statement
                            == parsed["raw_statement"],
                        )
                    )
                    if existing:
                        continue
                    statement = ManagementStatement(
                        company_id=period.company_id,
                        financial_period_id=period.id,
                        event_id=event.id,
                        document_id=document.id,
                        page_number=page.page_number,
                        published_at=document.published_at or event.published_at,
                        source_type=event.event_type.value,
                        section_type=(
                            "MANAGEMENT_ANSWER"
                            if qa_mode or answer_to_analyst
                            else "PREPARED_REMARKS"
                        ),
                        **parsed,
                    )
                    db.add(statement)
                    db.flush()
                    answer_to_analyst = False
                    guidance = parse_guidance(parsed)
                    if guidance:
                        db.add(
                            ManagementGuidance(
                                statement_id=statement.id,
                                company_id=period.company_id,
                                financial_period_id=period.id,
                                published_at=statement.published_at,
                                **guidance,
                            )
                        )
    db.commit()
    return list(
        db.scalars(
            select(ManagementStatement)
            .where(ManagementStatement.financial_period_id == period.id)
            .order_by(ManagementStatement.published_at, ManagementStatement.id)
        )
    )
