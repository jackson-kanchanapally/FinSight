import json
from pathlib import Path

import pymupdf

from app.ingestion.sources.base import DownloadedDocument, EventSource, MonitoredCompany, SourceEvent
from app.models.event import DataOrigin

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "demo_events.json"


class FixtureSource(EventSource):
    """Repeatable demo source. Every URL and event is explicitly marked as a fixture."""

    name = "FIXTURE"
    origin = DataOrigin.FIXTURE

    async def fetch_events(self, companies: list[MonitoredCompany], since) -> list[SourceEvent]:
        symbols = {company.external_symbol.upper() for company in companies}
        raw_items = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        # Replay the full fixture feed on each check to exercise deduplication.
        return [SourceEvent.model_validate(item) for item in raw_items
                if item["external_symbol"].upper() in symbols]

    async def download_document(self, event: SourceEvent) -> DownloadedDocument:
        fixture = next((item for item in json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
                        if item.get("document_url") == event.document_url
                        and item.get("external_id") == event.external_id), None)
        if fixture is None or event.document_url is None:
            raise ValueError("Unknown fixture document URL")
        with pymupdf.open() as pdf:
            first = pdf.new_page()
            first.insert_text((72, 72), f"{event.external_symbol} - {event.title}")
            first.insert_text((72, 100), "Generated from a almonds fixture disclosure.")
            second = pdf.new_page()
            second.insert_text((72, 72), "Fixture document - page 2")
            second.insert_text((72, 100), "This is not a real filing.")
            content = pdf.tobytes()
        return DownloadedDocument(content=content, mime_type="application/pdf",
                                  file_name=f"{event.external_symbol.lower()}_fixture_document.pdf")
