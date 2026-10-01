"""Local embedding persistence and exact cosine search, without a database server.

Only real embeddings supplied by a configured model are stored. Model identifiers,
dimensions, ownership and observation times are kept separate during retrieval.
Exact search is suitable for the local archive; no extension or service is needed.
"""

import hashlib
import math
from datetime import UTC, datetime

from .db import connect
from .storage_codec import decode_row, dump_json, load_json, to_datetime, utc_text


def validate_embedding(value: object) -> list[float]:
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 8192:
        raise ValueError("Embedding must contain between 1 and 8192 dimensions")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError("Embedding dimensions must be numeric")
    vector = [float(item) for item in value]
    if any(not math.isfinite(item) for item in vector) or not any(vector):
        raise ValueError("Embedding must be finite and have a nonzero norm")
    return vector


def _model(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("Embedding model is required")
    return value.strip()


def cosine_similarity(left: object, right: object) -> float:
    a, b = validate_embedding(left), validate_embedding(right)
    if len(a) != len(b):
        raise ValueError("Embedding dimensions differ")
    # Rescaling first avoids overflow for otherwise finite large dimensions.
    a_scale, b_scale = max(map(abs, a)), max(map(abs, b))
    a, b = [item / a_scale for item in a], [item / b_scale for item in b]
    a_norm, b_norm = math.hypot(*a), math.hypot(*b)
    return max(-1.0, min(1.0, math.fsum(
        (x / a_norm) * (y / b_norm) for x, y in zip(a, b, strict=True)
    )))


def save_news_embedding(document_id: str, embedding: object, model: str,
                        *, embedded_at: datetime | None = None) -> None:
    vector, model = validate_embedding(embedding), _model(model)
    embedded_at = embedded_at or datetime.now(UTC)
    timestamp = utc_text(embedded_at)
    with connect() as db:
        row = db.execute("SELECT ingested_at FROM news_documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise ValueError("News document does not exist")
        if embedded_at < to_datetime(row["ingested_at"]):
            raise ValueError("Embedding cannot predate news ingestion")
        db.execute("UPDATE news_documents SET embedding=?,embedding_model=?,embedded_at=? WHERE id=?",
                   (dump_json(vector), model, timestamp, document_id))


def save_analysis_embedding(analysis_id: str, user_id: str, summary: str,
                            embedding: object, model: str,
                            *, embedded_at: datetime | None = None) -> None:
    vector, model = validate_embedding(embedding), _model(model)
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("Analysis summary is required")
    embedded_at = embedded_at or datetime.now(UTC)
    timestamp = utc_text(embedded_at)
    with connect() as db:
        owner = db.execute("SELECT user_id,created_at FROM analyses WHERE id=?", (analysis_id,)).fetchone()
        if owner is None or owner["user_id"] != user_id:
            raise ValueError("Analysis does not belong to this user")
        if embedded_at < to_datetime(owner["created_at"]):
            raise ValueError("Embedding cannot predate the analysis")
        db.execute(
            "INSERT INTO analysis_embeddings(analysis_id,user_id,summary,content_hash,embedding,"
            "embedding_model,embedded_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(analysis_id) DO UPDATE SET summary=excluded.summary,"
            "content_hash=excluded.content_hash,embedding=excluded.embedding,"
            "embedding_model=excluded.embedding_model,embedded_at=excluded.embedded_at",
            (analysis_id, user_id, summary, hashlib.sha256(summary.encode()).hexdigest(),
             dump_json(vector), model, timestamp),
        )


def _rank(rows: list, query: list[float], *, limit: int, identity: str) -> list[dict]:
    matches = []
    for raw in rows:
        row = dict(raw)
        try:
            vector = validate_embedding(load_json(row.pop("embedding")))
        except (TypeError, ValueError, OverflowError):
            continue
        if len(vector) != len(query):
            continue
        row["similarity"] = cosine_similarity(query, vector)
        matches.append(row)
    matches.sort(key=lambda row: (-row["similarity"], row[identity]))
    return matches[:limit]


def _search_input(embedding: object, model: str, limit: int,
                  cutoff: datetime | None) -> tuple[list[float], str, str]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError("Invalid embedding search limit")
    return validate_embedding(embedding), _model(model), utc_text(cutoff or datetime.now(UTC))


def search_news_embeddings(embedding: object, model: str, *, market_id: str | None = None,
                           cutoff: datetime | None = None, limit: int = 10) -> list[dict]:
    query, model, at = _search_input(embedding, model, limit, cutoff)
    with connect(readonly=True) as db:
        rows = db.execute(
            "WITH ranked AS (SELECT id,source,source_url,title,body,content_hash,published_at,"
            "ingested_at,market_ids,metadata,embedding,embedding_model,embedded_at,"
            "ROW_NUMBER() OVER (PARTITION BY source,source_url ORDER BY ingested_at DESC,id DESC) "
            "AS rank FROM news_documents WHERE published_at<=? AND ingested_at<=?) "
            "SELECT * FROM ranked WHERE rank=1 AND embedding_model=? AND embedded_at<=? "
            "AND embedding IS NOT NULL AND (? IS NULL OR EXISTS "
            "(SELECT 1 FROM json_each(market_ids) WHERE value=?))",
            (at, at, model, at, market_id, market_id),
        ).fetchall()
    matches = _rank(rows, query, limit=limit, identity="id")
    for row in matches:
        row.pop("rank", None)
    return [decode_row(row, json_fields=("market_ids", "metadata")) for row in matches]


def search_analysis_embeddings(embedding: object, model: str, user_id: str,
                               *, cutoff: datetime | None = None, limit: int = 10) -> list[dict]:
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("Embedding search user is required")
    query, model, at = _search_input(embedding, model, limit, cutoff)
    with connect(readonly=True) as db:
        rows = db.execute(
            "SELECT e.analysis_id,e.user_id,e.summary,e.content_hash,e.embedding,e.embedding_model,"
            "e.embedded_at FROM analysis_embeddings e JOIN analyses a ON a.id=e.analysis_id "
            "AND a.user_id=e.user_id WHERE e.user_id=? AND e.embedding_model=? "
            "AND e.embedding IS NOT NULL AND e.embedded_at<=? AND a.created_at<=?",
            (user_id, model, at, at),
        ).fetchall()
    return _rank(rows, query, limit=limit, identity="analysis_id")
