from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.db import connect, init_db
from trade_helper.fed_release import extract_release_body
from trade_helper.news import news_snapshot
from trade_helper.news_sync import sync_once
from trade_helper.storage_codec import load_json

pytestmark = pytest.mark.usefixtures("sqlite_db")

URL = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260928a.htm"
TITLE = "Federal Reserve issues FOMC statement"
NOW = datetime(2026, 9, 28, 18, 10, tzinfo=UTC)
BODY = ("The Federal Open Market Committee announced that it would maintain its "
        "target range for the federal funds rate. The committee said it would "
        "continue monitoring incoming employment and inflation data.")
HTML = ("<html><body><p>Navigation must not enter the news body.</p>"
        f"<div id='article'><h3 class='title'>{TITLE}</h3>"
        f"<div class='col-xs-12 col-sm-8 col-md-8'><p>{BODY}</p>"
        "<p>For media inquiries, contact the Board.</p></div></div>"
        "<footer><p>Footer must not enter the news body.</p></footer></body></html>")
FEED = ("<rss version='2.0'><channel><title>FRB: Press Release - Monetary Policy</title>"
        f"<item><title>{TITLE}</title><link>{URL}</link>"
        f"<description>{TITLE}</description>"
        "<pubDate>Mon, 28 Sep 2026 18:00:00 GMT</pubDate></item></channel></rss>")


def test_release_parser_accepts_only_matching_official_primary_body():
    assert extract_release_body(HTML, url=URL, expected_title=TITLE) == BODY
    with pytest.raises(ValueError, match="title differs"):
        extract_release_body(HTML, url=URL, expected_title="Different story")
    with pytest.raises(ValueError, match="Invalid official"):
        extract_release_body(HTML, url="https://federalreserve.gov.evil.test/x", expected_title=TITLE)
    with pytest.raises(ValueError, match="missing or oversized"):
        extract_release_body(HTML.replace(BODY, "Short"), url=URL, expected_title=TITLE)


def test_sync_enriches_once_and_does_not_downgrade_body_to_rss_title():
    init_db()
    fetched = []

    def fetch(url):
        fetched.append(url)
        return FEED if url.endswith("press_monetary.xml") else HTML

    first = sync_once(fetch, checked=NOW)
    assert first["body_fetched"] == 1
    assert first["changed"] == 1
    assert len(fetched) == 2
    with connect() as db:
        row = db.execute("SELECT body,metadata FROM news_documents").fetchone()
        assert row["body"] == BODY
        assert load_json(row["metadata"])["model_use_allowed"] is True
        assert load_json(row["metadata"])["content_quality"] == "body"
    second = sync_once(fetch, checked=NOW + timedelta(minutes=5))
    assert second["body_fetched"] == 0
    assert second["changed"] == 0
    assert len(fetched) == 3
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM news_documents").fetchone()["n"] == 1
    assert news_snapshot(NOW + timedelta(minutes=5))["items"][0]["summary"] == BODY


def test_failed_body_fetch_keeps_headline_unclassified_and_tracks_failure():
    init_db()

    def fetch(url):
        return FEED if url.endswith("press_monetary.xml") else "<html>wrong article</html>"

    result = sync_once(fetch, checked=NOW)
    assert result["status"] == "ok"
    assert result["body_failed"] == 1
    with connect() as db:
        article = db.execute("SELECT body,metadata FROM news_documents").fetchone()
        check = db.execute("SELECT status,error_code FROM news_body_fetches").fetchone()
    assert article["body"] == TITLE
    assert load_json(article["metadata"]).get("model_use_allowed") is None
    assert check == {"status": "offline", "error_code": "ValueError"}


def test_official_body_flows_to_jev_but_stays_unreviewed():
    from trade_helper.news_classification_store import classification_snapshot
    from trade_helper.news_classification_worker import run_once

    from .test_news_jev import fake_response

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return fake_response()

    init_db()
    sync_once(lambda url: FEED if url.endswith("press_monetary.xml") else HTML, checked=NOW)
    result = run_once(now=NOW + timedelta(minutes=1), key="fixture-key",
                      post=lambda *args, **kwargs: Response())
    assert result["status"] == "completed"
    before = classification_snapshot(NOW, "binance:perp:BTCUSDT")
    after = classification_snapshot(NOW + timedelta(minutes=1), "binance:perp:BTCUSDT")
    assert before["pending_count"] == 1
    assert after["unreviewed_count"] == 1
    assert after["items"][0]["classification_status"] == "classified_unreviewed"
