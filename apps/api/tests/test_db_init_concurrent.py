import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from threading import Barrier

from trade_helper.db import SCHEMA_VERSION, connect, init_db


def _initialize_process(_index):
    init_db()
    with connect(readonly=True) as db:
        return db.execute("PRAGMA user_version").fetchone()["user_version"]


def test_concurrent_local_initialization_serializes_migrations(sqlite_db):
    workers = 6
    ready = Barrier(workers)

    def initialize(_index):
        ready.wait(timeout=10)
        init_db()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(initialize, range(workers)))

    with connect(readonly=True) as db:
        columns = db.execute("PRAGMA table_info(positions)").fetchall()
        migrations = db.execute("SELECT version FROM schema_migrations").fetchall()
    assert sum(column["name"] == "previous_stop_loss" for column in columns) == 1
    assert migrations == [{"version": version} for version in range(1, SCHEMA_VERSION + 1)]


def test_separate_processes_can_initialize_the_same_database(sqlite_db):
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=4, mp_context=context) as pool:
        versions = list(pool.map(_initialize_process, range(8)))
    assert versions == [SCHEMA_VERSION] * 8
