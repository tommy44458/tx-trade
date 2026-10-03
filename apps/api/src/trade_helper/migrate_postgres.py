"""One-time, read-only PostgreSQL export into a verified embedded database.

PostgreSQL support is an optional migration dependency, never a desktop runtime
requirement. Connection credentials are supplied through the environment only.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .db import SCHEMA_VERSION, init_db, utc_now
from .storage_codec import dump_json, utc_text

TABLES = (
    "analyses", "positions", "market_candles_v4", "zone_states_v4", "zone_events_v4",
    "news_documents", "news_classifications", "news_body_fetches",
    "news_classification_jobs", "news_jev_calls", "news_source_checks",
    "analysis_embeddings", "event_sources", "economic_events", "economic_event_versions",
    "macro_actual_versions", "macro_actual_source_checks", "consensus_provider_checks",
)
TIME_COLUMNS = {
    "published_at", "ingested_at", "embedded_at", "classified_at", "checked_at",
    "next_check_at", "next_attempt_at", "lease_until", "updated_at", "created_at",
    "started_at", "finished_at", "completed_at", "last_success_at", "reserved_at",
    "next_allowed_at", "v4_lease_until", "entry_time", "synced_at", "received_at",
}


@contextmanager
def _temporary_database(path: Path):
    previous = os.environ.get("APP_DB_PATH")
    os.environ["APP_DB_PATH"] = str(path)
    try:
        init_db()
        yield
    finally:
        if previous is None:
            os.environ.pop("APP_DB_PATH", None)
        else:
            os.environ["APP_DB_PATH"] = previous


def normalized_value(column: str, value):
    if isinstance(value, datetime):
        return utc_text(value)
    if isinstance(value, str) and column in TIME_COLUMNS:
        return utc_text(datetime.fromisoformat(value))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return dump_json(value)
    # pgvector returns its printable array when no adapter is installed.
    if column == "embedding" and value is not None:
        return dump_json(json.loads(value))
    return value


def _row_hash(values) -> str:
    return hashlib.sha256(dump_json(values).encode()).hexdigest()


def _digest(hashes: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(hashes)).encode()).hexdigest()


def import_snapshot(target: Path, rows_by_table: dict, *, archived: dict | None = None) -> dict:
    """Import iterables atomically; also used by offline migration regression tests."""
    target = Path(target).expanduser().resolve()
    manifest = target.with_suffix(".migration.json")
    if target.exists() or manifest.exists():
        raise FileExistsError("目的資料庫已存在；不會覆蓋既有本地資料。")
    missing = set(TABLES) - set(rows_by_table)
    if missing:
        raise ValueError("來源缺少必要資料表；未建立新的資料庫。")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pg-import-", suffix=".sqlite3", dir=target.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    counts, hashes, archived_counts = {}, {}, {}
    try:
        with _temporary_database(temporary), closing(sqlite3.connect(temporary)) as destination:
            destination.execute("PRAGMA foreign_keys=ON")
            destination.execute("PRAGMA synchronous=FULL")
            destination.execute("BEGIN IMMEDIATE")
            for table in TABLES:
                columns = [row[1] for row in destination.execute(f'PRAGMA table_info("{table}")')]
                names = ",".join(f'"{column}"' for column in columns)
                expected = []
                statement = f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in columns)})'
                batch = []
                for row in rows_by_table[table]:
                    if set(row) != set(columns):
                        raise ValueError(f"{table} 欄位與本地結構不同；未發佈新的資料庫。")
                    values = tuple(normalized_value(column, row[column]) for column in columns)
                    batch.append(values)
                    expected.append(_row_hash(values))
                    if len(batch) == 1000:
                        destination.executemany(statement, batch)
                        batch.clear()
                if batch:
                    destination.executemany(statement, batch)
                actual = [_row_hash(tuple(row)) for row in destination.execute(f'SELECT {names} FROM "{table}"')]
                if len(actual) != len(expected) or _digest(actual) != _digest(expected):
                    raise ValueError(f"{table} 資料比對失敗；未發佈新的資料庫。")
                counts[table], hashes[table] = len(actual), _digest(actual)
            if archived:
                destination.execute("""CREATE TABLE legacy_postgres_archive (
                    table_name TEXT NOT NULL, ordinal INTEGER NOT NULL,
                    row_json TEXT NOT NULL CHECK(json_valid(row_json)),
                    PRIMARY KEY(table_name,ordinal)
                )""")
                for table, rows in archived.items():
                    count = 0
                    for count, row in enumerate(rows, 1):
                        value = {key: normalized_value(key, value) for key, value in row.items()}
                        destination.execute("INSERT INTO legacy_postgres_archive VALUES (?,?,?)",
                                            (table, count, dump_json(value)))
                    archived_counts[table] = count
            if destination.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("資料關聯檢查失敗；未發佈新的資料庫。")
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("資料庫完整性檢查失敗；未發佈新的資料庫。")
            destination.commit()
            checkpoint = destination.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint[0]:
                raise RuntimeError("資料庫尚有未完成的寫入；未發佈新的資料庫。")
        # Windows flushes only a handle opened for writing; read-only fails with EBADF.
        with temporary.open("r+b") as database_file:
            os.fsync(database_file.fileno())
        result = {"status": "migrated", "source": "postgresql", "database": str(target),
                  "schema_version": SCHEMA_VERSION, "migrated_at": utc_now(),
                  "row_counts": counts, "table_hashes": hashes, "archived_tables": archived_counts}
        report_descriptor, report_temporary = tempfile.mkstemp(prefix=".pg-manifest-", dir=target.parent)
        try:
            with os.fdopen(report_descriptor, "w", encoding="utf-8") as output:
                output.write(dump_json(result))
                output.flush()
                os.fsync(output.fileno())
            os.link(report_temporary, manifest)
            try:
                # A concurrent creator cannot be overwritten between exists() and publish.
                os.link(temporary, target)
            except BaseException:
                if manifest.exists() and os.path.samefile(report_temporary, manifest):
                    manifest.unlink()
                raise
            if os.name == "posix":
                directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            Path(report_temporary).unlink(missing_ok=True)
        return result
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(str(temporary) + suffix).unlink(missing_ok=True)


def migrate(source_url: str, target: Path, *, schema: str = "public") -> dict:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ValueError("來源 schema 格式不正確")
    # Import only when this explicitly invoked migration command is used.
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    with psycopg.connect(source_url, options="-c default_transaction_read_only=on",
                         row_factory=dict_row) as source:
        source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        available = {row["tablename"] for row in source.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname=%s", (schema,)).fetchall()}
        if not set(TABLES) <= available:
            raise ValueError("來源缺少必要資料表；請確認既有資料庫，未修改任何資料。")
        def rows(table):
            with source.cursor(name="migration_" + table) as cursor:
                cursor.itersize = 1000
                cursor.execute(sql.SQL("SELECT * FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table)))
                yield from cursor
        return import_snapshot(target, {table: rows(table) for table in TABLES},
                               archived={table: rows(table) for table in sorted(available - set(TABLES))})


def main():
    parser = argparse.ArgumentParser(description="唯讀搬移既有 PostgreSQL 至本地 SQLite；不覆蓋現有檔案")
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--schema", default="public")
    args = parser.parse_args()
    source = os.getenv("MIGRATION_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not source:
        parser.error("請透過 MIGRATION_DATABASE_URL 或既有 .env 的 DATABASE_URL 提供來源連線")
    try:
        result = migrate(source, args.target, schema=args.schema)
    except Exception as exc:  # noqa: BLE001 - never print connection credentials or personal rows
        raise SystemExit(f"資料搬移未完成（{type(exc).__name__}）；原 PostgreSQL 資料保留，目的檔案不會被覆蓋。") from None
    print(dump_json(result))


if __name__ == "__main__":
    main()
