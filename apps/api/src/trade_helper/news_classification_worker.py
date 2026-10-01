"""Bounded background Jev classification of licensed, complete news versions."""

import os
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx

from .config import assert_local_mode
from .credential_store import CredentialStoreError
from .db import connect, init_db, new_id
from .local_settings import integration_credentials
from .news_classification_store import save_classification
from .news_jev import MODEL_ALIAS, QUESTION_VERSION, classify_article
from .storage_codec import load_json


def enqueue_candidates(now: datetime, limit: int = 100) -> int:
    if now.tzinfo is None or not 1 <= limit <= 500:
        raise ValueError("Invalid news queue time or limit")
    now = now.astimezone(UTC)
    model_alias = os.getenv("TYPESAFE_MODEL", MODEL_ALIAS)
    with connect() as db:
        rows = db.execute(
            "WITH ranked AS (SELECT id,metadata,body,published_at, "
            "ROW_NUMBER() OVER (PARTITION BY source,source_url ORDER BY ingested_at DESC,id DESC) AS rank "
            "FROM news_documents WHERE ingested_at<=? AND published_at<=? "
            "), latest AS (SELECT * FROM ranked WHERE rank=1) "
            "SELECT n.id FROM latest n WHERE n.published_at>=? "
            "AND json_extract(n.metadata,'$.model_use_allowed')=1 "
            "AND json_extract(n.metadata,'$.content_quality') IN ('body','summary') "
            "AND length(n.body) BETWEEN 80 AND 6000 "
            "AND NOT EXISTS (SELECT 1 FROM news_classifications c WHERE c.document_id=n.id "
            "AND c.question_version=?) "
            "ORDER BY n.published_at DESC,n.id DESC LIMIT ?",
            (now, now, now - timedelta(days=7), QUESTION_VERSION, limit),
        ).fetchall()
        added = 0
        for row in rows:
            inserted = db.execute(
                "INSERT INTO news_classification_jobs(document_id,question_version,model_alias,"
                "status,next_attempt_at,updated_at) VALUES(?,?,?,'queued',?,?) "
                "ON CONFLICT DO NOTHING RETURNING document_id",
                (row["id"], QUESTION_VERSION, model_alias, now, now),
            ).fetchone()
            added += inserted is not None
        db.commit()
    return added


def _daily_limit() -> int:
    value = int(os.getenv("NEWS_JEV_DAILY_LIMIT", "100"))
    if not 1 <= value <= 10000:
        raise ValueError("NEWS_JEV_DAILY_LIMIT must be between 1 and 10000")
    return value


def _claim(now: datetime) -> tuple[dict | None, str | None]:
    with connect() as db:
        db.execute(
            "UPDATE news_classification_jobs SET status='blocked',"
            "error_code='SUPERSEDED_OR_EXPIRED',lease_until=NULL,updated_at=? "
            "WHERE status IN ('queued','retry') AND ("
            "EXISTS (SELECT 1 FROM news_documents n JOIN news_documents newer "
            "ON newer.source=n.source AND newer.source_url=n.source_url "
            "WHERE n.id=news_classification_jobs.document_id AND "
            "(newer.ingested_at>n.ingested_at OR "
            "(newer.ingested_at=n.ingested_at AND newer.id>n.id))) OR "
            "EXISTS (SELECT 1 FROM news_documents n WHERE n.id=news_classification_jobs.document_id "
            "AND n.published_at<?))",
            (now, now - timedelta(days=7)),
        )
        db.execute(
            "UPDATE news_classification_jobs SET status='failed',"
            "error_code='MODEL_RESULT_UNKNOWN',lease_until=NULL,updated_at=? "
            "WHERE status='running' AND lease_until<?", (now, now),
        )
        db.execute(
            "UPDATE news_jev_calls SET status='unknown',error_code='MODEL_RESULT_UNKNOWN',"
            "finished_at=? WHERE status='started' AND started_at<?",
            (now, now - timedelta(seconds=30)),
        )
        calls = db.execute(
            "SELECT count(*) AS n FROM news_jev_calls WHERE started_at>=?",
            (now.replace(hour=0, minute=0, second=0, microsecond=0),),
        ).fetchone()["n"]
        if calls >= _daily_limit():
            db.commit()
            return None, "daily_limit"
        row = db.execute(
            "SELECT j.document_id,j.question_version,j.model_alias,j.attempts,"
            "n.source,n.title,n.body,n.metadata "
            "FROM news_classification_jobs j JOIN news_documents n ON n.id=j.document_id "
            "WHERE j.status IN ('queued','retry') AND j.next_attempt_at<=? "
            "ORDER BY j.next_attempt_at,j.document_id LIMIT 1",
            (now,),
        ).fetchone()
        if row is None:
            db.commit()
            return None, "idle"
        db.execute(
            "UPDATE news_classification_jobs SET status='running',attempts=attempts+1,"
            "lease_until=?,updated_at=? WHERE document_id=? AND question_version=? "
            "AND model_alias=?",
            (now + timedelta(seconds=30), now, row["document_id"],
             row["question_version"], row["model_alias"]),
        )
        call_id = new_id("jev_call")
        db.execute(
            "INSERT INTO news_jev_calls(id,document_id,started_at,status) "
            "VALUES(?,?,?,'started')", (call_id, row["document_id"], now),
        )
        db.commit()
    result = dict(row)
    result["metadata"] = load_json(result["metadata"])
    return result, call_id


def _finish(row: dict, call_id: str, now: datetime, status: str,
            error_code: str | None = None, usage: dict | None = None) -> None:
    with connect() as db:
        next_at = now + timedelta(minutes=min(30, 2 ** row["attempts"]))
        db.execute(
            "UPDATE news_classification_jobs SET status=?,error_code=?,lease_until=NULL,"
            "next_attempt_at=?,updated_at=? WHERE document_id=? AND question_version=? "
            "AND model_alias=? AND status='running'",
            (status, error_code, next_at, now, row["document_id"],
             row["question_version"], row["model_alias"]),
        )
        db.execute(
            "UPDATE news_jev_calls SET status=?,error_code=?,finished_at=?,"
            "input_tokens=?,output_tokens=? WHERE id=?",
            (status, error_code, now, (usage or {}).get("input_tokens"),
             (usage or {}).get("output_tokens"), call_id),
        )
        db.commit()


def run_once(*, now: datetime | None = None, key: str | None = None, post=None) -> dict:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Classification time must include timezone")
    now = now.astimezone(UTC)
    try:
        secret = key if key is not None else (integration_credentials("jev") or {}).get("api_key")
    except CredentialStoreError:
        return {"status": "credential_store_unavailable", "queued": 0}
    if not secret:
        return {"status": "disabled", "queued": 0}
    queued = enqueue_candidates(now)
    row, call_id = _claim(now)
    if row is None:
        return {"status": call_id, "queued": queued}
    article = {"source": row["source"], "title": row["title"], "body": row["body"],
               "model_use_allowed": row["metadata"].get("model_use_allowed"),
               "content_quality": row["metadata"].get("content_quality")}
    try:
        result = classify_article(article, key=secret, post=post)
        if result["status"] != "classified_unreviewed":
            _finish(row, call_id, now, "blocked", result["status"])
            return {"status": "blocked", "document_id": row["document_id"],
                    "error_code": result["status"]}
        save_classification(row["document_id"], result, now)
    except httpx.HTTPStatusError as exc:
        code = f"HTTP_{exc.response.status_code}"
        retry = exc.response.status_code == 429 or 500 <= exc.response.status_code < 600
        status = "retry" if retry and row["attempts"] < 2 else "failed"
        _finish(row, call_id, now, status, code)
        return {"status": status, "document_id": row["document_id"], "error_code": code}
    except httpx.ConnectError as exc:
        status = "retry" if row["attempts"] < 2 else "failed"
        _finish(row, call_id, now, status, type(exc).__name__)
        return {"status": status, "document_id": row["document_id"],
                "error_code": type(exc).__name__}
    except Exception as exc:  # noqa: BLE001 - no retry after an uncertain model result
        _finish(row, call_id, now, "failed", type(exc).__name__)
        return {"status": "failed", "document_id": row["document_id"],
                "error_code": type(exc).__name__}
    _finish(row, call_id, now, "completed", usage=result["usage"])
    return {"status": "completed", "document_id": row["document_id"],
            "model": result["model"], "usage": result["usage"]}


def main() -> None:
    assert_local_mode()
    init_db()
    once = "--once" in sys.argv
    while True:
        print(run_once(), flush=True)
        if once:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
