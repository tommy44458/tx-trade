import multiprocessing
import os
import sqlite3
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone

import pytest

from trade_helper.db import (
    SCHEMA_VERSION,
    Database,
    connect,
    database_path,
    init_db,
    utc_now,
)


def _claim_process(_index):
    claimed = []
    while True:
        with connect() as db:
            row = db.execute("SELECT id FROM claim_test WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return claimed
            db.execute("UPDATE claim_test SET status='running' WHERE id=?", (row["id"],))
            claimed.append(row["id"])


def _insert_analysis(db, identifier="analysis_1", owner="owner"):
    db.execute("""INSERT INTO analyses(
        id,user_id,idempotency_key,request_hash,request_json,status,phase,created_at
    ) VALUES (?,?,?,'hash','{}','completed','done',?)""",
               (identifier, owner, identifier, utc_now()))


def test_database_is_embedded_private_and_ignores_legacy_pg_url(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_DB_PATH", raising=False)
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "private data"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://invalid-host/no-server")
    init_db()
    assert database_path() == tmp_path / "private data" / "trade_helper.sqlite3"
    assert database_path().is_file()
    with connect(readonly=True) as db:
        assert db.execute("PRAGMA journal_mode").fetchone() == {"journal_mode": "wal"}
        assert db.execute("PRAGMA foreign_keys").fetchone() == {"foreign_keys": 1}
        assert db.execute("PRAGMA user_version").fetchone() == {"user_version": SCHEMA_VERSION}
    assert os.name != "posix" or database_path().stat().st_mode & 0o777 == 0o600


def test_transaction_exception_rolls_back_and_clean_exit_commits(sqlite_db):
    init_db()
    with pytest.raises(ValueError, match="interrupted"), connect() as db:
        _insert_analysis(db, "discarded")
        raise ValueError("interrupted")
    with connect() as db:
        _insert_analysis(db, "retained")
    with connect(readonly=True) as db:
        assert db.execute("SELECT id FROM analyses").fetchall() == [{"id": "retained"}]


def test_explicit_commit_starts_an_atomic_transaction_for_later_statements(sqlite_db):
    init_db()
    with pytest.raises(ValueError), connect() as db:
        _insert_analysis(db, "committed")
        db.commit()
        _insert_analysis(db, "rolled_back")
        raise ValueError("second transaction failed")
    with connect(readonly=True) as db:
        assert db.execute("SELECT id FROM analyses").fetchall() == [{"id": "committed"}]


def test_sql_script_does_not_commit_half_applied_migration(sqlite_db):
    init_db()
    with pytest.raises(sqlite3.OperationalError), connect() as db:
        db.executescript("CREATE TABLE interrupted_migration(id INTEGER); INVALID STATEMENT;")
    with connect(readonly=True) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='interrupted_migration'").fetchone() is None


def test_sql_script_keeps_quoted_semicolons_and_trigger_body_together(sqlite_db):
    init_db()
    with connect() as db:
        db.executescript("""
            CREATE TABLE script_test(value TEXT);
            CREATE TABLE trigger_test(value TEXT);
            CREATE TRIGGER copy_test AFTER INSERT ON script_test BEGIN
                INSERT INTO trigger_test VALUES ('hello;world');
                INSERT INTO trigger_test VALUES (NEW.value);
            END;
            INSERT INTO script_test VALUES ('second;value');
        """)
    with connect(readonly=True) as db:
        assert db.execute("SELECT value FROM trigger_test").fetchall() == [
            {"value": "hello;world"}, {"value": "second;value"},
        ]


def test_foreign_keys_reject_wrong_owner_and_delete_embedding_with_analysis(sqlite_db):
    init_db()
    with connect() as db:
        _insert_analysis(db)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"), connect() as db:
        db.execute("""INSERT INTO analysis_embeddings(
            analysis_id,user_id,summary,content_hash) VALUES ('analysis_1','other','text','hash')""")
    with connect() as db:
        db.execute("""INSERT INTO analysis_embeddings(
            analysis_id,user_id,summary,content_hash) VALUES ('analysis_1','owner','text','hash')""")
        db.execute("DELETE FROM analyses WHERE id='analysis_1'")
    with connect(readonly=True) as db:
        assert db.execute("SELECT * FROM analysis_embeddings").fetchall() == []


def test_wal_reader_observes_committed_snapshot_while_writer_is_open(sqlite_db):
    init_db()
    with connect() as db:
        db.execute("CREATE TABLE wal_test(value INTEGER)")
        db.execute("INSERT INTO wal_test VALUES (1)")
    with connect() as writer:
        writer.execute("UPDATE wal_test SET value=2")
        with ThreadPoolExecutor(max_workers=1) as pool:
            def read_committed():
                with connect(readonly=True) as reader:
                    return reader.execute("SELECT value FROM wal_test").fetchone()
            assert pool.submit(read_committed).result(timeout=2) == {"value": 1}
    with connect(readonly=True) as db:
        assert db.execute("SELECT value FROM wal_test").fetchone() == {"value": 2}


def test_readonly_connection_cannot_write(sqlite_db):
    init_db()
    with pytest.raises(sqlite3.OperationalError, match="readonly"), connect(readonly=True) as db:
        db.execute("DELETE FROM analyses")


def test_multi_process_claims_are_unique_and_complete(sqlite_db):
    init_db()
    with connect() as db:
        db.execute("CREATE TABLE claim_test(id INTEGER PRIMARY KEY, status TEXT NOT NULL)")
        db.connection.executemany("INSERT INTO claim_test VALUES (?,'queued')", [(i,) for i in range(60)])
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=4, mp_context=context) as pool:
        claims = list(pool.map(_claim_process, range(4)))
    claimed = [identifier for batch in claims for identifier in batch]
    assert sorted(claimed) == list(range(60))
    assert len(set(claimed)) == len(claimed)


def test_existing_sqlite_positions_upgrade_keeps_rows(sqlite_db):
    path = database_path()
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as old:
        old.execute("""CREATE TABLE positions (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, market_id TEXT NOT NULL,
            version INTEGER NOT NULL, side TEXT NOT NULL,
            leverage INTEGER NOT NULL, margin_mode TEXT NOT NULL,
            entry_price TEXT NOT NULL, quantity TEXT NOT NULL,
            stop_loss TEXT, take_profit TEXT, status TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        old.execute("""INSERT INTO positions VALUES(
            'legacy','owner','binance:perp:BTCUSDT',1,'long',2,'isolated','100','1',
            NULL,NULL,'open','old','old')""")
    init_db()
    init_db()
    with connect(readonly=True) as db:
        row = db.execute("SELECT id,source,previous_stop_loss FROM positions").fetchone()
    assert row == {"id": "legacy", "source": "manual", "previous_stop_loss": None}


def test_future_schema_version_is_not_rewritten(sqlite_db):
    init_db()
    with connect() as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer application"):
        init_db()
    with connect(readonly=True) as db:
        assert db.execute("PRAGMA user_version").fetchone()["user_version"] == SCHEMA_VERSION + 1


def test_datetime_bindings_are_utc_and_defaults_have_same_format(sqlite_db):
    init_db()
    local = datetime(2026, 10, 1, 8, tzinfo=timezone(timedelta(hours=8)))
    with connect() as db:
        db.execute("INSERT INTO event_sources(source,status,checked_at) VALUES ('test','ok',?)", (local,))
        db.execute("""INSERT INTO economic_events(id,source,source_uid,current_version)
                   VALUES ('event','test','1',1)""")
    with connect(readonly=True) as db:
        checked = db.execute("SELECT checked_at FROM event_sources").fetchone()["checked_at"]
        created = db.execute("SELECT created_at FROM economic_events").fetchone()["created_at"]
    assert checked == "2026-10-01T00:00:00.000000+00:00"
    assert datetime.fromisoformat(created).tzinfo == UTC
    assert len(created) == len(checked)


def test_consensus_cooldown_remains_database_enforced(sqlite_db):
    init_db()
    reserved = datetime(2026, 10, 1, tzinfo=UTC)
    with pytest.raises(sqlite3.IntegrityError), connect() as db:
        db.execute("""INSERT INTO consensus_provider_checks(
            provider,reserved_at,next_allowed_at,result) VALUES ('jblanked',?,?, '{}')""",
                   (reserved, reserved + timedelta(hours=23)))
    with connect() as db:
        db.execute("""INSERT INTO consensus_provider_checks(
            provider,reserved_at,next_allowed_at,result) VALUES ('jblanked',?,?, '{}')""",
                   (reserved, reserved + timedelta(days=1)))


def test_raw_database_wrapper_uses_native_placeholders_and_preserves_percent_literals():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        db = Database(connection)
        assert db.execute("SELECT ? AS value, '80%' AS label", ("text?%",)).fetchone() == ("text?%", "80%")
    finally:
        connection.close()
