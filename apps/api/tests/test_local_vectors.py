import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.db import connect, init_db
from trade_helper.local_vectors import (
    cosine_similarity,
    save_analysis_embedding,
    save_news_embedding,
    search_analysis_embeddings,
    search_news_embeddings,
    validate_embedding,
)
from trade_helper.storage_codec import dump_json, utc_text

pytestmark = pytest.mark.usefixtures("sqlite_db")
AT = datetime(2026, 9, 30, 12, tzinfo=UTC)
MARKET = "binance:perp:BTCUSDT"
MODEL = "fixture-model"


def add_analysis(analysis_id, user_id="alice", created_at=AT):
    with connect() as db:
        db.execute(
            "INSERT INTO analyses(id,user_id,idempotency_key,request_hash,request_json,"
            "positions_json,status,phase,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (analysis_id, user_id, analysis_id, "hash", "{}", "[]", "completed", "done",
             utc_text(created_at)),
        )


def add_news(document_id, *, url=None, ingested_at=AT, markets=None):
    with connect() as db:
        db.execute(
            "INSERT INTO news_documents(id,source,source_url,title,body,published_at,ingested_at,"
            "market_ids,metadata,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (document_id, "fixture", url or "https://example.test/" + document_id,
             "Event " + document_id, "Body", utc_text(AT), utc_text(ingested_at),
             dump_json(markets or [MARKET]), "{}", "hash-" + document_id),
        )


def test_local_vectors_keep_news_and_personal_analyses_separate_and_rank_cosine():
    init_db()
    add_news("news-1")
    add_news("news-2")
    save_news_embedding("news-1", [1, 0, 0], MODEL, embedded_at=AT)
    save_news_embedding("news-2", [0, 1, 0], MODEL, embedded_at=AT)
    add_analysis("ana-alice")
    add_analysis("ana-bob", "bob")
    save_analysis_embedding("ana-alice", "alice", "Alice summary", [1, 0, 0], MODEL, embedded_at=AT)
    save_analysis_embedding("ana-bob", "bob", "Bob summary", [0, 1, 0], MODEL, embedded_at=AT)
    news = search_news_embeddings([1, 0, 0], MODEL, cutoff=AT)
    assert [item["id"] for item in news] == ["news-1", "news-2"]
    assert news[0]["similarity"] == pytest.approx(1)
    assert news[1]["similarity"] == pytest.approx(0)
    assert news[0]["market_ids"] == [MARKET]
    assert "embedding" not in news[0]
    personal = search_analysis_embeddings([1, 0, 0], MODEL, "alice", cutoff=AT)
    assert [item["analysis_id"] for item in personal] == ["ana-alice"]
    assert search_analysis_embeddings([1, 0, 0], MODEL, "charlie", cutoff=AT) == []
    with pytest.raises(sqlite3.IntegrityError), connect() as db:
        db.execute(
            "INSERT INTO analysis_embeddings(analysis_id,user_id,summary,content_hash) "
            "VALUES(?,?,?,?) ON CONFLICT(analysis_id) DO UPDATE SET user_id=excluded.user_id",
            ("ana-alice", "bob", "summary", "hash"),
        )
    with pytest.raises(ValueError, match="belong"):
        save_analysis_embedding("ana-alice", "bob", "overwrite", [1, 0, 0], MODEL, embedded_at=AT)


def test_search_respects_model_dimensions_market_cutoff_and_latest_visible_version():
    init_db()
    add_news("old", url="https://example.test/story")
    save_news_embedding("old", [1, 0], MODEL, embedded_at=AT)
    revised = AT + timedelta(minutes=10)
    add_news("new", url="https://example.test/story", ingested_at=revised)
    add_news("other-model")
    save_news_embedding("other-model", [1, 0], "different-model", embedded_at=AT)
    add_news("other-dimensions")
    save_news_embedding("other-dimensions", [1, 0, 0], MODEL, embedded_at=AT)
    add_news("other-market", markets=["binance:perp:ETHUSDT"])
    save_news_embedding("other-market", [1, 0], MODEL, embedded_at=AT)
    assert [row["id"] for row in search_news_embeddings(
        [1, 0], MODEL, market_id=MARKET, cutoff=revised - timedelta(seconds=1)
    )] == ["old"]
    # A corrected document cannot reuse the prior version's embedding.
    assert search_news_embeddings([1, 0], MODEL, market_id=MARKET, cutoff=revised) == []
    save_news_embedding("new", [1, 0], MODEL, embedded_at=revised + timedelta(minutes=1))
    assert search_news_embeddings([1, 0], MODEL, market_id=MARKET, cutoff=revised) == []
    assert [row["id"] for row in search_news_embeddings(
        [1, 0], MODEL, market_id=MARKET, cutoff=revised + timedelta(minutes=1)
    )] == ["new"]


def test_analysis_embedding_observation_time_and_owner_checked_on_write_and_search():
    init_db()
    add_analysis("ana-1")
    later = AT + timedelta(minutes=1)
    save_analysis_embedding("ana-1", "alice", "summary", [1, 0], MODEL, embedded_at=later)
    assert search_analysis_embeddings([1, 0], MODEL, "alice", cutoff=AT) == []
    assert len(search_analysis_embeddings([1, 0], MODEL, "alice", cutoff=later)) == 1
    assert search_analysis_embeddings([1, 0, 0], MODEL, "alice", cutoff=later) == []
    assert search_analysis_embeddings([1, 0], "another-model", "alice", cutoff=later) == []
    with pytest.raises(ValueError, match="predate"):
        save_analysis_embedding("ana-1", "alice", "summary", [1, 0], MODEL,
                                embedded_at=AT - timedelta(seconds=1))
    add_news("news-1")
    with pytest.raises(ValueError, match="predate"):
        save_news_embedding("news-1", [1, 0], MODEL, embedded_at=AT - timedelta(seconds=1))


@pytest.mark.parametrize("embedding", [[], [0, 0], [True, 1], ["1", 1], [float("nan")],
                                       [float("inf")], [1] * 8193])
def test_invalid_or_fake_embedding_is_rejected(embedding):
    with pytest.raises(ValueError):
        validate_embedding(embedding)


def test_cosine_handles_large_numbers_and_mismatched_dimensions():
    assert cosine_similarity([1e308, 1e308], [1e308, 1e308]) == pytest.approx(1)
    assert cosine_similarity([1, 0], [-1, 0]) == pytest.approx(-1)
    with pytest.raises(ValueError, match="dimensions differ"):
        cosine_similarity([1, 0], [1, 0, 0])
