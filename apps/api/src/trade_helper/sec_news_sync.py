"""Low-rate polling of the SEC's public press-release RSS feed."""

import sys
import time
from datetime import UTC, datetime

import httpx

from .config import assert_local_mode
from .db import init_db
from .news import mark_news_offline
from .sec_news import FEED_URL, SOURCE, parse_sec_press_rss, save_sec_news


def sync_once(fetch=None, *, checked: datetime | None = None) -> dict:
    checked = checked or datetime.now(UTC)
    if checked.tzinfo is None:
        raise ValueError("SEC check time must include timezone")
    checked = checked.astimezone(UTC)
    try:
        if fetch is None:
            with httpx.Client(timeout=15, follow_redirects=False,
                              headers={"User-Agent": "txTrade/0.1 (local personal research)"}) as client:
                raw = client.get(FEED_URL).raise_for_status().text
        else:
            raw = fetch(FEED_URL)
        items = parse_sec_press_rss(raw)
        changed = save_sec_news(items, checked)
        return {"status": "ok", "parsed": len(items), "changed": changed,
                "summary_eligible": sum(item["metadata"]["model_use_allowed"] for item in items)}
    except (httpx.HTTPError, UnicodeError, ValueError, KeyError, TypeError) as exc:
        mark_news_offline(checked, type(exc).__name__, SOURCE)
        return {"status": "offline", "error_code": type(exc).__name__}


def main() -> None:
    assert_local_mode()
    init_db()
    once = "--once" in sys.argv
    while True:
        print(sync_once(), flush=True)
        if once:
            return
        time.sleep(15 * 60)


if __name__ == "__main__":
    main()
