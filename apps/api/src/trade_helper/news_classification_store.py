"""Append-only Jev results and observation-time-safe review snapshots.

These results are unreviewed candidates. Strategy agents must not consume them
until a separate evidence review step verifies the underlying event.
"""

import json
from datetime import UTC, datetime, timedelta

from .db import connect, new_id
from .news_jev import QUESTION_VERSION, classification_input_hash, validate_response
from .storage_codec import decode_row, load_json, to_datetime, utc_text


def save_classification(document_id: str, result: dict,
                        classified_at: datetime | None = None) -> bool:
    """Persist a Jev response only for the exact permitted document it classified."""
    if result.get("status") != "classified_unreviewed":
        raise ValueError("Only validated, unreviewed Jev results can be stored")
    if result.get("question_version") != QUESTION_VERSION:
        raise ValueError("Unknown Jev question version")
    validated = validate_response(result)
    classified_at = classified_at or datetime.now(UTC)
    if classified_at.tzinfo is None:
        raise ValueError("Classification time must include timezone")
    classified_at = classified_at.astimezone(UTC)
    with connect() as db:
        document = db.execute(
            "SELECT source,title,body,ingested_at,metadata FROM news_documents WHERE id=?",
            (document_id,),
        ).fetchone()
        if document is None:
            raise ValueError("News document does not exist")
        metadata = load_json(document["metadata"])
        if (metadata.get("model_use_allowed") is not True or
                metadata.get("content_quality") not in {"summary", "body"}):
            raise ValueError("News document is not licensed or detailed enough for Jev")
        if classified_at < to_datetime(document["ingested_at"]):
            raise ValueError("Classification cannot predate news ingestion")
        expected_hash = classification_input_hash(
            title=document["title"], body=document["body"], source=document["source"],
        )
        if result.get("input_hash") != expected_hash:
            raise ValueError("Jev input differs from the stored news document")
        inserted = db.execute(
            "INSERT INTO news_classifications(id,document_id,question_version,input_hash,"
            "model,answers,usage,status,classified_at) "
            "VALUES(?,?,?,?,?,?,?,'classified_unreviewed',?) "
            "ON CONFLICT(document_id,question_version,input_hash,model) DO NOTHING RETURNING id",
            (new_id("jev"), document_id, QUESTION_VERSION, expected_hash,
             validated["model"], json.dumps(validated["answers"]),
             json.dumps(validated["usage"]), utc_text(classified_at)),
        ).fetchone()
        db.commit()
    return inserted is not None


def classification_snapshot(cutoff: datetime, market_id: str | None = None,
                            lookback_hours: int = 72, limit: int = 100) -> dict:
    """Find the latest visible version of each story and its visible Jev result."""
    if cutoff.tzinfo is None or not 1 <= lookback_hours <= 24 * 30 or not 1 <= limit <= 500:
        raise ValueError("Invalid classification snapshot window")
    cutoff = cutoff.astimezone(UTC)
    with connect(readonly=True) as db:
        rows = db.execute(
            "WITH latest AS (SELECT id,source,source_url,title,published_at,ingested_at,"
            "market_ids,content_hash,ROW_NUMBER() OVER (PARTITION BY source,source_url "
            "ORDER BY ingested_at DESC,id DESC) AS rank FROM news_documents "
            "WHERE ingested_at<=? AND published_at<=?), "
            "classes AS (SELECT id,document_id,classified_at,question_version,model,answers,"
            "ROW_NUMBER() OVER (PARTITION BY document_id ORDER BY classified_at DESC,id DESC) "
            "AS rank FROM news_classifications WHERE classified_at<=?) "
            "SELECT n.id AS document_id,n.source,n.source_url,n.title,n.published_at,"
            "n.ingested_at,n.content_hash,c.id AS classification_id,c.classified_at,"
            "c.question_version,c.model,c.answers FROM latest n "
            "LEFT JOIN classes c ON c.document_id=n.id AND c.rank=1 "
            "WHERE n.rank=1 AND n.published_at>=? "
            "AND (? IS NULL OR EXISTS (SELECT 1 FROM json_each(n.market_ids) WHERE value=?)) "
            "ORDER BY n.published_at DESC,n.id DESC LIMIT ?",
            (utc_text(cutoff), utc_text(cutoff), utc_text(cutoff),
             utc_text(cutoff - timedelta(hours=lookback_hours)), market_id, market_id, limit),
        ).fetchall()
    rows = [decode_row(row, json_fields=("answers",),
                       time_fields=("published_at", "ingested_at", "classified_at")) for row in rows]
    items = [{"document_id": row["document_id"], "source": row["source"],
              "source_url": row["source_url"], "title": row["title"],
              "published_at": row["published_at"].isoformat(),
              "ingested_at": row["ingested_at"].isoformat(),
              "content_hash": row["content_hash"],
              "classification_status": "classified_unreviewed" if row["classification_id"] else "pending",
              "classification_id": row["classification_id"],
              "classified_at": row["classified_at"].isoformat() if row["classified_at"] else None,
              "question_version": row["question_version"], "model": row["model"],
              "answers": row["answers"]} for row in rows]
    return {"cutoff": cutoff.isoformat(), "market_id": market_id,
            "lookback_hours": lookback_hours, "items": items,
            "pending_count": sum(item["classification_status"] == "pending" for item in items),
            "unreviewed_count": sum(item["classification_status"] == "classified_unreviewed"
                                    for item in items)}
