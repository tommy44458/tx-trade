"""Offline regression coverage for importing and backing up personal data."""

import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trade_helper import migrate_postgres
from trade_helper.database_backup import backup_database
from trade_helper.db import connect, database_path, init_db, utc_now
from trade_helper.migrate_postgres import TABLES, import_snapshot


def empty_snapshot():
    return {table: [] for table in TABLES}


def populated_snapshot():
    init_db()
    with connect() as db:
        db.execute("""INSERT INTO analyses (
            id,user_id,idempotency_key,request_hash,request_json,positions_json,
            status,phase,report_json,created_at
        ) VALUES ('analysis-1','owner','request-1','hash','{}','[]',
                  'completed','done','{"reason":"保留報告"}',?)""", (utc_now(),))
        db.execute("""INSERT INTO positions (
            id,user_id,market_id,version,side,leverage,entry_price,quantity,status,
            created_at,updated_at,notes
        ) VALUES ('position-1','owner','binance:perp:BTCUSDT',3,'short',20,
                  '85277.4000','0.00500000','open',?,?,'保留原始持倉')""",
                   (utc_now(), utc_now()))
        db.execute("""INSERT INTO news_documents (
            id,source,source_url,title,body,published_at,market_ids,metadata,content_hash,
            embedding,embedding_model,embedded_at
        ) VALUES ('news-1','fed','https://example.test/fed','利率公告','官方正文',?,
                  '["binance:perp:BTCUSDT"]','{"revision":2}','hash-news',
                  '[0.25,0.75]','fixture-model',?)""", (utc_now(), utc_now()))
        db.execute("""INSERT INTO analysis_embeddings (
            analysis_id,user_id,summary,content_hash,embedding,embedding_model,embedded_at
        ) VALUES ('analysis-1','owner','分析摘要','hash-summary','[0.5,0.5]',
                  'fixture-model',?)""", (utc_now(),))
    with connect(readonly=True) as db:
        snapshot = {table: db.execute(f'SELECT * FROM "{table}"').fetchall() for table in TABLES}
    # Match the structured types returned by PostgreSQL, including pgvector text.
    at = datetime(2026, 10, 1, 8, 30, tzinfo=timezone(timedelta(hours=8)))
    snapshot["positions"][0]["entry_price"] = Decimal("85277.4000")
    snapshot["positions"][0]["quantity"] = Decimal("0.00500000")
    snapshot["news_documents"][0].update({
        "published_at": at,
        "market_ids": ["binance:perp:BTCUSDT"],
        "metadata": {"revision": 2, "note": "中文資料"},
        "embedding": "[0.25,0.75]",
    })
    return snapshot


def test_migration_preserves_personal_reports_json_vectors_decimals_and_archived_tables(tmp_path):
    snapshot = populated_snapshot()
    original_path = database_path()
    before = original_path.read_bytes()
    archived = {"retired_feature": [{"id": "old-1", "payload": {"keep": "歷史資料"}}]}
    target = tmp_path / "export" / "trade helper.sqlite3"
    result = import_snapshot(target, snapshot, archived=archived)
    assert database_path() == original_path
    assert original_path.read_bytes() == before
    assert result["row_counts"]["positions"] == 1
    assert result["archived_tables"] == {"retired_feature": 1}
    assert os.name != "posix" or target.stat().st_mode & 0o777 == 0o600
    with closing(sqlite3.connect(target)) as db:
        assert db.execute("SELECT entry_price,quantity,notes FROM positions").fetchone() == (
            "85277.4000", "0.00500000", "保留原始持倉",
        )
        news = db.execute("SELECT published_at,metadata,embedding FROM news_documents").fetchone()
        assert news[0] == "2026-10-01T00:30:00.000000+00:00"
        assert json.loads(news[1]) == {"revision": 2, "note": "中文資料"}
        assert json.loads(news[2]) == [0.25, 0.75]
        assert json.loads(db.execute("SELECT report_json FROM analyses").fetchone()[0]) == {
            "reason": "保留報告",
        }
        assert db.execute("SELECT count(*) FROM analysis_embeddings").fetchone()[0] == 1
        archived_row = db.execute("SELECT table_name,row_json FROM legacy_postgres_archive").fetchone()
        assert archived_row[0] == "retired_feature"
        assert json.loads(json.loads(archived_row[1])["payload"]) == {"keep": "歷史資料"}
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    manifest = json.loads(target.with_suffix(".migration.json").read_text())
    assert manifest["table_hashes"] == result["table_hashes"]
    assert not list(target.parent.glob(".pg-import-*"))


def test_migration_streams_more_than_one_batch_without_losing_rows(tmp_path):
    snapshot = empty_snapshot()
    snapshot["market_candles_v4"] = ({
        "market": "binance:perp:BTCUSDT", "timeframe": "1h", "open_ms": number,
        "payload": {"close": "85.00", "ordinal": number}, "sha256": str(number),
        "received_at": datetime(2026, 10, 1, tzinfo=UTC),
    } for number in range(2005))
    target = tmp_path / "batch.sqlite3"
    result = import_snapshot(target, snapshot)
    assert result["row_counts"]["market_candles_v4"] == 2005
    with closing(sqlite3.connect(target)) as db:
        assert db.execute("SELECT min(open_ms),max(open_ms),count(*) FROM market_candles_v4").fetchone() == (
            0, 2004, 2005,
        )


@pytest.mark.parametrize("problem", [
    "missing_table", "extra_column", "missing_column", "foreign_key", "lossy_type",
])
def test_failed_migration_publishes_nothing_and_restores_environment(tmp_path, problem):
    snapshot = populated_snapshot()
    if problem == "missing_table":
        del snapshot["zone_states_v4"]
    elif problem == "extra_column":
        snapshot["positions"][0]["unknown_column"] = "must not silently discard"
    elif problem == "missing_column":
        del snapshot["positions"][0]["notes"]
    elif problem == "foreign_key":
        snapshot["analysis_embeddings"][0]["user_id"] = "wrong-owner"
    else:
        # An unexpected numeric value in a TEXT price column must not quietly
        # pass a lossy SQLite coercion: the imported rows' hashes must match.
        snapshot["positions"][0]["entry_price"] = 85277.4
    original_environment = os.environ["APP_DB_PATH"]
    target = tmp_path / "failed.sqlite3"
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        import_snapshot(target, snapshot)
    assert os.environ["APP_DB_PATH"] == original_environment
    assert not target.exists()
    assert not target.with_suffix(".migration.json").exists()
    assert not list(tmp_path.glob(".pg-import-*"))


def test_migration_refuses_existing_target_and_atomic_publish_race(tmp_path, monkeypatch):
    target = tmp_path / "existing.sqlite3"
    target.write_bytes(b"original personal database")
    with pytest.raises(FileExistsError):
        import_snapshot(target, empty_snapshot())
    assert target.read_bytes() == b"original personal database"
    target.unlink()
    real_link = os.link

    def competing_create(source, destination):
        if destination == target:
            target.write_bytes(b"concurrently created personal database")
        return real_link(source, destination)

    monkeypatch.setattr(migrate_postgres.os, "link", competing_create)
    with pytest.raises(FileExistsError):
        import_snapshot(target, empty_snapshot())
    assert target.read_bytes() == b"concurrently created personal database"
    assert not target.with_suffix(".migration.json").exists()
    assert not list(tmp_path.glob(".pg-import-*"))
    assert not list(tmp_path.glob(".pg-manifest-*"))


def test_migration_refuses_existing_manifest_without_publishing_a_database(tmp_path):
    target = tmp_path / "manifest-conflict.sqlite3"
    manifest = target.with_suffix(".migration.json")
    manifest.write_text("original migration record")
    with pytest.raises(FileExistsError):
        import_snapshot(target, empty_snapshot())
    assert manifest.read_text() == "original migration record"
    assert not target.exists()
    assert not list(tmp_path.glob(".pg-import-*"))


def test_migration_cleans_own_manifest_when_database_publication_fails(tmp_path, monkeypatch):
    target = tmp_path / "publish-failure.sqlite3"
    real_link = os.link

    def fail_database_link(source, destination):
        if destination == target:
            raise OSError("simulated publication failure")
        return real_link(source, destination)

    monkeypatch.setattr(migrate_postgres.os, "link", fail_database_link)
    with pytest.raises(OSError, match="publication failure"):
        import_snapshot(target, empty_snapshot())
    assert not target.exists()
    assert not target.with_suffix(".migration.json").exists()
    assert not list(tmp_path.glob(".pg-import-*"))
    assert not list(tmp_path.glob(".pg-manifest-*"))


def test_backup_contains_committed_wal_and_remains_independent_of_later_writes(tmp_path):
    init_db()
    output = tmp_path / "backup" / "snapshot.sqlite3"
    with closing(sqlite3.connect(database_path())) as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE backup_probe (value TEXT PRIMARY KEY)")
        writer.execute("INSERT INTO backup_probe VALUES ('committed in WAL')")
        writer.commit()
        assert database_path().with_name(database_path().name + "-wal").is_file()
        assert backup_database(output) == output
        writer.execute("INSERT INTO backup_probe VALUES ('written after backup')")
        writer.commit()
        with closing(sqlite3.connect(output)) as backed_up:
            assert backed_up.execute("SELECT value FROM backup_probe").fetchall() == [("committed in WAL",)]
            assert backed_up.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert backed_up.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert writer.execute("SELECT count(*) FROM backup_probe").fetchone()[0] == 2
    assert os.name != "posix" or output.stat().st_mode & 0o777 == 0o600
    assert not list(output.parent.glob(".sqlite-backup-*"))


def test_backup_refuses_existing_output_and_does_not_create_a_missing_source(tmp_path):
    output = tmp_path / "retained.sqlite3"
    with pytest.raises(FileNotFoundError):
        backup_database(output)
    assert not database_path().exists()
    init_db()
    output.write_bytes(b"original backup")
    with pytest.raises(FileExistsError):
        backup_database(output)
    assert output.read_bytes() == b"original backup"
