import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trade_helper import desktop_updates, discussions, macro_interpretation, macro_translation
from trade_helper.api import app
from trade_helper.config import local_user_id
from trade_helper.db import connect, database_path, init_db, utc_now
from trade_helper.news_classification_worker import _claim as claim_news
from trade_helper.worker import run_once as run_analysis

from .test_macro_interpretation import evidence, result

TOKEN = "isolated-test-desktop-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
PREFIX = "/api/v1/desktop-updates"


@pytest.fixture
def desktop_client(monkeypatch):
    monkeypatch.setenv("APP_DESKTOP", "1")
    monkeypatch.setenv("APP_DESKTOP_TOKEN", TOKEN)
    with TestClient(app) as client:
        yield client


def add_analysis(*, status="queued", identifier="analysis-update-test"):
    with connect() as db:
        db.execute(
            "INSERT INTO analyses(id,user_id,idempotency_key,request_hash,request_json,"
            "positions_json,status,phase,created_at,lease_until) VALUES(?,?,?,?,'{}','[]',?,?,?,?)",
            (identifier, local_user_id(), identifier, "fixture", status, "model", utc_now(),
             (datetime.now(UTC) - timedelta(days=1)).isoformat()),
        )


def test_update_endpoints_require_desktop_and_current_private_session(monkeypatch):
    # Test the router itself so protection does not depend on the app middleware.
    isolated = FastAPI()
    isolated.include_router(desktop_updates.router)
    init_db()
    with TestClient(isolated) as client:
        for action in ("prepare", "cancel"):
            assert client.post(f"{PREFIX}/{action}", headers=HEADERS).status_code == 403
        monkeypatch.setenv("APP_DESKTOP", "1")
        monkeypatch.setenv("APP_DESKTOP_TOKEN", TOKEN)
        for action in ("prepare", "cancel"):
            assert client.post(f"{PREFIX}/{action}").status_code == 401
            assert client.post(f"{PREFIX}/{action}", headers={
                "Authorization": "Bearer previous-session-token"}).status_code == 401
    with connect(readonly=True) as db:
        assert not desktop_updates._gate_exists(db)


def test_preparation_preserves_wal_database_and_reuses_private_backup(desktop_client):
    add_analysis()
    # A previously created directory must not keep permissive access settings.
    directory = database_path().parent / "backups"
    directory.mkdir(mode=0o755)
    if os.name == "posix":
        directory.chmod(0o755)
    first = desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS)
    assert first.status_code == 200
    state = first.json()
    assert state["ready"] and state["active_tasks"] == 0
    output = Path(state["backup_path"])
    assert output.is_file() and output.parent == database_path().parent / "backups"
    with sqlite3.connect(output) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert backup.execute("SELECT status FROM analyses").fetchone() == ("queued",)
        assert backup.execute("PRAGMA user_version").fetchone() == (9,)
    if os.name == "posix":
        assert output.stat().st_mode & 0o777 == 0o600
        assert output.parent.stat().st_mode & 0o777 == 0o700
    repeated = desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()
    assert repeated["backup_path"] == state["backup_path"]
    assert TOKEN.encode() not in output.read_bytes()
    assert desktop_client.post(f"{PREFIX}/cancel", headers=HEADERS).json() == {"cancelled": True}
    assert output.is_file()
    with connect() as db:
        assert not desktop_updates.update_is_draining(db)


def test_busy_running_analysis_keeps_gate_even_if_its_lease_expired(desktop_client, monkeypatch):
    add_analysis(status="running")
    monkeypatch.setattr(desktop_updates, "backup_database",
                        lambda _path: pytest.fail("Never back up while models are running"))
    state = desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()
    assert state["ready"] is False and state["active_tasks"] == 1
    assert state["task_counts"]["analyses"] == 1
    assert not run_analysis()
    with connect() as db:
        row = db.execute("SELECT status,error_code FROM analyses").fetchone()
        assert row == {"status": "running", "error_code": None}
        assert desktop_updates.update_is_draining(db)


def test_prepare_waits_for_completion_then_creates_backup(desktop_client):
    add_analysis(status="running")
    assert not desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()["ready"]
    # This represents the original worker committing its result without being cancelled.
    with connect() as db:
        db.execute("UPDATE analyses SET status='completed',report_json='{}'")
    state = desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()
    assert state["ready"] and state["active_tasks"] == 0
    with sqlite3.connect(state["backup_path"]) as backup:
        assert backup.execute("SELECT status FROM analyses").fetchone() == ("completed",)


def test_backup_failure_is_safe_and_cancel_restores_claims(desktop_client, monkeypatch):
    def failed_backup(_path):
        raise OSError("private fixture data must not appear in errors")

    monkeypatch.setattr(desktop_updates, "backup_database", failed_backup)
    result = desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS)
    assert result.status_code == 503
    assert result.json()["detail"]["code"] == "UPDATE_BACKUP_FAILED"
    assert "private fixture" not in result.text
    snapshot = evidence()
    assert macro_interpretation._claim(snapshot, local_user_id(), retry=False) is None
    assert desktop_client.post(f"{PREFIX}/cancel", headers=HEADERS).status_code == 200
    assert macro_interpretation._claim(snapshot, local_user_id(), retry=False) is not None


def test_preparing_blocks_mutations_and_new_jobs_but_preserves_reads(desktop_client):
    assert desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()["ready"]
    result = desktop_client.post("/api/v1/analyses", headers=HEADERS | {
        "Idempotency-Key": "new-analysis-during-update"}, json={
            "kind": "market", "market_id": "binance:perp:BTCUSDT", "timeframe": "1h"})
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "DESKTOP_UPDATE_PREPARING"
    assert desktop_client.get("/api/v1/analyses", headers=HEADERS).status_code == 200
    assert desktop_client.get("/api/v1/health", headers=HEADERS).status_code == 200
    assert desktop_client.post(f"{PREFIX}/cancel", headers=HEADERS).status_code == 200
    assert desktop_client.post("/api/v1/analyses", headers=HEADERS | {
        "Idempotency-Key": "new-analysis-during-update"}, json={
            "kind": "market", "market_id": "binance:perp:BTCUSDT", "timeframe": "1h"}).status_code == 202


def test_every_model_claim_stops_before_expiry_cleanup(desktop_client, monkeypatch):
    snapshot = evidence()
    claim = macro_interpretation._claim(snapshot, local_user_id(), retry=False)
    old = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    add_analysis(status="completed")
    with connect() as db:
        db.execute("UPDATE macro_interpretations SET lease_until=?", (old,))
        db.execute(
            "INSERT INTO discussion_sessions(id,user_id,subject_type,subject_id,analysis_id,"
            "subject_json,context_json,created_at,updated_at) "
            "VALUES('session',?,'analysis','analysis-update-test','analysis-update-test','{}','{}',?,?)",
            (local_user_id(), old, old),
        )
        db.execute("INSERT INTO discussion_messages(id,session_id,sequence,role,status,"
                   "request_id,created_at) VALUES('question','session',1,'user','completed','request',?)",
                   (old,))
        db.execute("INSERT INTO discussion_messages(id,session_id,sequence,role,status,"
                   "reply_to_id,created_at,lease_until) "
                   "VALUES('answer','session',2,'assistant','running','question',?,?)", (old, old))
        db.execute("INSERT INTO macro_translation_jobs(id,user_id,interpretation_id,response_locale,"
                   "render_version,source_result_sha256,source_result_json,status,lease_until,"
                   "created_at,updated_at) VALUES('translation',?,?,'en-US','fixture','fixture','{}',"
                   "'running',?,?,?)", (local_user_id(), claim["id"], old, old, old))
        db.execute("INSERT INTO news_documents(id,source,source_url,title,body,published_at,"
                   "ingested_at,market_ids,metadata,content_hash) "
                   "VALUES('news','fixture','https://fixture.test/article','Title','Body',?,?,'[]','{}','hash')",
                   (old, old))
        db.execute("INSERT INTO news_classification_jobs(document_id,question_version,model_alias,"
                   "status,next_attempt_at,lease_until,updated_at) "
                   "VALUES('news','fixture','fixture','running',?,?,?)", (old, old, old))
        db.execute("INSERT INTO news_jev_calls(id,document_id,started_at,status) "
                   "VALUES('call','news',?,'started')", (old,))
    state = desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()
    assert not state["ready"] and state["active_tasks"] == 5
    assert discussions.claim_next(timeout=10) is None
    assert macro_translation.claim_macro_translation(timeout=10) is None
    assert macro_interpretation._claim(snapshot, local_user_id(), retry=True) is None
    assert claim_news(datetime.now(UTC)) == (None, "update_preparing")
    monkeypatch.setattr(desktop_updates, "backup_database",
                        lambda _path: pytest.fail("Expired leases are still running"))
    assert desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()["active_tasks"] == 5


def test_gate_serializes_with_inline_model_claim(desktop_client):
    snapshot = evidence()
    # Both operations use independent connections and BEGIN IMMEDIATE. Either
    # claim wins and prepare reports busy, or prepare wins and claim cannot start.
    with ThreadPoolExecutor(max_workers=2) as pool:
        claiming = pool.submit(macro_interpretation._claim, snapshot, local_user_id(), retry=False)
        preparing = pool.submit(desktop_client.post, f"{PREFIX}/prepare", headers=HEADERS)
        claim = claiming.result(timeout=10)
        state = preparing.result(timeout=10).json()
    assert (claim is None and state["ready"] and state["active_tasks"] == 0) or (
        claim is not None and not state["ready"] and state["active_tasks"] == 1)
    with connect() as db:
        assert desktop_updates.update_is_draining(db)


def test_new_desktop_session_recovers_gate_without_deleting_backup(desktop_client):
    state = desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()
    desktop_updates.reset_desktop_update_gate(force=True)
    with connect() as db:
        assert not desktop_updates.update_is_draining(db)
    assert Path(state["backup_path"]).is_file()
    assert macro_interpretation._claim(evidence(), local_user_id(), retry=False) is not None


def test_web_development_claims_ignore_stale_desktop_gate(desktop_client, monkeypatch):
    assert desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()["ready"]
    monkeypatch.setenv("APP_DESKTOP", "0")
    assert macro_interpretation._claim(evidence(), local_user_id(), retry=False) is not None


def test_expiry_before_prepare_cannot_hide_a_still_executing_model(desktop_client, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    snapshot = evidence()
    claim = macro_interpretation._claim(snapshot, local_user_id(), retry=False)

    def generate(_evidence, **_kwargs):
        started.set()
        assert finish.wait(timeout=10)
        return result(), {"provider": "fixture", "model": "fixture"}

    monkeypatch.setattr(macro_interpretation, "_generate", generate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(macro_interpretation._run_claim, claim)
        try:
            assert started.wait(timeout=5)
            # Another API request may have expired the row before installation
            # was requested. That state change does not terminate the provider.
            with connect() as db:
                db.execute("UPDATE macro_interpretations SET status='failed',lease_until=NULL")
            state = desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()
            assert not state["ready"] and state["active_tasks"] == 1
            assert state["task_counts"]["macro_interpretations"] == 1
        finally:
            finish.set()
        running.result(timeout=5)
    ready = desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()
    assert ready["ready"] and ready["active_tasks"] == 0


def test_retry_completion_does_not_clear_an_older_execution_marker(desktop_client):
    snapshot = evidence()
    original = macro_interpretation._claim(snapshot, local_user_id(), retry=False)
    with connect() as db:
        db.execute("UPDATE macro_interpretations SET status='failed',lease_until=NULL")
    retry = macro_interpretation._claim(snapshot, local_user_id(), retry=True)
    assert original["update_execution_id"] != retry["update_execution_id"]
    desktop_updates.finish_desktop_execution(retry["update_execution_id"])
    with connect() as db:
        db.execute("UPDATE macro_interpretations SET status='failed',lease_until=NULL")
    assert desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()["active_tasks"] == 1
    desktop_updates.finish_desktop_execution(original["update_execution_id"])
    assert desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()["ready"]


def test_api_lifespan_does_not_clear_execution_markers_from_same_session(desktop_client):
    claim = macro_interpretation._claim(evidence(), local_user_id(), retry=False)
    desktop_updates.reset_desktop_update_gate()
    with connect(readonly=True) as db:
        assert db.execute("SELECT id FROM desktop_model_executions").fetchone()["id"] == (
            claim["update_execution_id"])
    # A fresh backend session can clear abandoned markers after all previous
    # service processes have been stopped by the desktop supervisor.
    desktop_updates.reset_desktop_update_gate(force=True)
    with connect(readonly=True) as db:
        assert db.execute("SELECT id FROM desktop_model_executions").fetchone() is None


def test_analysis_failure_finishes_actual_execution_marker(desktop_client):
    add_analysis()  # Deliberately incomplete input fails before any model or network call.
    assert run_analysis()
    with connect(readonly=True) as db:
        assert db.execute("SELECT status FROM analyses").fetchone()["status"] == "failed"
        assert db.execute("SELECT id FROM desktop_model_executions").fetchone() is None
    assert desktop_client.post(f"{PREFIX}/prepare", headers=HEADERS).json()["ready"]


def test_missing_database_allows_lazy_settings_without_creating_master_key(monkeypatch):
    monkeypatch.setenv("APP_DESKTOP", "1")
    monkeypatch.setenv("APP_DESKTOP_TOKEN", TOKEN)
    assert not database_path().exists()
    # No lifespan: this request is the first operation on the isolated store.
    response = TestClient(app).put("/api/v1/settings", headers=HEADERS, json={"clear_jev": True})
    assert response.status_code == 200
    with connect(readonly=True) as db:
        assert not desktop_updates._gate_exists(db)
        assert db.execute("SELECT count(*) AS n FROM credential_local_keys").fetchone()["n"] == 0
        assert db.execute("SELECT count(*) AS n FROM credential_records").fetchone()["n"] == 0


def test_existing_unreadable_update_state_denies_mutation_without_fail_open(monkeypatch):
    from trade_helper import local_settings

    monkeypatch.setenv("APP_DESKTOP", "1")
    monkeypatch.setenv("APP_DESKTOP_TOKEN", TOKEN)
    path = database_path()
    path.parent.mkdir(parents=True)
    path.write_bytes(b"isolated corrupt SQLite fixture")
    monkeypatch.setattr(local_settings, "_write_settings",
                        lambda *_args: pytest.fail("Unavailable update state must deny mutation"))
    response = TestClient(app).put("/api/v1/settings", headers=HEADERS, json={})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "UPDATE_STATE_UNAVAILABLE"
    assert path.read_bytes() == b"isolated corrupt SQLite fixture"
