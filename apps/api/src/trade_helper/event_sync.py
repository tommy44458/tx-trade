"""Periodically refresh official release schedules without any model API key."""

import sys
import time
from datetime import UTC, datetime

import httpx

from .config import assert_local_mode
from .db import init_db
from .events import (
    SOURCES,
    mark_source_offline,
    parse_bea_html,
    parse_bls_ics,
    parse_fed_html,
    save_source,
)


def sync_once(fetch=None) -> dict[str, dict]:
    if fetch is None:
        with httpx.Client(timeout=15, follow_redirects=True,
                          headers={"User-Agent": "txinTrade/0.1 calendar research"}) as client:
            return sync_once(lambda url: client.get(url).raise_for_status().text)
    results = {}
    for source, url in SOURCES.items():
        checked = datetime.now(UTC)
        try:
            body = fetch(url)
            if len(body.encode()) > 2_000_000:
                raise ValueError("Calendar body too large")
            if source == "bls":
                events = parse_bls_ics(body)
            elif source == "bea":
                events = parse_bea_html(body)
            else:
                events = parse_fed_html(body, checked.year)
            if not events:
                raise ValueError("Relevant calendar entries missing")
            changed = save_source(source, events, checked)
            results[source] = {"status": "ok", "parsed": len(events), "changed": changed}
        except (httpx.HTTPError, UnicodeError, ValueError, KeyError) as exc:
            mark_source_offline(source, checked, type(exc).__name__)
            results[source] = {"status": "offline", "error_code": type(exc).__name__}
    return results


def main() -> None:
    assert_local_mode()
    init_db()
    once = "--once" in sys.argv
    while True:
        print(sync_once(), flush=True)
        if once:
            return
        time.sleep(6 * 60 * 60)


if __name__ == "__main__":
    main()
