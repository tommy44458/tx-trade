import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from trade_helper.db import connect, init_db
from trade_helper.news_classification_worker import enqueue_candidates, run_once

from .test_news_jev import ARTICLE, fake_response

pytestmark = pytest.mark.usefixtures("pg_schema")

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)


def add_article(document_id: str, *, allowed: bool = True, ingested_at=NOW):
    with connect() as db:
        db.execute(
            "INSERT INTO news_documents(id,source,source_url,title,body,published_at,"
            "ingested_at,market_ids,metadata,content_hash) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (document_id, ARTICLE["source"], f"https://example.test/{document_id}",
             ARTICLE["title"], ARTICLE["body"], NOW - timedelta(minutes=1), ingested_at,
             json.dumps(["binance:perp:BTCUSDT"]),
             json.dumps({"model_use_allowed": allowed, "content_quality": "summary"}),
             document_id),
        )
        db.commit()


class GoodResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return fake_response()


def test_worker_classifies_only_allowed_article_once_and_records_usage(monkeypatch):
    init_db()
    monkeypatch.setenv("NEWS_JEV_DAILY_LIMIT", "1")
    add_article("article-1")
    add_article("article-2", allowed=False)
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return GoodResponse()

    result = run_once(now=NOW + timedelta(minutes=2), key="fixture-key", post=post)
    assert result["status"] == "completed"
    assert len(calls) == 1
    assert run_once(now=NOW + timedelta(minutes=3), key="fixture-key", post=post)["status"] == "daily_limit"
    with connect() as db:
        classes = db.execute("SELECT document_id,model FROM news_classifications").fetchall()
        jobs = db.execute("SELECT document_id,status FROM news_classification_jobs").fetchall()
        usage = db.execute("SELECT status,input_tokens,output_tokens FROM news_jev_calls").fetchone()
    assert len(classes) == len(jobs) == 1
    assert classes[0]["document_id"] == "article-1"
    assert jobs[0]["status"] == "completed"
    assert usage == {"status": "completed", "input_tokens": 250, "output_tokens": 40}


def test_worker_retries_explicit_rate_limit_without_duplicate_classification():
    init_db()
    add_article("article-1")
    request = httpx.Request("POST", "https://api.typesafe.ai/v1/systemone")

    def limited(*args, **kwargs):
        response = httpx.Response(429, request=request)
        response.raise_for_status()

    first = run_once(now=NOW + timedelta(minutes=2), key="fixture-key", post=limited)
    assert first["status"] == "retry"
    assert first["error_code"] == "HTTP_429"
    assert run_once(now=NOW + timedelta(minutes=2, seconds=30),
                    key="fixture-key", post=lambda *a, **k: GoodResponse())["status"] == "idle"
    second = run_once(now=NOW + timedelta(minutes=3),
                      key="fixture-key", post=lambda *a, **k: GoodResponse())
    assert second["status"] == "completed"
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM news_classifications").fetchone()["n"] == 1
        assert db.execute("SELECT count(*) AS n FROM news_jev_calls").fetchone()["n"] == 2


def test_worker_never_pays_to_classify_a_superseded_version():
    init_db()
    add_article("old")
    assert run_once(now=NOW + timedelta(minutes=2), key="")["status"] == "disabled"
    # Seed a previously queued version explicitly; a disabled worker queues none.
    assert enqueue_candidates(NOW + timedelta(minutes=2)) == 1
    with connect() as db:
        db.execute("UPDATE news_documents SET source_url='https://example.test/story' WHERE id='old'")
        db.commit()
    add_article("new", ingested_at=NOW + timedelta(minutes=3))
    with connect() as db:
        db.execute("UPDATE news_documents SET source_url='https://example.test/story' WHERE id='new'")
        db.commit()
    called = []
    result = run_once(now=NOW + timedelta(minutes=4), key="fixture-key",
                      post=lambda *a, **k: (called.append(1), GoodResponse())[1])
    assert result["status"] == "completed"
    assert result["document_id"] == "new"
    assert len(called) == 1
    with connect() as db:
        old = db.execute("SELECT status,error_code FROM news_classification_jobs "
                         "WHERE document_id='old'").fetchone()
    assert old == {"status": "blocked", "error_code": "SUPERSEDED_OR_EXPIRED"}
