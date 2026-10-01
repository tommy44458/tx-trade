"""Poll Fed monetary RSS and enrich recent items from official release pages."""

import sys
import time
from datetime import UTC, datetime, timedelta

import httpx

from .config import assert_local_mode
from .db import connect, init_db
from .fed_release import enrich_fed_item
from .news import FED_MONETARY_FEED, mark_news_offline, parse_fed_monetary_rss, save_news
from .storage_codec import to_datetime, utc_text

MAX_BODY_FETCHES_PER_SYNC = 5
BODY_LOOKBACK = timedelta(days=7)
FOMC_BODY_LOOKBACK = timedelta(days=30)


def _body_due(url: str, checked: datetime) -> bool:
    with connect(readonly=True) as db:
        row = db.execute("SELECT next_check_at FROM news_body_fetches WHERE source_url=?",
                         (url,)).fetchone()
    return row is None or to_datetime(row["next_check_at"]) <= checked


def _record_body_checks(checks: list[tuple[str, str, str | None]], checked: datetime) -> None:
    with connect() as db:
        for url, status, code in checks:
            interval = timedelta(hours=6) if status == "ok" else timedelta(minutes=30)
            db.execute(
                "INSERT INTO news_body_fetches(source_url,status,checked_at,next_check_at,error_code) "
                "VALUES(?,?,?,?,?) ON CONFLICT(source_url) DO UPDATE SET "
                "status=EXCLUDED.status,checked_at=EXCLUDED.checked_at,"
                "next_check_at=EXCLUDED.next_check_at,error_code=EXCLUDED.error_code",
                (url, status, utc_text(checked), utc_text(checked + interval), code),
            )
        db.commit()


def _sync(fetch, checked: datetime) -> dict:
    try:
        items = parse_fed_monetary_rss(fetch(FED_MONETARY_FEED))
        checks: list[tuple[str, str, str | None]] = []
        enriched = []
        for item in sorted(items, key=lambda row: row["metadata"]["kind"] != "fomc_statement"):
            published = datetime.fromisoformat(item["published_at"])
            lookback = FOMC_BODY_LOOKBACK if item["metadata"]["kind"] == "fomc_statement" else BODY_LOOKBACK
            if (published > checked or checked - published > lookback or
                    len(checks) >= MAX_BODY_FETCHES_PER_SYNC or
                    not _body_due(item["source_url"], checked)):
                enriched.append(item)
                continue
            try:
                enriched.append(enrich_fed_item(item, fetch(item["source_url"])))
                checks.append((item["source_url"], "ok", None))
            except (httpx.HTTPError, UnicodeError, ValueError, KeyError, TypeError) as exc:
                enriched.append(item)
                checks.append((item["source_url"], "offline", type(exc).__name__))
        changed = save_news(enriched, checked)
        _record_body_checks(checks, checked)
        return {"status": "ok", "parsed": len(items), "changed": changed,
                "body_fetched": sum(status == "ok" for _, status, _ in checks),
                "body_failed": sum(status != "ok" for _, status, _ in checks)}
    except (httpx.HTTPError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        mark_news_offline(checked, type(exc).__name__)
        return {"status": "offline", "error_code": type(exc).__name__}


def sync_once(fetch=None, *, checked: datetime | None = None) -> dict:
    checked = checked or datetime.now(UTC)
    if checked.tzinfo is None:
        raise ValueError("News check time must include timezone")
    checked = checked.astimezone(UTC)
    if fetch is not None:
        return _sync(fetch, checked)
    with httpx.Client(timeout=15, follow_redirects=True,
                      headers={"User-Agent": "txTrade/0.1 official news research"}) as client:
        return _sync(lambda url: client.get(url).raise_for_status().text, checked)


def main() -> None:
    assert_local_mode()
    init_db()
    once = "--once" in sys.argv
    while True:
        print(sync_once(), flush=True)
        if once:
            return
        time.sleep(5 * 60)


if __name__ == "__main__":
    main()
