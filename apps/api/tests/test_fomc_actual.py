from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.db import init_db
from trade_helper.events import _record, event_snapshot, save_source
from trade_helper.fomc_actual import parse_target_range
from trade_helper.news import save_news
from trade_helper.news_sync import sync_once

URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
TITLE = "Federal Reserve issues FOMC statement"
SENTENCE = ("The Committee decided to raise the target range for the federal funds "
            "rate by 1/4 percentage point to 3-3/4 to 4 percent.")
BODY = ("Recent indicators suggest that economic activity has continued to expand.\n\n"
        + SENTENCE + "\n\nThe Committee will continue to monitor the data.")
PUBLISHED = datetime(2026, 9, 16, 18, 0, tzinfo=UTC)
OBSERVED = PUBLISHED + timedelta(minutes=5)


def _item(body=BODY, *, origin="official_release", url=URL, published=PUBLISHED):
    return {"source": "fed_monetary_rss", "source_url": url, "title": TITLE,
            "body": body, "published_at": published.isoformat(), "market_ids": [],
            "metadata": {"kind": "fomc_statement", "origin": origin,
                         "content_quality": "body" if origin == "official_release" else "headline"}}


def test_parser_requires_one_explicit_and_plausible_decision():
    actual_sentence = (SENTENCE[:-1] + ", in support of the Federal Reserve's dual mandate.")
    assert parse_target_range(actual_sentence)["upper_pct"] == "4"
    assert parse_target_range(BODY) == {"decision": "raise", "lower_pct": "3.75",
                                        "upper_pct": "4", "evidence_sentence": SENTENCE}
    maintain = "The Committee decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent."
    assert parse_target_range(maintain)["decision"] == "maintain"
    assert parse_target_range(maintain)["upper_pct"] == "3.75"
    assert parse_target_range("The Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 3.5 to 3.75 percent.")["decision"] == "lower"
    assert parse_target_range(BODY + "\n\n" + maintain) is None
    assert parse_target_range("The Committee decided to raise the target range for the federal funds rate to 4 to 3 percent.") is None
    assert parse_target_range("Federal Reserve raises rates to 4 percent") is None


@pytest.mark.usefixtures("sqlite_db")
def test_fomc_result_requires_calendar_body_publication_and_ingestion():
    init_db()
    calendar = _record("fed", "fomc:2026:september", "fomc", "FOMC September 政策會議",
                       PUBLISHED.date(), None,
                       "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
    save_source("fed", [calendar], PUBLISHED - timedelta(days=1))
    assert event_snapshot(PUBLISHED - timedelta(seconds=1))["events"][0]["actual_value"] is None
    save_news([_item(origin="official_rss", body=TITLE)], PUBLISHED + timedelta(minutes=1))
    assert event_snapshot(PUBLISHED + timedelta(minutes=2))["events"][0]["actual_value"] is None
    save_news([_item()], OBSERVED)
    early = event_snapshot(OBSERVED - timedelta(seconds=1))
    assert early["events"][0]["actual_value"] is None
    result = event_snapshot(OBSERVED)
    event = result["events"][0]
    assert event["actual_value"] == "3.75–4%"
    assert event["actual_unit"] == "target_range_percent"
    assert event["official_result"]["source_url"] == URL
    assert event["official_result"]["published_at"] == PUBLISHED.isoformat()
    assert result["actual_values_status"] == "partial_fomc"
    assert result["risk"] == "none"
    assert event_snapshot(OBSERVED - timedelta(seconds=1))["events"][0]["actual_value"] is None


@pytest.mark.usefixtures("sqlite_db")
def test_wrong_release_date_does_not_attach_to_meeting():
    init_db()
    calendar = _record("fed", "fomc:2026:september", "fomc", "FOMC September 政策會議",
                       PUBLISHED.date(), None,
                       "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
    save_source("fed", [calendar], PUBLISHED - timedelta(days=1))
    save_news([_item(url=URL.replace("0916", "0917"))], OBSERVED)
    assert event_snapshot(OBSERVED)["events"][0]["actual_value"] is None


@pytest.mark.usefixtures("sqlite_db")
def test_fomc_body_backfill_is_prioritized_over_recent_other_items():
    init_db()
    old = PUBLISHED
    recent = OBSERVED + timedelta(days=12)
    feed = ("<rss version='2.0'><channel><title>FRB: Press Release - Monetary Policy</title>"
            + "".join(
                f"<item><title>Federal Reserve reports item {i}</title>"
                f"<link>https://www.federalreserve.gov/newsevents/pressreleases/monetary20260928{chr(97+i)}.htm</link>"
                f"<pubDate>Mon, 28 Sep 2026 18:00:00 GMT</pubDate></item>"
                for i in range(5))
            + f"<item><title>{TITLE}</title><link>{URL}</link>"
            "<pubDate>Wed, 16 Sep 2026 18:00:00 GMT</pubDate></item></channel></rss>")
    html = (f"<div id='article'><h3 class='title'>{TITLE}</h3>"
            f"<div class='col-xs-12 col-sm-8 col-md-8'><p>{BODY}</p></div></div>")
    result = sync_once(lambda url: feed if url.endswith("press_monetary.xml") else html,
                       checked=recent)
    assert result["body_fetched"] == 1
    assert old < recent - timedelta(days=7)


def test_report_rejects_future_or_modified_fomc_actual():
    from trade_helper.agent import fallback_analysis
    from trade_helper.analysis import build_report
    from trade_helper.report_contract import validate_report

    from .test_analysis import sample_candles

    now = datetime.now(UTC)
    sentence = parse_target_range(BODY)
    result = sentence | {"parser_version": "fomc_target_range_v1", "document_id": "news-fixture",
                         "source_url": URL, "content_hash": "a" * 64,
                         "published_at": PUBLISHED.isoformat(),
                         "ingested_at": OBSERVED.isoformat()}
    events = {"status": "available", "risk": "none", "cutoff": now.isoformat(),
              "events": [{"id": "evt-fixture", "version": 1, "source": "fed", "kind": "fomc",
                          "scheduled_date": "2026-09-16", "scheduled_at": None,
                          "ingested_at": (PUBLISHED - timedelta(days=1)).isoformat(),
                          "actual_value": "3.75–4%", "actual_unit": "target_range_percent",
                          "official_result": result}]}
    quote = {"price": "120", "tick_size": "0.1", "snapshot_hash": "a" * 64,
             "observed_at": now.isoformat(), "event_risk": "none",
             "events_status": "available"}
    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": "1h", "leverage": 5}
    candles = sample_candles(recent=True)
    report = build_report(request, candles, quote, [], fallback_analysis(request, candles, quote),
                          events=events)
    validate_report(report)
    report["event_context"]["events"][0]["official_result"]["ingested_at"] = (
        now + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="Invalid or future"):
        validate_report(report)
    report["event_context"]["events"][0]["official_result"]["ingested_at"] = OBSERVED.isoformat()
    report["event_context"]["events"][0]["official_result"]["lower_pct"] = "3.5"
    with pytest.raises(ValueError, match="Invalid or future"):
        validate_report(report)
