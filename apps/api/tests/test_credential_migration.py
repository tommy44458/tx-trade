"""Upgrade preserves old external-store ciphertext without touching it."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from trade_helper import credential_store as store
from trade_helper import db as storage
from trade_helper import local_settings
from trade_helper.api import app


@pytest.fixture
def old_credentials(monkeypatch):
    monkeypatch.setenv("APP_DESKTOP", "1")
    monkeypatch.setenv("APP_DESKTOP_TOKEN", "test-desktop-token")
    storage.init_db()
    with storage.connect() as db:
        db.execute("DROP TABLE credential_records")
        db.execute("DROP TABLE credential_local_keys")
        db.execute("ALTER TABLE credential_legacy_records RENAME TO credential_records")
        db.execute("ALTER TABLE credential_legacy_key_metadata RENAME TO credential_key_metadata")
        db.execute("DELETE FROM schema_migrations WHERE version>=5")
        db.execute("PRAGMA user_version=4")
        db.execute("INSERT INTO credential_key_metadata VALUES (1,'retired-master-id')")
        for name in ("bingx", "jev", "chatgpt", "openai", "jblanked"):
            db.execute("INSERT INTO credential_records VALUES (?,?,?,?,1,?)",
                       (name, "retired-master-id", b"n" * 12, b"old-ciphertext" * 3, "2026-10-01T00:00:00Z"))
        db.execute("INSERT INTO app_preferences VALUES ('local-demo',?,?)", (
            json.dumps({"integration_status": {"bingx": {"configured": True}},
                        "favorite_market_ids": ["binance:perp:BTCUSDT"],
                        "trading_preferences": {"risk_tolerance": "high", "leverage": 40}}),
            "2026-10-01T00:00:00Z"))
        db.execute("INSERT INTO auth_metadata VALUES ('chatgpt',?,?)", ('{"authenticated":true}', "2026-10-01"))
        before = db.execute("SELECT * FROM credential_records ORDER BY name").fetchall()
    return before


def test_v5_preserves_old_rows_and_requires_reentry(old_credentials):
    storage.init_db()
    with storage.connect(readonly=True) as db:
        assert db.execute("SELECT * FROM credential_legacy_records ORDER BY name").fetchall() == old_credentials
        assert db.execute("SELECT key_id FROM credential_legacy_key_metadata").fetchone()["key_id"] == "retired-master-id"
        assert db.execute("SELECT * FROM credential_local_keys").fetchall() == []
        assert db.execute("SELECT * FROM credential_records").fetchall() == []
        assert db.execute("SELECT * FROM auth_metadata WHERE provider='chatgpt'").fetchall() == []
    for name in ("bingx", "jev", "openai", "jblanked"):
        assert not store.credential_status(name)
        assert store.credential_needs_reentry(name)
        assert local_settings.integration_status(name)["needs_reentry"]
        assert local_settings.integration_credentials(name, allow_interaction=True) is None
    assert local_settings.favorite_market_ids() == ["binance:perp:BTCUSDT"]
    assert local_settings.trading_preferences()["risk_tolerance"] == "high"


def test_reenter_updates_active_connection_and_keeps_archived_copy(old_credentials):
    response = TestClient(app, headers={"Authorization": "Bearer test-desktop-token"}).put('/api/v1/settings', json={
        'bingx_api_key': 'test-new-api-key', 'bingx_api_secret': 'test-new-secret'})
    assert response.status_code == 200
    assert response.json()['integrations']['bingx'] == {'configured': True}
    assert 'test-new-secret' not in response.text
    assert store.load_credentials('bingx')['api_secret'] == 'test-new-secret'
    with storage.connect(readonly=True) as db:
        assert db.execute("SELECT * FROM credential_legacy_records ORDER BY name").fetchall() == old_credentials
    local_settings._INITIALIZED_PREFERENCES.clear()
    assert local_settings.integration_credentials('bingx')['api_secret'] == 'test-new-secret'


def test_explicit_clear_removes_only_selected_active_and_archive(old_credentials):
    storage.init_db()
    store.save_credentials('jev', {'api_key': 'test-key'})
    response = TestClient(app, headers={"Authorization": "Bearer test-desktop-token"}).put('/api/v1/settings', json={'clear_jev': True})
    assert response.status_code == 200
    assert response.json()['integrations']['jev'] == {'configured': False}
    assert not store.credential_needs_reentry('jev')
    assert store.load_credentials('jev') is None
    with storage.connect(readonly=True) as db:
        assert db.execute("SELECT * FROM credential_legacy_records ORDER BY name").fetchall() == [
            row for row in old_credentials if row['name'] != 'jev']


def test_retired_import_route_never_opens_any_external_store(old_credentials):
    response = TestClient(app, headers={"Authorization": "Bearer test-desktop-token"}).post('/api/v1/settings/migrate-credentials', json={'names': ['bingx']})
    assert response.status_code == 410
    assert response.json()['detail']['code'] == 'CREDENTIAL_REENTRY_REQUIRED'
    storage.init_db()
    with storage.connect(readonly=True) as db:
        assert db.execute("SELECT * FROM credential_legacy_records ORDER BY name").fetchall() == old_credentials
        assert db.execute("SELECT * FROM credential_records").fetchall() == []


def test_concurrent_old_schema_upgrade_only_archives_once(old_credentials):
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: storage.init_db(), range(4)))
    with storage.connect(readonly=True) as db:
        assert db.execute("SELECT * FROM credential_legacy_records ORDER BY name").fetchall() == old_credentials
        assert len(db.execute("SELECT * FROM schema_migrations WHERE version=5").fetchall()) == 1


def test_failed_v5_upgrade_rolls_back_and_preserves_active_old_rows(old_credentials, monkeypatch):
    original = storage._migration_5
    def interrupted(db):
        db.execute("ALTER TABLE credential_records RENAME TO partial_archive")
        raise RuntimeError('migration interrupted')
    monkeypatch.setattr(storage, '_migration_5', interrupted)
    with pytest.raises(RuntimeError, match='migration interrupted'):
        storage.init_db()
    with storage.connect(readonly=True) as db:
        assert db.execute('PRAGMA user_version').fetchone()['user_version'] == 4
        assert db.execute('SELECT * FROM credential_records ORDER BY name').fetchall() == old_credentials
    monkeypatch.setattr(storage, '_migration_5', original)
    storage.init_db()
