"""Drain model work and preserve SQLite before the desktop installs an update.

The gate shares the workers' BEGIN IMMEDIATE transaction, so a job claim and
starting a drain cannot pass each other. It stays closed until cancellation or
a fresh desktop backend startup; an expired model lease never proves that its
provider request has finished. This runtime table does not change the product
data schema or persist the private desktop session token.
"""

import hmac
import os
import threading
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .database_backup import backup_database
from .db import connect, database_path, new_id, utc_now
from .product_version import product_version

router = APIRouter(prefix="/api/v1/desktop-updates", tags=["desktop-updates"])
_preparation_lock = threading.RLock()
_TABLE = "desktop_update_gate"
_EXECUTIONS = "desktop_model_executions"
_initialized_session: tuple[str, str] | None = None
_TASKS = {
    "analyses": ("analyses", "status='running'"),
    "discussions": ("discussion_messages", "status='running'"),
    "macro_interpretations": ("macro_interpretations", "status='running'"),
    "macro_translations": ("macro_translation_jobs", "status='running'"),
    "news_classifications": ("news_classification_jobs", "status='running'"),
    "news_model_calls": ("news_jev_calls", "status='started'"),
}
_TASK_IDS = {name: "id" for name in _TASKS}
_TASK_IDS["news_classifications"] = "document_id || char(0) || question_version || char(0) || model_alias"


def _gate_exists(db) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,),
    ).fetchone() is not None


def _executions_exist(db) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                      (_EXECUTIONS,)).fetchone() is not None


def register_desktop_execution(db, kind: str, task_id: str) -> str | None:
    """Register in the same claim transaction; task lease cleanup must not remove this."""
    if os.getenv("APP_DESKTOP") != "1":
        return None
    if kind not in _TASKS:
        raise ValueError("Unknown desktop model task")
    db.execute(f"CREATE TABLE IF NOT EXISTS {_EXECUTIONS} ("
               "id TEXT PRIMARY KEY, kind TEXT NOT NULL, task_id TEXT NOT NULL,"
               "started_at TEXT NOT NULL)")
    identifier = new_id("execution")
    db.execute(f"INSERT INTO {_EXECUTIONS}(id,kind,task_id,started_at) VALUES(?,?,?,?)",
               (identifier, kind, task_id, utc_now()))
    return identifier


def finish_desktop_execution(identifier: str | None, *, db=None) -> None:
    """Release only after this invocation has returned, independently of job status."""
    if identifier is None:
        return
    if db is not None:
        if _executions_exist(db):
            db.execute(f"DELETE FROM {_EXECUTIONS} WHERE id=?", (identifier,))
        return
    with connect() as connection:
        finish_desktop_execution(identifier, db=connection)


def update_is_draining(db) -> bool:
    """Call inside the same transaction as a new claim or task insertion."""
    return (os.getenv("APP_DESKTOP") == "1" and _gate_exists(db)
            and db.execute(f"SELECT 1 FROM {_TABLE} WHERE singleton=1").fetchone() is not None)


def require_task_start_allowed(db) -> None:
    if update_is_draining(db):
        raise HTTPException(409, {
            "code": "DESKTOP_UPDATE_PREPARING",
            "message": "The application is preparing an update. Finish or cancel the update first.",
        })


def reset_desktop_update_gate(*, force: bool = False) -> None:
    """A new private desktop backend session recovers a previously interrupted install."""
    if os.getenv("APP_DESKTOP") != "1":
        return
    global _initialized_session
    session = (str(database_path()), sha256(os.getenv("APP_DESKTOP_TOKEN", "").encode()).hexdigest())
    with _preparation_lock:
        # desktop_runtime calls this before spawning workers. FastAPI's lifespan
        # must not then clear execution markers created by those new workers.
        if not force and _initialized_session == session:
            return
        with connect() as db:
            if _gate_exists(db):
                db.execute(f"DELETE FROM {_TABLE}")
            if _executions_exist(db):
                db.execute(f"DELETE FROM {_EXECUTIONS}")
        _initialized_session = session


def _require_desktop_session(request: Request) -> None:
    if os.getenv("APP_DESKTOP") != "1":
        raise HTTPException(403, "Desktop update preparation is only available in the desktop app")
    expected = os.getenv("APP_DESKTOP_TOKEN", "")
    authorization = request.headers.get("Authorization", "")
    supplied = authorization[7:] if authorization.startswith("Bearer ") else ""
    if not expected or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "Desktop session authorization required")


def _task_counts(db) -> dict[str, int]:
    markers = _executions_exist(db)
    counts = {}
    for name, (table, condition) in _TASKS.items():
        query = f"SELECT {_TASK_IDS[name]} AS task_id FROM {table} WHERE {condition}"
        parameters = ()
        if markers:
            # A job may have been expired by another request before preparation,
            # even though its provider request has not actually returned yet.
            query += f" UNION SELECT task_id FROM {_EXECUTIONS} WHERE kind=?"
            parameters = (name,)
        counts[name] = db.execute(f"SELECT count(*) AS n FROM ({query})", parameters).fetchone()["n"]
    return counts


@router.post("/prepare")
def prepare_update(request: Request):
    _require_desktop_session(request)
    # Serialize backup and cancel requests in this private API process. Workers
    # remain independent and can commit completion while preparation is polling.
    with _preparation_lock:
        with connect() as db:
            db.execute(f"CREATE TABLE IF NOT EXISTS {_TABLE} ("
                       "singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
                       "generation TEXT NOT NULL, started_at TEXT NOT NULL, backup_path TEXT)")
            db.execute(f"INSERT INTO {_TABLE}(singleton,generation,started_at) VALUES(1,?,?) "
                       "ON CONFLICT(singleton) DO NOTHING", (new_id("update"), utc_now()))
            gate = db.execute(f"SELECT * FROM {_TABLE} WHERE singleton=1").fetchone()
            counts = _task_counts(db)
        active = sum(counts.values())
        if active:
            return {"ready": False, "active_tasks": active, "task_counts": counts}

        saved = Path(gate["backup_path"]) if gate["backup_path"] else None
        if saved is None or not saved.is_file():
            timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            output = database_path().parent / "backups" / (
                f"before-update-{product_version()}-{timestamp}-{gate['generation']}.sqlite3"
            )
            try:
                output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if os.name == "posix":
                    output.parent.chmod(0o700)
                saved = backup_database(output)
            except Exception as exc:
                raise HTTPException(503, {
                    "code": "UPDATE_BACKUP_FAILED",
                    "message": "The local database backup failed. Cancel the update and try again.",
                }) from exc
            with connect() as db:
                current = db.execute(f"SELECT generation FROM {_TABLE} WHERE singleton=1").fetchone()
                if current is None or current["generation"] != gate["generation"]:
                    raise HTTPException(409, {"code": "UPDATE_PREPARATION_CANCELLED",
                                              "message": "Update preparation was cancelled."})
                db.execute(f"UPDATE {_TABLE} SET backup_path=? WHERE singleton=1", (str(saved),))
        return {"ready": True, "active_tasks": 0, "task_counts": counts, "backup_path": str(saved)}


@router.post("/cancel")
def cancel_update(request: Request):
    _require_desktop_session(request)
    with _preparation_lock, connect() as db:
        if _gate_exists(db):
            db.execute(f"DELETE FROM {_TABLE}")
    return {"cancelled": True}
