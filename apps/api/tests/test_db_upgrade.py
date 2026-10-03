"""Existing SQLite v1 data survives the local preferences/credentials upgrade."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from trade_helper import db as storage

NEW_TABLES = {"app_preferences", "auth_metadata", "credential_local_keys", "credential_records", "credential_legacy_records", "credential_legacy_key_metadata"}


@pytest.fixture
def version_one_database():
    path = storage.database_path()
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(storage._INITIAL_SCHEMA)
        connection.execute("""CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        )""")
        connection.execute("INSERT INTO schema_migrations VALUES (1,'2026-09-30T01:00:00+00:00')")
        connection.execute("PRAGMA user_version=1")
        connection.execute("""INSERT INTO positions (
            id,user_id,market_id,version,side,leverage,margin_mode,entry_price,quantity,
            stop_loss,take_profit,previous_stop_loss,status,created_at,updated_at,
            entry_time,exchange_liquidation_price,notes,source,external_position_id,
            exchange_symbol,contract_type,synced_at
        ) VALUES (
            'existing-position','owner','binance:perp:BTCUSDT',7,'short',20,'isolated',
            '85277.4000','0.12345678','86000','82000','86500','open',
            '2026-09-30T00:00:00+00:00','2026-09-30T01:00:00+00:00',
            '2026-09-29T23:00:00+00:00','88000','既有持倉註記','bingx',
            'external-001','BTC-USDT','standard','2026-09-30T01:00:00+00:00'
        )""")
        connection.execute("""INSERT INTO analyses (
            id,user_id,idempotency_key,request_hash,request_json,positions_json,
            status,phase,report_json,snapshot_json,created_at,started_at,completed_at
        ) VALUES (?,?,?,?,?,?,'completed','done',?,?,?,?,?)""", (
            "existing-analysis", "owner", "existing-idempotency", "existing-request-hash",
            json.dumps({"market_id": "binance:perp:BTCUSDT", "risk_tolerance": "high"}),
            json.dumps([{"id": "existing-position", "version": 7}]),
            json.dumps({"headline": "繼續持倉", "reason": "既有分析理由"}, ensure_ascii=False),
            json.dumps({"current_price": "85277.4000", "timeframe": "1h"}),
            "2026-09-30T01:00:00+00:00", "2026-09-30T01:01:00+00:00",
            "2026-09-30T01:02:00+00:00",
        ))
        connection.row_factory = sqlite3.Row
        return {
            "positions": [dict(row) for row in connection.execute("SELECT * FROM positions")],
            "analyses": [dict(row) for row in connection.execute("SELECT * FROM analyses")],
        }


def _assert_existing_data(snapshot):
    with storage.connect(readonly=True) as database:
        for table in ("positions", "analyses"):
            # Additive migrations may extend a row; all original fields remain
            # unchanged, including the persisted report and snapshot JSON.
            columns = ','.join(snapshot[table][0])
            assert database.execute(f"SELECT {columns} FROM {table}").fetchall() == snapshot[table]
        assert database.execute("SELECT prompt_artifact_id FROM analyses").fetchone() == {
            'prompt_artifact_id': None}
        assert database.execute("PRAGMA user_version").fetchone() == {"user_version": storage.SCHEMA_VERSION}
        assert database.execute("PRAGMA journal_mode").fetchone() == {"journal_mode": "wal"}
        tables = {row["name"] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert NEW_TABLES <= tables
        versions = database.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        assert versions == [{"version": version} for version in range(1, storage.SCHEMA_VERSION + 1)]
        assert database.execute("SELECT applied_at FROM schema_migrations WHERE version=1").fetchone() == {
            "applied_at": "2026-09-30T01:00:00+00:00",
        }


def test_version_one_upgrade_preserves_positions_analyses_and_creates_four_tables(version_one_database):
    storage.init_db()
    _assert_existing_data(version_one_database)
    with storage.connect(readonly=True) as database:
        # Schema installation alone must never import or generate credential
        # material, or require an OS-vault authorization interaction.
        for table in NEW_TABLES:
            assert database.execute(f"SELECT * FROM {table}").fetchall() == []


def test_second_initialization_keeps_new_preferences_and_encrypted_rows(version_one_database):
    storage.init_db()
    with storage.connect() as database:
        database.execute("INSERT INTO app_preferences VALUES (?,?,?)", (
            "owner", json.dumps({"favorite_market_ids": ["binance:perp:BTCUSDT"],
                                  "trading_preferences": {"risk_tolerance": "high", "trading_style": "left"}}),
            "2026-10-01T00:00:00+00:00",
        ))
        database.execute("INSERT INTO auth_metadata VALUES ('codex',?,?)", (
            json.dumps({"authenticated": True, "status_known": True}),
            "2026-10-01T00:00:00+00:00",
        ))
        database.execute("INSERT INTO credential_local_keys VALUES (?,?,?)", (
            "test-master-identity", b"m" * 32, "2026-10-01T00:00:00+00:00"))
        database.execute("INSERT INTO credential_records VALUES (?,?,?,?,2,?)", (
            "bingx", "test-master-identity", b"n" * 12, b"c" * 32, "2026-10-01T00:00:00+00:00",
        ))
    with storage.connect(readonly=True) as database:
        before = {table: database.execute(f"SELECT * FROM {table}").fetchall() for table in NEW_TABLES}
        migration_history = database.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
    storage.init_db()
    _assert_existing_data(version_one_database)
    with storage.connect(readonly=True) as database:
        for table in NEW_TABLES:
            assert database.execute(f"SELECT * FROM {table}").fetchall() == before[table]
        assert database.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall() == migration_history


def test_failed_upgrade_rolls_back_schema_and_version_without_touching_old_data(
    version_one_database, monkeypatch,
):
    original = storage._migration_2
    def interrupted(database):
        database.execute("CREATE TABLE incomplete_upgrade(value TEXT)")
        raise RuntimeError("upgrade interrupted")
    monkeypatch.setattr(storage, "_migration_2", interrupted)
    with pytest.raises(RuntimeError, match="upgrade interrupted"):
        storage.init_db()
    with storage.connect(readonly=True) as database:
        assert database.execute("PRAGMA user_version").fetchone() == {"user_version": 1}
        assert database.execute("SELECT * FROM positions").fetchall() == version_one_database["positions"]
        assert database.execute("SELECT * FROM analyses").fetchall() == version_one_database["analyses"]
        tables = {row["name"] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "incomplete_upgrade" not in tables
        assert not tables.intersection(NEW_TABLES)
    monkeypatch.setattr(storage, "_migration_2", original)
    storage.init_db()
    _assert_existing_data(version_one_database)


def test_simultaneous_upgrade_runs_apply_migration_once(version_one_database):
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _index: storage.init_db(), range(4)))
    _assert_existing_data(version_one_database)


def test_schema_nine_removes_retired_chatgpt_authorization_only():
    storage.init_db()
    preferences = {"model_provider": "chatgpt", "models": {"chatgpt": "gpt", "codex": "kept"},
                   "ui_locale": "en-US"}
    with storage.connect() as database:
        database.execute("INSERT INTO credential_local_keys VALUES ('k',?,'2026-10-01')", (b"k" * 32,))
        for name in ("chatgpt", "openai"):
            database.execute("INSERT INTO credential_records(name,key_id,nonce,ciphertext,updated_at)"
                             " VALUES (?,?,?,?,'2026-10-01')", (name, "k", b"n" * 12, b"c" * 16))
        for provider in ("chatgpt", "codex"):
            database.execute("INSERT INTO auth_metadata VALUES (?,?,'2026-10-01')", (provider, "{}"))
        database.execute("INSERT INTO app_preferences VALUES ('owner',?,'2026-10-01')",
                         (json.dumps(preferences),))
        database.execute("DELETE FROM schema_migrations WHERE version>=9")
        database.execute("PRAGMA user_version=8")
    storage.init_db()
    with storage.connect(readonly=True) as database:
        assert [row["name"] for row in database.execute("SELECT name FROM credential_records")] == ["openai"]
        assert [row["provider"] for row in database.execute("SELECT provider FROM auth_metadata")] == ["codex"]
        saved = json.loads(database.execute("SELECT value_json FROM app_preferences").fetchone()["value_json"])
        assert saved == {"models": {"codex": "kept"}, "ui_locale": "en-US"}
