"""One-time, repeatable import. The SQLite source is opened read-only and retained."""

import argparse
import sqlite3
from pathlib import Path

from psycopg import sql

from trade_helper.db import connect, init_db

TABLES = (
    "positions", "analyses", "market_candles_v4", "zone_states_v4", "zone_events_v4",
)


def migrate(source: Path) -> dict[str, int]:
    if not source.is_file():
        raise FileNotFoundError(source)
    init_db()
    counts = {}
    with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as old:
        old.row_factory = sqlite3.Row
        for table in TABLES:
            exists = old.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                counts[table] = 0
                continue
            columns = [row["name"] for row in old.execute(f"PRAGMA table_info({table})")]
            query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.SQL(",").join(sql.Placeholder() for _ in columns),
            )
            total = 0
            with connect() as db:
                cursor = db.connection.cursor()
                source_rows = old.execute(f"SELECT * FROM {table}")
                while batch := source_rows.fetchmany(1000):
                    cursor.executemany(query, [tuple(row) for row in batch])
                    total += len(batch)
                db.commit()
            counts[table] = total
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    for name, count in migrate(args.source).items():
        print(f"{name}: {count} source rows processed")
