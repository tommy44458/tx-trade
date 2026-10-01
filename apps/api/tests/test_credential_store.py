"""All app credentials, encryption metadata and concurrency live in SQLite."""

import builtins
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from trade_helper import credential_store as store
from trade_helper.db import connect, database_path


def test_round_trip_delete_and_restart_need_only_sqlite():
    assert store.load_credentials("bingx") is None
    store.save_credentials("bingx", {"api_key": "private-test-key", "api_secret": "private-test-secret"})
    store.save_credentials("jev", {"api_key": "private-jev-test-key"})
    assert store.load_credentials("bingx", allow_interaction=True)["api_secret"] == "private-test-secret"
    assert store.load_credentials("jev")["api_key"] == "private-jev-test-key"
    with connect(readonly=True) as db:
        assert len(db.execute("SELECT * FROM credential_local_keys").fetchall()) == 1
    store.delete_credentials("bingx")
    store.delete_credentials("bingx")
    assert store.load_credentials("bingx") is None
    assert not store.credential_status("bingx")
    assert store.credential_status("jev")


def test_raw_api_secrets_are_not_written_to_database_or_wal():
    key, secret = "dummy-key" * 4, "dummy-secret" * 4
    store.save_credentials("bingx", {"api_key": key, "api_secret": secret})
    with connect(readonly=True) as db:
        db.execute("SELECT * FROM credential_records").fetchall()
        store.save_credentials("bingx", {"api_key": key, "api_secret": secret, "revision": 2})
        paths = [database_path(), database_path().with_name(database_path().name + "-wal")]
        for path in paths:
            assert key.encode() not in path.read_bytes()
            assert secret.encode() not in path.read_bytes()
        row = db.execute("SELECT * FROM credential_records").fetchone()
        assert isinstance(row["ciphertext"], bytes) and len(row["nonce"]) == 12
        assert row["version"] == 2


def test_local_master_is_in_sqlite_and_complete_backup_can_restore(tmp_path, monkeypatch):
    store.save_credentials("chatgpt", {"access_token": "private-test-token"})
    backup = tmp_path / "restored" / "backup.sqlite3"
    backup.parent.mkdir()
    with sqlite3.connect(database_path()) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    monkeypatch.setenv("APP_DB_PATH", str(backup))
    assert store.load_credentials("chatgpt") == {"access_token": "private-test-token"}
    with connect(readonly=True) as db:
        assert len(db.execute("SELECT master_key FROM credential_local_keys").fetchone()["master_key"]) == 32


def test_metadata_and_missing_records_never_decrypt(monkeypatch):
    store.save_credentials("bingx", {"api_key": "private-key"})
    monkeypatch.setattr(store, "AESGCM", lambda *_: pytest.fail("metadata must not decrypt"))
    assert store.credential_status("bingx")
    assert not store.credential_status("jev")
    assert not store.credential_needs_reentry("bingx")
    assert store.load_credentials("jev") is None
    store.delete_credentials("bingx")
    assert store.load_credentials("bingx") is None


def test_missing_local_master_never_regenerates_over_existing_records():
    store.save_credentials("bingx", {"api_key": "private-key"})
    with sqlite3.connect(database_path()) as db:
        original = db.execute("SELECT * FROM credential_records").fetchone()
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("DELETE FROM credential_local_keys")
    with pytest.raises(store.CredentialStoreError, match="不會被覆寫"):
        store.load_credentials("bingx")
    with pytest.raises(store.CredentialStoreError, match="不會被覆寫"):
        store.save_credentials("jev", {"api_key": "another-key"})
    with sqlite3.connect(database_path()) as db:
        assert db.execute("SELECT * FROM credential_records").fetchone() == original
        assert db.execute("SELECT * FROM credential_local_keys").fetchall() == []


@pytest.mark.parametrize("change", ["ciphertext", "nonce", "name", "version", "master_key"])
def test_tampering_is_rejected(change):
    store.save_credentials("bingx", {"api_key": "private-key"})
    with connect() as db:
        row = db.execute("SELECT * FROM credential_records").fetchone()
        if change == "name":
            db.execute("UPDATE credential_records SET name='jev'")
        elif change == "version":
            db.execute("PRAGMA ignore_check_constraints=ON")
            db.execute("UPDATE credential_records SET version=99")
        elif change == "master_key":
            db.execute("UPDATE credential_local_keys SET master_key=?", (b"m" * 32,))
        else:
            value = row[change]
            db.execute(f"UPDATE credential_records SET {change}=?", (bytes([value[0] ^ 1]) + value[1:],))
    with pytest.raises(store.CredentialStoreError):
        store.load_credentials("jev" if change == "name" else "bingx")


def test_each_update_uses_a_fresh_nonce():
    store.save_credentials("bingx", {"api_key": "private-key"})
    with connect(readonly=True) as db:
        original = db.execute("SELECT nonce,ciphertext FROM credential_records").fetchone()
    store.save_credentials("bingx", {"api_key": "private-key"})
    with connect(readonly=True) as db:
        current = db.execute("SELECT nonce,ciphertext FROM credential_records").fetchone()
    assert current["nonce"] != original["nonce"]
    assert current["ciphertext"] != original["ciphertext"]


def test_concurrent_threads_keep_both_credentials_and_one_master():
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda name: store.save_credentials(name, {"api_key": name + "-key"}), ["bingx", "jev"]))
    with connect(readonly=True) as db:
        assert len(db.execute("SELECT * FROM credential_local_keys").fetchall()) == 1
    assert store.load_credentials("bingx")["api_key"] == "bingx-key"
    assert store.load_credentials("jev")["api_key"] == "jev-key"


def test_concurrent_processes_keep_one_master():
    script = """
import sys
from trade_helper import credential_store as store
store.save_credentials(sys.argv[1], {'api_key': sys.argv[1] + '-key'})
assert store.load_credentials(sys.argv[1])['api_key'] == sys.argv[1] + '-key'
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(store.__file__).resolve().parents[1])
    processes = [subprocess.Popen([sys.executable, "-c", script, name], env=environment,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for name in ["bingx", "jev"]]
    for process in processes:
        _out, error = process.communicate(timeout=30)
        assert process.returncode == 0, error
    with connect(readonly=True) as db:
        rows = db.execute("SELECT name,key_id FROM credential_records ORDER BY name").fetchall()
        assert [row["name"] for row in rows] == ["bingx", "jev"]
        assert rows[0]["key_id"] == rows[1]["key_id"]
        assert len(db.execute("SELECT * FROM credential_local_keys").fetchall()) == 1


def test_save_without_keyring_installed_and_no_system_command(monkeypatch):
    original = builtins.__import__
    def blocked(name, *args, **kwargs):
        assert not name.startswith(("keyring", "keyrings")), "OS store must not be imported"
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", blocked)
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("credential storage must not run commands"))
    store.save_credentials("bingx", {"api_key": "private-key"}, allow_interaction=True)
    assert store.load_credentials("bingx", allow_interaction=True) == {"api_key": "private-key"}


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_database_and_sidecars_are_private():
    store.save_credentials("jev", {"api_key": "private-key"})
    database_path().chmod(0o644)
    assert store.load_credentials("jev")
    assert database_path().stat().st_mode & 0o777 == 0o600


def test_storage_error_does_not_reveal_secret(monkeypatch):
    def failed():
        raise RuntimeError("private-key must not be exposed")
    monkeypatch.setattr(store, "_private_database", failed)
    with pytest.raises(store.CredentialStoreError) as error:
        store.save_credentials("jev", {"api_key": "private-key"})
    assert "private-key" not in str(error.value)


def test_revision_compare_prevents_stale_overwrite_and_logout_resurrection():
    first = {"auth": {"tokens": {"access_token": "test-first-token"}}}
    second = {"auth": {"tokens": {"access_token": "test-new-token"}}}
    store.save_credentials("codex", first)
    value, revision = store.load_credentials_with_revision("codex")
    assert value == first
    store.save_credentials("codex", second)
    assert store.save_credentials("codex", first, expected_revision=revision) is False
    assert store.delete_credentials("codex", expected_revision=revision) is False
    assert store.load_credentials("codex") == second
    _, newer = store.load_credentials_with_revision("codex")
    store.delete_credentials("codex")
    assert store.save_credentials("codex", second, expected_revision=newer) is False
    assert store.load_credentials("codex") is None
