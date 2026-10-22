import asyncio
import hashlib
import re
from email.utils import parsedate_to_datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
from app.ingestion.retry_policy import retry_after_seconds

if TYPE_CHECKING:
    from app.ingestion.sources.base import DownloadedDocument


class DocumentDownloadError(Exception):
    def __init__(self, message, retry_after_seconds=None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_pdf(document: "DownloadedDocument", max_bytes: int = 10_000_000) -> None:
    if len(document.content) > max_bytes:
        raise DocumentDownloadError("Document exceeds the size limit")
    if document.mime_type.split(";", 1)[0].strip().lower() != "application/pdf":
        raise DocumentDownloadError("Document MIME type is not PDF")
    if not document.content.startswith(b"%PDF-"):
        raise DocumentDownloadError("Document content is not a PDF")


class SafePdfDownloader:
    """HTTP retrieval for trusted adapters, with explicit hosts and bounded content."""

    def __init__(
        self,
        allowed_hosts: set[str],
        *,
        max_bytes: int = 10_000_000,
        timeout_seconds: float = 10,
        retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ):
        self.allowed_hosts = {host.lower() for host in allowed_hosts}
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.client = client
        self.access_policy = None

    def validate_url(self, url: str) -> None:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname is None
            or parts.hostname.lower() not in self.allowed_hosts
            or parts.username
            or parts.password
            or parts.port not in (None, 443)
        ):
            raise DocumentDownloadError(
                "Document URL is not from an approved HTTPS host"
            )

    async def download(self, url: str) -> "DownloadedDocument":
        from app.ingestion.sources.base import DownloadedDocument

        self.validate_url(url)
        own_client = self.client is None
        client = self.client or httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "almonds/0.3 (personal portfolio monitor)"},
        )
        try:
            for attempt in range(self.retries + 1):
                try:
                    if self.access_policy:
                        await self.access_policy.check(url)
                    async with client.stream(
                        "GET", url, follow_redirects=False
                    ) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            raise DocumentDownloadError(
                                "Document redirects are not followed"
                            )
                        if response.status_code >= 500:
                            raise httpx.HTTPStatusError(
                                "Source server error",
                                request=response.request,
                                response=response,
                            )
                        if response.status_code != 200:
                            raise DocumentDownloadError(
                                f"Document request returned HTTP {response.status_code}", retry_after_seconds(response.headers)
                            )
                        try:
                            declared_size = int(
                                response.headers.get("content-length", "0")
                            )
                        except ValueError as exc:
                            raise DocumentDownloadError(
                                "Invalid document size header"
                            ) from exc
                        if declared_size > self.max_bytes:
                            raise DocumentDownloadError(
                                "Document exceeds the size limit"
                            )
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > self.max_bytes:
                                raise DocumentDownloadError(
                                    "Document exceeds the size limit"
                                )
                        raw_name = (
                            PurePosixPath(urlsplit(url).path).name or "document.pdf"
                        )
                        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)[:100]
                        last_modified = None
                        if header := response.headers.get("last-modified"):
                            try:
                                parsed = parsedate_to_datetime(header)
                                if parsed.tzinfo is not None:
                                    last_modified = parsed
                            except (TypeError, ValueError):
                                pass
                        document = DownloadedDocument(
                            bytes(content),
                            response.headers.get("content-type", ""),
                            safe_name,
                            last_modified,
                        )
                        validate_pdf(document, self.max_bytes)
                        return document
                except (
                    httpx.TimeoutException,
                    httpx.TransportError,
                    httpx.HTTPStatusError,
                ) as exc:
                    if attempt >= self.retries:
                        raise DocumentDownloadError(
                            "Document source unavailable after retries"
                        ) from exc
                    await asyncio.sleep(0.2 * (attempt + 1))
        finally:
            if own_client:
                await client.aclose()
        raise DocumentDownloadError("Document download failed")
