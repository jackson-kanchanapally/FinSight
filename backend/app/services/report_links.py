"""Expiring, resource-scoped report links; never fall back to localhost."""
import hashlib
import hmac
import logging
import re
import time
from urllib.parse import urlsplit
from app.config import settings
from app.schemas.earnings_notification import ReportLink, public_https_url


def remote_base():
    raw = settings.report_public_base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
        if not public_https_url(raw) or parsed.query or parsed.fragment:
            return None
    except ValueError:
        return None
    return raw if len(settings.report_access_secret.get_secret_value()) >= 32 else None


def signature(resource, expires):
    return hmac.new(settings.report_access_secret.get_secret_value().encode(),
                    f"almonds-report-v1:{resource}:{expires}".encode(), hashlib.sha256).hexdigest()


def signed_link(period_id, *, document_id=None, delivery_id=None, expires=None):
    base = remote_base()
    if base is None:
        return ReportLink(unavailable_reason="Unassessed: authenticated remote report access is not configured")
    resource = str(period_id) + (f"/documents/{document_id}" if document_id is not None else
        f"/deliveries/{delivery_id}" if delivery_id is not None else "")
    expires = expires or int(time.time()) + settings.report_link_ttl_seconds
    return ReportLink(url=f"{base}/api/reports/earnings/{resource}?expires={expires}&signature={signature(resource, expires)}")


def authorized(resource, expires, supplied):
    current = int(time.time())
    return (remote_base() is not None and current < expires <= current+86400
            and bool(re.fullmatch(r"[a-f0-9]{64}", supplied))
            and hmac.compare_digest(signature(resource, expires), supplied))


class _RedactReportAccess(logging.Filter):
    def filter(self, record):
        def redact(value):
            return re.sub(r"([?&]signature=)[a-zA-Z0-9]+", r"\1[REDACTED]", value) if isinstance(value, str) else value
        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(value) for value in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactReportAccess())
