"""SEC press-release RSS summaries for unreviewed regulatory-event discovery."""

from datetime import UTC
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .market import MARKETS
from .news import save_news

SOURCE = "sec_press_rss"
FEED_URL = "https://www.sec.gov/news/pressreleases.rss"


def official_release_url(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.hostname == "www.sec.gov" and
            parts.path.startswith("/newsroom/press-releases/") and
            len(parts.path) > len("/newsroom/press-releases/") and
            not parts.username and not parts.password and
            not parts.query and not parts.fragment)


def _clean(value: str | None, limit: int) -> str:
    text = " ".join(unescape(value or "").split())
    if not text or len(text) > limit or "<" in text or ">" in text:
        raise ValueError("SEC RSS text is empty, oversized or contains markup")
    return text


def parse_sec_press_rss(raw: str) -> list[dict]:
    if len(raw.encode("utf-8")) > 1_000_000:
        raise ValueError("SEC RSS is too large")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid SEC RSS XML") from exc
    if root.tag != "rss" or root.findtext("channel/title") != "Press Releases":
        raise ValueError("Unexpected SEC press-release feed")
    items = []
    seen = set()
    for entry in root.findall("channel/item"):
        title = _clean(entry.findtext("title"), 300)
        summary = _clean(entry.findtext("description"), 1000)
        url = (entry.findtext("link") or "").strip()
        if not official_release_url(url):
            raise ValueError("Unexpected SEC release URL")
        published = parsedate_to_datetime(entry.findtext("pubDate") or "")
        if published.tzinfo is None:
            raise ValueError("SEC publication time lacks timezone")
        if url in seen:
            continue
        seen.add(url)
        enough = len(summary) >= 100 and summary.casefold() != title.casefold()
        items.append({"source": SOURCE, "source_url": url, "title": title,
                      "body": summary, "published_at": published.astimezone(UTC).isoformat(),
                      "market_ids": [market["id"] for market in MARKETS],
                      "metadata": {"origin": "official_sec_rss", "rights_basis": "sec_public_information",
                                   "content_quality": "summary" if enough else "headline_only",
                                   "model_use_allowed": enough, "summary_truncated": summary.endswith(("…", "...")),
                                   "interpretation": "rss_excerpt_not_full_release",
                                   "classification": "unreviewed"}})
    if not items:
        raise ValueError("SEC press-release feed has no items")
    return items


def save_sec_news(items: list[dict], observed_at) -> int:
    if any(item.get("source") != SOURCE for item in items):
        raise ValueError("SEC source mismatch")
    return save_news(items, observed_at)
