import json
from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.db import connect, init_db
from trade_helper.news_classification_store import classification_snapshot, save_classification
from trade_helper.news_jev import QUESTIONS, classification_input_hash

pytestmark = pytest.mark.usefixtures("sqlite_db")

START = datetime(2026, 9, 28, 12, tzinfo=UTC)
MARKET = "binance:perp:BTCUSDT"
TITLE = "Bitcoin exchange pauses matching"
BODY = ("The exchange reported that its Bitcoin perpetual order matching engine "
        "had stopped accepting new orders at noon. Its status page said engineers "
        "were investigating and that trading would resume after checks completed.")


def add_document(document_id, *, ingested_at=START, body=BODY, allowed=True):
    with connect() as db:
        db.execute(
            "INSERT INTO news_documents(id,source,source_url,title,body,published_at,"
            "ingested_at,market_ids,metadata,content_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (document_id, "licensed_fixture", "https://example.test/news/1", TITLE, body,
             START - timedelta(minutes=1), ingested_at, json.dumps([MARKET]),
             json.dumps({"model_use_allowed": allowed, "content_quality": "summary"}),
             document_id),
        )
        db.commit()


def jev_result(body=BODY):
    answers = {}
    for name, question in QUESTIONS.items():
        if question["type"] == "noul":
            answers[name] = {"type": "noul", "noul": 0.9}
        elif question["type"] == "choice":
            names = list(question["criteria"])
            answers[name] = {"type": "choice", "choice": names[0], "confidence": 0.9,
                             "probabilities": {key: float(key == names[0]) for key in names}}
        else:
            answers[name] = {"type": "score", "score": 2.0, "confidence": 0.8,
                             "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0, "3": 0.0}}
    return {"status": "classified_unreviewed", "question_version": "news_jev_v1",
            "input_hash": classification_input_hash(title=TITLE, body=body,
                                                    source="licensed_fixture"),
            "model": "jev-fixture", "answers": answers,
            "usage": {"input_tokens": 150, "output_tokens": 40}}


def test_classification_is_idempotent_and_only_visible_after_it_is_recorded():
    init_db()
    add_document("news-1")
    result = jev_result()
    classified_at = START + timedelta(minutes=5)
    assert save_classification("news-1", result, classified_at)
    assert not save_classification("news-1", result, classified_at + timedelta(minutes=1))
    before = classification_snapshot(classified_at - timedelta(seconds=1), MARKET)
    after = classification_snapshot(classified_at, MARKET)
    assert before["pending_count"] == 1
    assert before["items"][0]["answers"] is None
    assert after["unreviewed_count"] == 1
    assert after["items"][0]["answers"]["topic"]["choice"] == "macro_policy"
    assert classification_snapshot(classified_at, "binance:perp:ETHUSDT")["items"] == []


def test_revised_story_needs_its_own_classification():
    init_db()
    add_document("news-1")
    save_classification("news-1", jev_result(), START + timedelta(minutes=5))
    revised = BODY + " The venue later reported a recovery window."
    add_document("news-2", ingested_at=START + timedelta(minutes=10), body=revised)
    old = classification_snapshot(START + timedelta(minutes=9), MARKET)
    new = classification_snapshot(START + timedelta(minutes=10), MARKET)
    assert old["items"][0]["document_id"] == "news-1"
    assert old["unreviewed_count"] == 1
    assert new["items"][0]["document_id"] == "news-2"
    assert new["pending_count"] == 1
    assert save_classification("news-2", jev_result(revised), START + timedelta(minutes=12))
    assert classification_snapshot(START + timedelta(minutes=12), MARKET)["unreviewed_count"] == 1


def test_rights_input_hash_and_time_are_enforced():
    init_db()
    add_document("blocked", allowed=False)
    with pytest.raises(ValueError, match="licensed"):
        save_classification("blocked", jev_result(), START)
    add_document("news-1")
    with pytest.raises(ValueError, match="differs"):
        save_classification("news-1", jev_result(BODY + " changed"), START)
    with pytest.raises(ValueError, match="predate"):
        save_classification("news-1", jev_result(), START - timedelta(seconds=1))
    with pytest.raises(ValueError, match="Only validated"):
        save_classification("news-1", {"status": "pending_key"}, START)
