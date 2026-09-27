from datetime import datetime

from app.ingestion.sources.base import EventSource, MonitoredCompany, SourceEvent, SourceUnavailableError


class CompanyIRSource(EventSource):
    name = "COMPANY_IR"

    async def fetch_events(self, companies: list[MonitoredCompany], since: datetime | None) -> list[SourceEvent]:
        raise SourceUnavailableError("A company-specific IR adapter is not configured")

    async def download_document(self, event: SourceEvent):
        raise SourceUnavailableError("A company-specific IR adapter is not configured")
