from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.db import connect, init_db
from trade_helper.news import news_snapshot
from trade_helper.news_classification_worker import enqueue_candidates
from trade_helper.sec_news import parse_sec_press_rss
from trade_helper.sec_news_sync import sync_once

pytestmark = pytest.mark.usefixtures("sqlite_db")

NOW = datetime(2026, 9, 23, 19, tzinfo=UTC)
URL = "https://www.sec.gov/newsroom/press-releases/2026-90-test-tokenized-markets"
TITLE = "SEC issues statement on tokenized markets"
SUMMARY = ("The Securities and Exchange Commission published a statement on how "
           "tokenized securities may be traded. The release describes a proposed "
           "framework and says the agency is requesting comments from market participants…")


def feed(summary=SUMMARY, url=URL):
    return ("<rss version='2.0'><channel><title>Press Releases</title>"
            f"<item><title>{TITLE}</title><link>{url}</link>"
            f"<description>{summary}</description>"
            "<pubDate>Wed, 23 Sep 2026 14:00:00 -0400</pubDate>"
            "</item></channel></rss>")


def test_sec_parser_restricts_official_urls_and_marks_short_text_ineligible():
    article = parse_sec_press_rss(feed())[0]
    assert article["published_at"] == "2026-09-23T18:00:00+00:00"
    assert article["metadata"]["content_quality"] == "summary"
    assert article["metadata"]["model_use_allowed"] is True
    assert article["metadata"]["summary_truncated"] is True
    assert parse_sec_press_rss(feed(summary="A brief item."))[0]["metadata"]["model_use_allowed"] is False
    with pytest.raises(ValueError, match="Unexpected SEC release URL"):
        parse_sec_press_rss(feed(url="https://www.sec.gov.evil.test/newsroom/press-releases/x"))
    with pytest.raises(ValueError, match="Unexpected SEC press-release feed"):
        parse_sec_press_rss(feed().replace("<title>Press Releases</title>", "<title>Other</title>", 1))


def test_sec_feed_versions_are_idempotent_and_only_summaries_queue_for_jev():
    init_db()
    first = sync_once(lambda url: feed(), checked=NOW)
    assert first == {"status": "ok", "parsed": 1, "changed": 1, "summary_eligible": 1}
    assert sync_once(lambda url: feed(), checked=NOW + timedelta(minutes=5))["changed"] == 0
    assert news_snapshot(NOW + timedelta(minutes=5))["source_status"]["sec_press_rss"] == "ok"
    assert enqueue_candidates(NOW + timedelta(minutes=6)) == 1
    assert enqueue_candidates(NOW + timedelta(minutes=7)) == 0
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM news_documents").fetchone()["n"] == 1
        assert db.execute("SELECT status FROM news_source_checks WHERE source='sec_press_rss' "
                          "ORDER BY id DESC LIMIT 1").fetchone()["status"] == "ok"
    assert sync_once(lambda url: "<rss/>", checked=NOW + timedelta(minutes=8))["status"] == "offline"
