from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.db import connect, init_db
from trade_helper.news import (
    mark_news_offline,
    news_snapshot,
    parse_fed_monetary_rss,
    save_news,
)

URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"


def feed(url=URL, title="Federal Reserve issues FOMC statement", date="Wed, 16 Sep 2026 18:00:00 GMT"):
    return ("<rss version='2.0'><channel><title>FRB: Press Release - Monetary Policy</title>"
            f"<item><title>{title}</title><link>{url}</link><description>{title}</description>"
            f"<pubDate>{date}</pubDate></item></channel></rss>")


def test_rss_parser_requires_official_url_and_precise_time():
    parsed = parse_fed_monetary_rss(feed())
    assert parsed[0]["metadata"]["kind"] == "fomc_statement"
    assert parsed[0]["published_at"] == "2026-09-16T18:00:00+00:00"
    assert parsed[0]["metadata"]["classification"] == "unreviewed"
    with pytest.raises(ValueError):
        parse_fed_monetary_rss(feed(url="https://www.federalreserve.gov.evil.test/fake.htm"))
    with pytest.raises(ValueError):
        parse_fed_monetary_rss(feed(date="Wed, 16 Sep 2026 18:00:00"))
    with pytest.raises(ValueError):
        parse_fed_monetary_rss("<rss/>")


@pytest.mark.usefixtures("sqlite_db")
def test_news_snapshot_is_as_of_ingestion_and_versions_are_immutable():
    init_db()
    first = datetime(2026, 9, 16, 18, 10, tzinfo=UTC)
    original = parse_fed_monetary_rss(feed())[0]
    assert save_news([original], first) == 1
    assert save_news([original], first + timedelta(minutes=5)) == 0
    earlier = news_snapshot(first - timedelta(seconds=1))
    assert earlier["status"] == "offline"
    assert earlier["items"] == []
    seen = news_snapshot(first)
    assert seen["items"][0]["version"] == 1
    assert seen["status"] == "available"
    assert seen["risk"] == "recent_fomc_release"
    assert news_snapshot(first + timedelta(minutes=16))["risk"] == "none"
    revised = original | {"title": "Federal Reserve issues corrected FOMC statement"}
    later = first + timedelta(minutes=10)
    assert save_news([revised], later) == 1
    assert news_snapshot(first + timedelta(minutes=6))["items"][0]["title"] == original["title"]
    assert news_snapshot(later)["items"][0]["version"] == 2
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM news_documents").fetchone()["n"] == 2
    with pytest.raises(ValueError, match="Future"):
        save_news([original], datetime(2026, 9, 16, 17, 0, tzinfo=UTC))


@pytest.mark.usefixtures("sqlite_db")
def test_stale_source_and_old_headline_do_not_become_current_signal():
    init_db()
    first = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
    assert save_news(parse_fed_monetary_rss(feed()), first) == 1
    view = news_snapshot(first)
    assert view["status"] == "no_recent_news"
    assert view["items"] == []
    assert len(view["archive"]) == 1
    assert news_snapshot(first + timedelta(minutes=21))["status"] == "offline"
    mark_news_offline(first + timedelta(minutes=1), "ConnectError")
    assert news_snapshot(first + timedelta(minutes=1))["status"] == "offline"
    assert news_snapshot(first)["status"] == "no_recent_news"


def test_report_rejects_future_news_in_snapshot():
    from trade_helper.agent import fallback_analysis
    from trade_helper.analysis import build_report
    from trade_helper.report_contract import validate_report

    from .test_analysis import sample_candles

    now = datetime.now(UTC)
    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": "1h", "leverage": 5}
    quote = {"price": "120", "tick_size": "0.1", "snapshot_hash": "a" * 64,
             "observed_at": now.isoformat(), "news_risk": "none"}
    candles = sample_candles(recent=True)
    news = {"status": "available", "cutoff": now.isoformat(), "risk": "none", "items": [{
        "source": "fed_monetary_rss", "source_url": URL, "version": 1,
        "content_hash": "a" * 64,
        "published_at": (now - timedelta(minutes=10)).isoformat(),
        "ingested_at": (now - timedelta(minutes=9)).isoformat()}], "archive": []}
    report = build_report(request, candles, quote, [], fallback_analysis(request, candles, quote),
                          news=news)
    validate_report(report)
    report["news_context"]["items"][0]["ingested_at"] = (now + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="learned after"):
        validate_report(report)
