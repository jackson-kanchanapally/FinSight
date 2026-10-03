from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree


class MalformedFeedError(ValueError):
    pass


@dataclass(frozen=True)
class RssItem:
    title: str
    description: str | None
    link: str
    external_id: str | None
    published_at: datetime
    enclosure_url: str | None


def parse_rss(payload: bytes) -> list[RssItem]:
    """Parse standard RSS 2.0 items; source adapters map items to symbols."""
    if (
        len(payload) > 5_000_000
        or b"<!DOCTYPE" in payload.upper()
        or b"<!ENTITY" in payload.upper()
    ):
        raise MalformedFeedError(
            "Feed is too large or contains forbidden XML declarations"
        )
    try:
        root = ElementTree.fromstring(payload)
        if root.tag != "rss":
            raise MalformedFeedError("Expected RSS 2.0")
        items = []
        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            date_text = (item.findtext("pubDate") or "").strip()
            if not title or not link or not date_text:
                raise MalformedFeedError("RSS item is missing title, link, or date")
            published = parsedate_to_datetime(date_text)
            if published.tzinfo is None:
                raise MalformedFeedError("RSS date lacks a timezone")
            enclosure = item.find("enclosure")
            items.append(
                RssItem(
                    title=title,
                    description=item.findtext("description"),
                    link=link,
                    external_id=item.findtext("guid"),
                    published_at=published,
                    enclosure_url=(
                        enclosure.get("url") if enclosure is not None else None
                    ),
                )
            )
        return items
    except (ElementTree.ParseError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, MalformedFeedError):
            raise
        raise MalformedFeedError("Malformed RSS feed") from exc
