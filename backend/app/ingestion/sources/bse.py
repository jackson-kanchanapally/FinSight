"""BSE integration point; no verified public bulk feed is configured."""

from datetime import datetime

from app.ingestion.sources.base import (
    EventSource,
    MonitoredCompany,
    SourceEvent,
    SourceUnavailableError,
)


class BSESource(EventSource):
    name = "BSE"

    async def fetch_events(
        self, companies: list[MonitoredCompany], since: datetime | None
    ) -> list[SourceEvent]:
        raise SourceUnavailableError(
            "A verified, permitted BSE bulk feed is not configured"
        )

    async def download_document(self, event: SourceEvent):
        raise SourceUnavailableError("BSE document retrieval is not configured")
