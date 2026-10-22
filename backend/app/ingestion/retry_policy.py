"""Parse server backoff without retrying earlier than Retry-After."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import ceil


def retry_after_seconds(headers, now=None):
    value = headers.get('retry-after', '').strip()
    if value.isdigit():
        return int(value)
    try:
        until = parsedate_to_datetime(value)
        if until.tzinfo is None:
            return None
        return max(0, ceil((until-(now or datetime.now(timezone.utc))).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return None
