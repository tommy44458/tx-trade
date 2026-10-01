"""Official Federal Reserve headlines with observation-time provenance.

RSS is a discovery source, not a sentiment model or a source of numeric policy data.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .db import connect, new_id
from .market import MARKETS
from .storage_codec import decode_row, dump_json, utc_text

FED_MONETARY_FEED = "https://www.federalreserve.gov/feeds/press_monetary.xml"
SOURCE = "fed_monetary_rss"


def official_release_url(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.scheme == "https" and parts.hostname == "www.federalreserve.gov" and
            parts.path.startswith("/newsevents/pressreleases/monetary") and
            parts.path.endswith(".htm") and not parts.username and not parts.password and
            not parts.query and not parts.fragment)


def _clean(value: str | None, limit: int) -> str:
    text = " ".join(unescape(value or "").split())
    if not text or len(text) > limit:
        raise ValueError("RSS text is empty or oversized")
    return text


def _kind(title: str) -> str:
    lower = title.lower()
    if "fomc statement" in lower:
        return "fomc_statement"
    if "economic projections" in lower:
        return "fomc_projections"
    if "minutes" in lower:
        return "minutes"
    return "monetary_policy_other"


def parse_fed_monetary_rss(raw: str) -> list[dict]:
    if len(raw.encode("utf-8")) > 1_000_000:
        raise ValueError("RSS body too large")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ValueError("Invalid RSS XML") from exc
    if root.tag != "rss" or root.findtext("channel/title") != "FRB: Press Release - Monetary Policy":
        raise ValueError("Unexpected official RSS feed")
    result = []
    seen = set()
    for item in root.findall("channel/item"):
        title = _clean(item.findtext("title"), 300)
        summary = _clean(item.findtext("description") or title, 600)
        url = (item.findtext("link") or "").strip()
        if not official_release_url(url):
            raise ValueError("Unexpected press-release URL")
        published = parsedate_to_datetime(item.findtext("pubDate") or "")
        if published.tzinfo is None:
            raise ValueError("RSS publication time has no timezone")
        published = published.astimezone(UTC)
        if url in seen:
            continue
        seen.add(url)
        result.append({"source": SOURCE, "source_url": url, "title": title,
                       "body": summary, "published_at": published.isoformat(),
                       "market_ids": [market["id"] for market in MARKETS],
                       "metadata": {"kind": _kind(title), "origin": "official_rss",
                                    "classification": "unreviewed", "time_precision": "second",
                                    "interpretation": "headline_only"}})
    if not result:
        raise ValueError("Official RSS has no items")
    return result


def save_news(items: list[dict], observed_at: datetime) -> int:
    if observed_at.tzinfo is None or not items:
        raise ValueError("News observation time or items missing")
    observed_at = observed_at.astimezone(UTC)
    source = items[0]["source"]
    if source not in {SOURCE, "sec_press_rss"}:
        raise ValueError("Unsupported news source")
    changed = 0
    with connect() as db:
        for item in items:
            if item["source"] != source:
                raise ValueError("News source mismatch")
            published = datetime.fromisoformat(item["published_at"])
            if published.tzinfo is None or published > observed_at:
                raise ValueError("Future or timezone-free publication time")
            canonical = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            previous = db.execute(
                "SELECT content_hash,title,published_at,metadata FROM news_documents "
                "WHERE source=? AND source_url=? "
                "ORDER BY ingested_at DESC,id DESC LIMIT 1", (source, item["source_url"]),
            ).fetchone()
            if previous:
                previous = decode_row(previous, json_fields=("metadata",), time_fields=("published_at",))
            if previous and previous["content_hash"] == digest:
                continue
            # An RSS headline is not a correction to a matching, verified release body.
            if (previous and previous["metadata"].get("content_quality") == "body" and
                    item["metadata"].get("interpretation") == "headline_only" and
                    previous["title"] == item["title"] and previous["published_at"] == published):
                continue
            db.execute(
                "INSERT INTO news_documents(id,source,source_url,title,body,published_at,"
                "ingested_at,market_ids,metadata,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (new_id("news"), source, item["source_url"], item["title"], item["body"],
                 utc_text(published), utc_text(observed_at), dump_json(item["market_ids"]),
                 json.dumps(item["metadata"]), digest),
            )
            changed += 1
        db.execute("INSERT INTO news_source_checks(source,status,checked_at,error_code) "
                   "VALUES(?,'ok',?,NULL)", (source, utc_text(observed_at)))
        db.commit()
    return changed


def mark_news_offline(observed_at: datetime, code: str, source: str = SOURCE) -> None:
    if source not in {SOURCE, "sec_press_rss"}:
        raise ValueError("Unsupported news source")
    with connect() as db:
        db.execute("INSERT INTO news_source_checks(source,status,checked_at,error_code) "
                   "VALUES(?,'offline',?,?)", (source, utc_text(observed_at), code[:80]))
        db.commit()


def news_snapshot(cutoff: datetime, market_id: str | None = None) -> dict:
    if cutoff.tzinfo is None:
        raise ValueError("News cutoff must include timezone")
    cutoff = cutoff.astimezone(UTC)
    with connect(readonly=True) as db:
        check = db.execute("SELECT status,checked_at,error_code FROM news_source_checks "
                           "WHERE source=? AND checked_at<=? ORDER BY checked_at DESC,id DESC LIMIT 1",
                           (SOURCE, utc_text(cutoff))).fetchone()
        sec_check = db.execute("SELECT status,checked_at FROM news_source_checks "
                               "WHERE source='sec_press_rss' AND checked_at<=? "
                               "ORDER BY checked_at DESC,id DESC LIMIT 1", (utc_text(cutoff),)).fetchone()
        rows = db.execute(
            "WITH ranked AS (SELECT n.*,ROW_NUMBER() OVER (PARTITION BY source_url "
            "ORDER BY ingested_at DESC,id DESC) AS rank FROM news_documents n "
            "WHERE n.source=? AND n.ingested_at<=? AND n.published_at<=? AND n.published_at>=?) "
            "SELECT n.id,n.source,n.source_url,n.title,n.body,"
            "n.published_at,n.ingested_at,n.market_ids,n.metadata,n.content_hash,"
            "(SELECT count(*) FROM news_documents prior WHERE prior.source=n.source "
            "AND prior.source_url=n.source_url AND prior.ingested_at<=?) AS version "
            "FROM ranked n WHERE n.rank=1 ORDER BY n.source_url",
            (SOURCE, utc_text(cutoff), utc_text(cutoff), utc_text(cutoff - timedelta(days=30)),
             utc_text(cutoff)),
        ).fetchall()
    check = decode_row(check, time_fields=("checked_at",)) if check else None
    sec_check = decode_row(sec_check, time_fields=("checked_at",)) if sec_check else None
    rows = [decode_row(row, json_fields=("market_ids", "metadata"),
                       time_fields=("published_at", "ingested_at")) for row in rows]
    healthy = bool(check and check["status"] == "ok" and
                   cutoff - check["checked_at"] <= timedelta(minutes=20))
    sec_healthy = bool(sec_check and sec_check["status"] == "ok" and
                       cutoff - sec_check["checked_at"] <= timedelta(minutes=30))
    relevant = [row for row in rows if row["published_at"] >= cutoff - timedelta(hours=72)
                and (market_id is None or market_id in row["market_ids"] or row["source"] == SOURCE)]
    archive = [row for row in rows if row["published_at"] < cutoff - timedelta(hours=72)
               and (market_id is None or market_id in row["market_ids"] or row["source"] == SOURCE)]

    def public(row):
        return {"id": row["id"], "source": row["source"], "source_url": row["source_url"],
                "title": row["title"], "summary": row["body"],
                "published_at": row["published_at"].isoformat(),
                "ingested_at": row["ingested_at"].isoformat(),
                "market_ids": row["market_ids"], "metadata": row["metadata"],
                "content_hash": row["content_hash"], "version": row["version"]}

    risk = "recent_fomc_release" if any(
        row["metadata"].get("kind") == "fomc_statement" and
        cutoff - row["published_at"] <= timedelta(minutes=15) for row in relevant
    ) else "none"
    relevant.sort(key=lambda row: row["published_at"], reverse=True)
    archive.sort(key=lambda row: row["published_at"], reverse=True)
    return {"status": "offline" if not healthy else "available" if relevant else "no_recent_news",
            "source_status": {SOURCE: "ok" if healthy else "offline",
                              "sec_press_rss": "ok" if sec_healthy else "offline"},
            "source_checked_at": check["checked_at"].isoformat() if check else None,
            "cutoff": cutoff.isoformat(), "coverage_hours": 72, "risk": risk,
            "items": [public(row) for row in relevant[:12]],
            "archive": [public(row) for row in archive[:5]],
            "classification_status": "unreviewed_not_strategy_ready"}
