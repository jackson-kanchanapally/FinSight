"""Conservative extraction of attributed statements from official, associated documents."""

import re
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
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
from app.models.financial import FinancialPeriod, FinancialScope, FinancialStatus

COMMENTARY_EVENTS = {
    EventType.EARNINGS_CALL,
    EventType.INVESTOR_PRESENTATION,
    EventType.EARNINGS_RELEASE,
    EventType.CONCALL_TRANSCRIPT,
    EventType.CONCALL_AUDIO,
    EventType.FINANCIAL_RESULTS,
    EventType.OTHER,
}
OFFICIAL_SOURCES = {"NSE", "BSE", "ESDS_IR", "COMPANY_IR"}
INDIA = ZoneInfo("Asia/Kolkata")
COMMENTARY_TITLE = re.compile(
    r"concall|earnings call|transcript|investor presentation|corporate presentation|"
    r"earnings release|results release|press release|business update|con\.?\s*call updates?",
    re.I,
)
INVITATION_TITLE = re.compile(r"\b(?:invite|invitation|intimation|schedule|notice)\b", re.I)


def is_commentary_candidate(event_type: EventType, title: str) -> bool:
    if event_type == EventType.EARNINGS_CALL and INVITATION_TITLE.search(title):
        return False
    return event_type in COMMENTARY_EVENTS and (
        event_type != EventType.OTHER or bool(COMMENTARY_TITLE.search(title))
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
    r"\b(?:expect|expects|guidance|target|targets|targeting|forecast|project|anticipate|guided|outlook)\b",
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
FY = re.compile(r"(?<![A-Za-z])FY\s*'?((?:20)?\d{2})\b", re.I)
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
    ("WORKING_CAPITAL", ("working capital",)),
    ("CUSTOMERS", ("customer", "client")),
    ("PRICING", ("pricing",)),
    ("GEOGRAPHY", ("geography", "region", "market")),
    ("EXPANSION", ("expansion", "expand")),
    ("RISKS", ("risk",)),
    ("GROWTH", ("growth",)),
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
        speaker = speaker.strip()
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
            ("TARGET" if re.search(r"\btarget(?:s|ing)?\b", raw, re.I) else "GUIDANCE")
            if GUIDANCE.search(raw) and (
                RANGE.search(raw) or SINGLE.search(raw)
                or AMOUNT_RANGE.search(raw) or AMOUNT_SINGLE.search(raw)
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
    if statement["statement_type"] not in {"GUIDANCE", "TARGET"}:
        return None
    raw = statement["raw_statement"]
    targets = list(FY.finditer(raw))
    if len(targets) != 1 or re.search(r"FY\s*'?\d{2,4}\s*[-–—/]\s*\d{2,4}", raw, re.I):
        return None
    target = targets[0]
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
    if metric == "DEBT" and re.search(
        r"\bdebt\s+reduction\b|\b(?:reduc\w*|repay\w*)\s+debt\s+(?:by|of)\b", raw, re.I
    ):
        metric = "DEBT_REDUCTION"
    if not metric:
        return None
    amount_metric = metric in {"CAPEX", "ORDER_BOOK", "DEBT", "DEBT_REDUCTION", "CASH_FLOW"}
    match = AMOUNT_RANGE.search(raw) if amount_metric else RANGE.search(raw)
    unit = "INR_CRORE" if amount_metric else "PERCENT"
    if match:
        lower, upper = Decimal(match.group(1).replace(",", "")), Decimal(match.group(2).replace(",", ""))
    else:
        match = AMOUNT_SINGLE.search(raw) if amount_metric else SINGLE.search(raw)
        if match:
            lower = upper = Decimal(match.group(1).replace(",", ""))
        else:
            return None
    if lower > upper or lower < 0 or (unit == "PERCENT" and upper > 100):
        return None
    if unit == "INR_CRORE" and metric not in {
        "CAPEX",
        "ORDER_BOOK",
        "DEBT",
        "DEBT_REDUCTION",
        "CASH_FLOW",
    }:
        return None
    year = target.group(1)[-2:]
    quarter = re.search(r"\bQ([1-4])\s*FY|FY\s*'?\d{2,4}\s*Q([1-4])\b", raw, re.I)
    return {
        "metric": metric,
        "target_period": f"Q{quarter.group(1) or quarter.group(2)}FY{year}" if quarter else f"FY{year}",
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


def guidance_target_end(target_period: str) -> date | None:
    match = re.fullmatch(r"(?:Q([1-4]))?FY(\d{2})", target_period)
    if not match:
        return None
    year = 2000 + int(match.group(2))
    quarter = int(match.group(1)) if match.group(1) else 4
    return {1: date(year - 1, 6, 30), 2: date(year - 1, 9, 30),
            3: date(year - 1, 12, 31), 4: date(year, 3, 31)}[quarter]


def _management_roster(first_page: str) -> dict[str, str]:
    """Only names explicitly listed under MANAGEMENT may own transcript turns."""
    section = re.search(r"\bMANAGEMENT\s*:\s*(.*?)\bMODERATOR\s*:", first_page, re.I | re.S)
    if not section:
        return {}
    roster = {}
    entries = list(re.finditer(r"\bMR\.?\s+([A-Z][A-Z ]{3,70}?)\s*[–—-]\s*", section.group(1)))
    for index, entry in enumerate(entries):
        name = " ".join(entry.group(1).split()).title()
        role_text = " ".join(section.group(1)[
            entry.end():entries[index + 1].start() if index + 1 < len(entries) else None
        ].upper().split())
        role = ("CFO" if "CHIEF FINANCIAL OFFICER" in role_text else
                "Managing Director" if "MANAGING DIRECTOR" in role_text else
                "Chairman" if "CHAIRMAN" in role_text else "Management")
        roster[name.casefold()] = role
    return roster


TURN_LABEL = re.compile(r"^(?:Mr\.\s*)?([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){0,3})\s*:\s*(.*)$")


def _transcript_statements(pages: list[DocumentPage]):
    """Extract paragraph-sized, page-cited turns from an explicit management roster."""
    if not pages:
        return
    roster = _management_roster(pages[0].text)
    if not roster:
        return
    speaker = role = None
    qa_mode = False
    for page in pages:
        content = re.sub(r"\bPage\s+\d+\s+of\s+\d+\b", "", page.text, flags=re.I)
        header_date = re.search(r"^\s*[A-Za-z]+\s+\d{1,2},\s+20\d{2}\s*$", content, re.M)
        if header_date and header_date.start() < 300:
            content = content[header_date.end():]
        # Canonical PDF text has a blank line after each visual line. Preserve
        # wider block gaps as paragraph breaks before reading speaker turns.
        if (len(re.findall(r"\n[ \t]*\n", content)) > len(content.splitlines()) * 0.3
                and len(re.findall(r"(?m)^ {6,}\S", content)) > 3):
            content = re.sub(r"\n(?:[ \t]*\n){2,}", "\x00", content)
            content = re.sub(r"\n[ \t]*\n", "\n", content)
            content = content.replace("\x00", "\n\n")
        paragraph = []

        def finish():
            if speaker and paragraph:
                parsed = parse_statement(f"{speaker} ({role}): {' '.join(paragraph)}")
                if parsed:
                    return parsed
            return None

        for raw_line in content.splitlines() + [""]:
            line = raw_line.strip()
            tagged = TURN_LABEL.match(line)
            if tagged:
                parsed = finish()
                if parsed:
                    yield page, parsed, qa_mode
                paragraph = []
                name = tagged.group(1)
                role = roster.get(name.casefold())
                speaker = name if role else None
                if tagged.group(2):
                    paragraph.append(tagged.group(2))
                continue
            if not line:
                parsed = finish()
                if parsed:
                    yield page, parsed, qa_mode
                if speaker is None and re.search(r"\bquestion\b", " ".join(paragraph), re.I):
                    qa_mode = True
                paragraph = []
            else:
                paragraph.append(line)
        # The same speaker can continue on the next page; page evidence remains local.


def _document_statement_candidates(event: CompanyEvent, pages: list[DocumentPage]):
    if event.event_type == EventType.CONCALL_TRANSCRIPT and _management_roster(pages[0].text if pages else ""):
        yield from _transcript_statements(pages)
        return
    for page in pages:
        qa_mode = False
        answer_to_analyst = False
        for line in page.text.splitlines():
            if re.search(r"\b(?:question and answer|questions and answers|Q\s*&\s*A)\b", line, re.I):
                qa_mode = True
            if re.match(r"^\s*(?:analyst|operator)\s*[:–-]", line, re.I):
                answer_to_analyst = True
                continue
            parsed = parse_statement(line)
            if parsed:
                yield page, parsed, qa_mode or answer_to_analyst
                answer_to_analyst = False


def _associated(
    event: CompanyEvent, period: FinancialPeriod,
    periods: list[FinancialPeriod], result_events: dict[int, CompanyEvent | None],
) -> bool:
    if event.company_id != period.company_id or event.data_origin == DataOrigin.FIXTURE:
        return False
    if event.data_origin == DataOrigin.LIVE and event.source.upper() not in OFFICIAL_SOURCES:
        return False
    if not is_commentary_candidate(event.event_type, event.title) or (
        event.event_type == EventType.FINANCIAL_RESULTS
        and not COMMENTARY_TITLE.search(event.title)
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
    if event.reporting_basis and event.reporting_basis.value in {"STANDALONE", "CONSOLIDATED"}:
        if event.reporting_basis.value != period.scope.value:
            return False
    title_scopes = {value.upper() for value in re.findall(
        r"\b(standalone|consolidated)\b", event.title, re.I
    )}
    if len(title_scopes) > 1 or (title_scopes and period.scope.value not in title_scopes):
        return False
    title_quarter = re.search(r"(?<!\w)Q([1-4])(?=\s*FY|\b)", event.title, re.I)
    title_fy = FY.search(event.title)
    if title_quarter and int(title_quarter.group(1)) != period.quarter:
        return False
    if title_fy and title_fy.group(1)[-2:] != period.fiscal_year[-2:]:
        return False
    if not title_scopes and (not event.reporting_basis or event.reporting_basis.value == "BOTH") and period.scope == FinancialScope.STANDALONE:
        if any(p.period_end == period.period_end and p.scope == FinancialScope.CONSOLIDATED for p in periods):
            return False
    if (
        event.quarter is None
        and event.fiscal_year is None
        and event.period_end_date is None
        and not (title_quarter and title_fy)
    ):
        # Untagged announcements must follow the nearest validated result
        # publication within 45 days. A call invitation before results is not
        # treated as commentary about a later result.
        dated = [
            (result_events[p.id].published_at.astimezone(INDIA).date(), p.period_end)
            for p in periods if result_events.get(p.id) is not None
            and result_events[p.id].published_at.astimezone(INDIA).date() <= published_date
            and 0 <= (published_date - result_events[p.id].published_at.astimezone(INDIA).date()).days <= 45
        ]
        own = result_events.get(period.id)
        if not own or not dated or (
            own.published_at.astimezone(INDIA).date(), period.period_end
        ) != max(dated):
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
    periods = list(db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.company_id == period.company_id,
        FinancialPeriod.status == FinancialStatus.VALIDATED,
    )))
    result_events = {p.id: db.get(CompanyEvent, p.source_event_id) for p in periods}

    def source_priority(event: CompanyEvent) -> tuple:
        sources = ({event.source.upper()} if event.data_origin == DataOrigin.LIVE else set()) | {
            ref.source.upper() for ref in event.references if ref.data_origin == DataOrigin.LIVE
        }
        source_rank = (0 if sources & {"NSE", "BSE"}
                       else 1 if sources & {"ESDS_IR", "COMPANY_IR"} else 2)
        type_rank = {
            EventType.CONCALL_TRANSCRIPT: 0,
            EventType.EARNINGS_CALL: 1,
            EventType.EARNINGS_RELEASE: 2,
            EventType.INVESTOR_PRESENTATION: 3,
            EventType.CONCALL_AUDIO: 4,
        }.get(event.event_type, 5)
        return source_rank, type_rank, event.published_at

    events.sort(key=source_priority)
    for event in events:
        if not _associated(event, period, periods, result_events):
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
            rank = source_priority(event)
            if association is None:
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
            elif association.source_priority != rank[0] * 10 + rank[1]:
                association.source_priority = rank[0] * 10 + rank[1]
            pages = list(
                db.scalars(
                    select(DocumentPage).where(DocumentPage.document_id == document.id).order_by(DocumentPage.page_number)
                )
            )
            if event.event_type == EventType.CONCALL_TRANSCRIPT and pages and _management_roster(pages[0].text):
                # Earlier line-only extraction could turn the roster header into
                # unattributed statements. A rostered transcript requires a name.
                stale_ids = list(db.scalars(select(ManagementStatement.id).where(
                    ManagementStatement.financial_period_id == period.id,
                    ManagementStatement.document_id == document.id,
                    ManagementStatement.speaker_name.is_(None),
                )))
                if stale_ids:
                    db.execute(delete(ManagementGuidance).where(ManagementGuidance.statement_id.in_(stale_ids)))
                    db.execute(delete(ManagementStatement).where(ManagementStatement.id.in_(stale_ids)))
            for page, parsed, is_answer in _document_statement_candidates(event, pages):
                existing = db.scalar(
                    select(ManagementStatement).where(
                        ManagementStatement.financial_period_id == period.id,
                        ManagementStatement.document_id == document.id,
                        ManagementStatement.page_number == page.page_number,
                        ManagementStatement.raw_statement == parsed["raw_statement"],
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
                    section_type="MANAGEMENT_ANSWER" if is_answer else "PREPARED_REMARKS",
                    **parsed,
                )
                db.add(statement)
                db.flush()
                guidance = parse_guidance(parsed)
                if guidance and statement.published_at.astimezone(INDIA).date() <= guidance_target_end(
                    guidance["target_period"]
                ):
                    db.add(
                        ManagementGuidance(
                            statement_id=statement.id,
                            company_id=period.company_id,
                            financial_period_id=period.id,
                            published_at=statement.published_at,
                            **guidance,
                        )
                    )
    from app.services.checkpoints import sync_checkpoints
    sync_checkpoints(db, period)
    db.commit()
    return list(
        db.scalars(
            select(ManagementStatement)
            .where(ManagementStatement.financial_period_id == period.id)
            .order_by(ManagementStatement.published_at, ManagementStatement.id)
        )
    )


def commentary_availability(db: Session, period: FinancialPeriod) -> dict:
    """Surface official recording links without pretending audio was transcribed."""
    periods = list(db.scalars(select(FinancialPeriod).where(
        FinancialPeriod.company_id == period.company_id,
        FinancialPeriod.status == FinancialStatus.VALIDATED,
    )))
    result_events = {p.id: db.get(CompanyEvent, p.source_event_id) for p in periods}
    events = list(db.scalars(select(CompanyEvent).where(CompanyEvent.company_id == period.company_id)))
    related = [e for e in events if _associated(e, period, periods, result_events)]
    transcript = next((e for e in related if e.event_type == EventType.CONCALL_TRANSCRIPT
        and db.scalar(select(Document.id).where(Document.event_id == e.id,
            Document.processing_status == ProcessingStatus.PROCESSED)) is not None), None)
    audio = [e for e in related if e.event_type == EventType.CONCALL_AUDIO]
    text = next((e for e in related if e.event_type != EventType.CONCALL_AUDIO
        and db.scalar(select(Document.id).where(Document.event_id == e.id,
            Document.processing_status == ProcessingStatus.PROCESSED)) is not None), None)
    pending = any(db.scalar(select(Document.id).where(Document.event_id == e.id,
        Document.processing_status == ProcessingStatus.PENDING)) is not None for e in audio)
    status = ("TRANSCRIPT_AVAILABLE" if transcript else "COMMENTARY_AVAILABLE" if text
        else "TRANSCRIPTION_PENDING" if pending else "AUDIO_AVAILABLE" if audio else "NO_COMMENTARY_FOUND")
    return {"status": status, "audio_sources": [
        {"event_id": e.id, "source": e.source, "url": e.source_url,
         "published_at": e.published_at.isoformat()} for e in audio
    ]}


def refresh_commentary_for_company(engine, company_id: int) -> dict:
    """Associate new filings, then send their pages through Phase 7 indexing."""
    from app.config import settings
    from app.services.memory.clients import MemoryUnavailable, OllamaEmbeddings, QdrantStore
    from app.services.memory.service import index_document

    with Session(engine) as db:
        periods = list(db.scalars(select(FinancialPeriod).where(
            FinancialPeriod.company_id == company_id,
            FinancialPeriod.status == FinancialStatus.VALIDATED,
        )))
        for period in periods:
            ingest_commentary(db, period)
        from app.services.quarterly_review import refresh_quarterly_reviews
        refresh_quarterly_reviews(db, company_id)
        document_ids = set(db.scalars(select(CommentaryDocument.document_id).where(
            CommentaryDocument.company_id == company_id,
        )))
        if not document_ids or not settings.company_memory_enabled:
            return {"associated_documents": len(document_ids), "indexed": 0}
        # Existing indexed documents are rechecked by index_document's revision
        # guard; this also refreshes official references on a deduplicated PDF.
        store = QdrantStore()
        try:
            store.ensure()
            embedder = OllamaEmbeddings()
            rows = [index_document(db, db.get(Document, doc_id), store, embedder)
                    for doc_id in sorted(document_ids)]
            return {"associated_documents": len(document_ids),
                    "indexed": sum(row["status"] in {"INDEXED", "UNCHANGED"} for row in rows)}
        except MemoryUnavailable as exc:
            return {"associated_documents": len(document_ids), "indexed": 0,
                    "index_status": "UNAVAILABLE", "reason": str(exc)}
