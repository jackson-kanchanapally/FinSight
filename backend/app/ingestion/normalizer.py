import re
from datetime import date, datetime

from pydantic import BaseModel

from app.ingestion.sources.base import MonitoredCompany, SourceEvent
from app.models.event import DataOrigin, EventType, PublicationPrecision, ReportingBasis

RULES: tuple[tuple[EventType, str], ...] = (
    (
        EventType.FINANCIAL_RESULTS,
        r"financial results|quarterly results|annual results|\bq[1-4]\s*fy\s*\d{2,4}\s+results\b",
    ),
    (EventType.BOARD_MEETING, r"board meeting"),
    (
        EventType.EARNINGS_CALL,
        r"earnings call|earnings conference call|analyst call|investor call|analysts?/institutional investor meet|investor meet",
    ),
    (EventType.INVESTOR_PRESENTATION, r"investor presentation|earnings presentation"),
    (EventType.ANNUAL_REPORT, r"annual report"),
    (EventType.ORDER_CANCELLATION, r"order cancellation|contract cancellation"),
    (EventType.ORDER_WIN, r"order win|contract award|new order"),
    (EventType.PARTNERSHIP, r"partnership|joint venture|strategic alliance"),
    (EventType.ACQUISITION, r"acquisition|acquire"),
    (EventType.DIVESTMENT, r"divestment|stake sale"),
    (EventType.CAPEX, r"capex|capital expenditure"),
    (EventType.NEW_PRODUCT, r"new product|product launch"),
    (
        EventType.MANAGEMENT_CHANGE,
        r"management change|appointment of director|resignation of director|chief executive",
    ),
    (
        EventType.AUDITOR_CHANGE,
        r"auditor change|statutory auditor|appointment of auditor",
    ),
    (EventType.DIVIDEND, r"dividend"),
    (EventType.BONUS, r"bonus issue|bonus shares"),
    (EventType.STOCK_SPLIT, r"stock split|subdivision of shares"),
    (EventType.BUYBACK, r"buyback|buy-back"),
    (
        EventType.FUND_RAISE,
        r"fund raise|fundraising|rights issue|qualified institutions placement",
    ),
    (EventType.CREDIT_RATING, r"credit rating|rating revision"),
    (EventType.SHAREHOLDING, r"shareholding pattern"),
    (EventType.PROMOTER_TRANSACTION, r"promoter transaction|promoter acquisition"),
    (EventType.PLEDGE, r"pledge|encumbrance"),
    (EventType.REGULATORY, r"regulatory|sebi order|regulation"),
    (EventType.LITIGATION, r"litigation|legal proceedings|court order"),
    (EventType.COMPLIANCE, r"trading window|compliance|disclosure under regulation"),
)


def classify_event(title: str, description: str | None = None) -> EventType:
    # Prefer title: a broad description can mention unrelated event categories.
    for content in (title, description or ""):
        for event_type, pattern in RULES:
            if re.search(pattern, content, flags=re.IGNORECASE):
                return event_type
    return EventType.OTHER


class NormalizedEvent(BaseModel):
    company_id: int
    symbol: str
    source: str
    event_type: EventType
    title: str
    description: str | None
    published_at: datetime
    external_id: str | None
    source_url: str
    document_url: str | None
    raw_payload: dict | None
    data_origin: DataOrigin
    publication_precision: PublicationPrecision
    period_end_date: date | None
    fiscal_year: str | None
    quarter: int | None
    reporting_basis: ReportingBasis | None


def normalize_event(
    raw: SourceEvent,
    source: str,
    companies: list[MonitoredCompany],
    origin: DataOrigin = DataOrigin.LIVE,
) -> NormalizedEvent | None:
    symbol = raw.external_symbol.strip().upper()
    match = next(
        (company for company in companies if company.external_symbol.upper() == symbol),
        None,
    )
    if match is None:
        return None
    return NormalizedEvent(
        company_id=match.company_id,
        symbol=match.symbol,
        source=source,
        event_type=classify_event(raw.title, raw.description),
        title=raw.title.strip(),
        description=raw.description,
        published_at=raw.published_at,
        external_id=raw.external_id,
        source_url=raw.source_url,
        document_url=raw.document_url,
        raw_payload=raw.model_dump(mode="json"),
        data_origin=origin,
        publication_precision=raw.publication_precision,
        period_end_date=raw.period_end_date,
        fiscal_year=raw.fiscal_year,
        quarter=raw.quarter,
        reporting_basis=raw.reporting_basis,
    )
