"""Consistent SQLite backup, including committed data still residing in WAL."""

import argparse
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .db import database_path


def backup_database(output: Path) -> Path:
    source = database_path()
    output = Path(output).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("本地資料庫尚未建立")
    if output.exists():
        raise FileExistsError("備份檔案已存在；不會覆蓋既有檔案")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".sqlite-backup-", dir=output.parent)
    os.close(descriptor)
    try:
        with (closing(sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)) as original,
              closing(sqlite3.connect(temporary)) as target):
            original.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("備份完整性檢查失敗")
        with open(temporary, "rb") as completed:
            os.fsync(completed.fileno())
        os.link(temporary, output)
        return output
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="建立本地 SQLite 的一致性備份")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = backup_database(args.output)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        raise SystemExit(f"備份失敗（{type(exc).__name__}）；既有資料未修改。") from None
    print(f"備份完成：{output}")


if __name__ == "__main__":
    main()
