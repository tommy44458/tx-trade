import json

import pytest
from fastapi.testclient import TestClient

from trade_helper import credential_store, local_settings
from trade_helper.api import app
from trade_helper.db import connect, database_path


def save_keys(client, suffix="one"):
    return client.patch("/api/v1/settings", json={
        "binance_api_key": f"private-binance-key-{suffix}",
        "binance_api_secret": f"private-binance-secret-{suffix}",
    })


def test_binance_credentials_are_encrypted_and_public_settings_are_nonsecret():
    client = TestClient(app)
    client.patch("/api/v1/settings", json={"ui_theme": "dark", "ui_locale": "en-US",
        "initial_indicators": ["obv"], "favorite_market_ids": ["binance:perp:SOLUSDT"]})
    response = save_keys(client)
    assert response.status_code == 200
    assert response.json()["integrations"]["binance"] == {"configured": True}
    assert "private-binance" not in response.text
    assert "account_scope" not in response.text
    saved = credential_store.load_credentials("binance")
    assert saved["api_key"] == "private-binance-key-one"
    assert saved["api_secret"] == "private-binance-secret-one"
    assert len(saved["account_scope"]) == 32
    assert local_settings.preferences()["binance_account_scope"] == saved["account_scope"]
    assert "private-binance" not in json.dumps(local_settings.preferences())
    for path in [database_path(), database_path().with_name(database_path().name + "-wal")]:
        if path.exists():
            assert b"private-binance" not in path.read_bytes()
    assert response.json()["ui_theme"] == "dark"
    assert response.json()["ui_locale"] == "en-US"
    assert response.json()["initial_indicators"] == ["obv"]
    assert response.json()["favorite_market_ids"] == ["binance:perp:SOLUSDT"]


@pytest.mark.parametrize("patch", [
    {}, {"binance_api_key": "", "binance_api_secret": ""},
    {"binance_api_key": None, "binance_api_secret": None},
    {"binance_api_key": "  ", "binance_api_secret": "  "},
])
def test_omitted_or_blank_keys_preserve_connection_and_account_scope(patch):
    client = TestClient(app)
    save_keys(client)
    frozen, scope = local_settings.binance_sync_credentials()
    assert client.patch("/api/v1/settings", json=patch).status_code == 200
    with connect() as db:
        assert local_settings.binance_connection_is_current(db, frozen, scope)
    assert local_settings.binance_sync_credentials() == (frozen, scope)


def test_replacement_or_removal_invalidates_frozen_connection_and_preserves_other_records():
    client = TestClient(app)
    client.patch("/api/v1/settings", json={"bingx_api_key": "other-key", "bingx_api_secret": "other-secret"})
    original_bingx = credential_store.load_credentials_with_revision("bingx")
    save_keys(client)
    frozen, scope = local_settings.binance_sync_credentials()
    with connect() as db:
        assert local_settings.binance_connection_is_current(db, frozen, scope)
    save_keys(client, "two")
    replacement, next_scope = local_settings.binance_sync_credentials()
    assert scope != next_scope
    with connect() as db:
        assert not local_settings.binance_connection_is_current(db, frozen, scope)
        assert local_settings.binance_connection_is_current(db, replacement, next_scope)
    assert client.patch("/api/v1/settings", json={"clear_binance": True}).status_code == 200
    with connect() as db:
        assert not local_settings.binance_connection_is_current(db, replacement, next_scope)
    assert "binance_account_scope" not in local_settings.preferences()
    assert credential_store.load_credentials("binance") is None
    assert credential_store.load_credentials_with_revision("bingx") == original_bingx


def test_revision_check_blocks_an_inflight_sync_before_scope_metadata_has_changed():
    client = TestClient(app)
    save_keys(client)
    frozen, scope = local_settings.binance_sync_credentials()
    credential_store.save_credentials("binance", {
        "api_key": "new-key", "api_secret": "new-secret", "account_scope": "f" * 32,
    })
    assert local_settings.preferences()["binance_account_scope"] == scope
    with connect() as db:
        assert not local_settings.binance_connection_is_current(db, frozen, scope)
    with pytest.raises(credential_store.CredentialStoreError, match="連線已變更"):
        local_settings.binance_sync_credentials()


@pytest.mark.parametrize("patch", [
    {"binance_api_key": "do-not-echo-key"},
    {"binance_api_secret": "do-not-echo-secret"},
    {"binance_api_key": "do-not-echo-key", "binance_api_secret": " "},
    {"binance_api_key": "do-not-echo-key", "binance_api_secret": "do-not-echo-secret", "clear_binance": True},
    {"binance_api_key": "do-not-echo-key" * 500, "binance_api_secret": "do-not-echo-secret"},
])
def test_key_validation_never_echoes_secret_or_changes_saved_keys(patch):
    client = TestClient(app)
    save_keys(client)
    frozen = local_settings.binance_sync_credentials()
    response = client.patch("/api/v1/settings", json=patch)
    assert response.status_code == 422
    assert "do-not-echo" not in response.text
    assert all(set(error) <= {"loc", "type", "msg"} for error in response.json()["detail"])
    assert local_settings.binance_sync_credentials() == frozen


def test_viewing_settings_and_changing_theme_never_decrypts_binance_keys(monkeypatch):
    client = TestClient(app)
    save_keys(client)
    monkeypatch.setattr(local_settings, "load_credentials", lambda *_args, **_kwargs: pytest.fail("Unexpected key read"))
    monkeypatch.setattr(local_settings, "load_credentials_with_revision", lambda *_args, **_kwargs: pytest.fail("Unexpected key read"))
    assert client.get("/api/v1/settings").json()["integrations"]["binance"]["configured"]
    assert client.patch("/api/v1/settings", json={"ui_theme": "light"}).status_code == 200


@pytest.mark.parametrize("value", ["bad\x00key", "bad\x1bkey", "bad key", "bad\nkey", "私密金鑰", "\ud800"])
@pytest.mark.parametrize("field", ["binance_api_key", "binance_api_secret"])
def test_invalid_key_characters_are_safe_422_before_encryption(field, value):
    client = TestClient(app)
    save_keys(client)
    frozen = local_settings.binance_sync_credentials()
    payload = {"binance_api_key": "valid-test-key", "binance_api_secret": "valid-test-secret", field: value}
    response = client.patch("/api/v1/settings", content=json.dumps(payload),
                            headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert "valid-test" not in response.text
    assert "私密金鑰" not in response.text
    assert local_settings.binance_sync_credentials() == frozen


def test_binance_is_explicitly_opt_in_and_never_inherits_env(monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "env-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "env-secret")
    for desktop in ["0", "1"]:
        monkeypatch.setenv("APP_DESKTOP", desktop)
        assert local_settings.public_settings()["integrations"]["binance"] == {"configured": False}
        assert local_settings.integration_credentials("binance") is None
    with pytest.raises(credential_store.CredentialStoreError, match="先在設定"):
        local_settings.binance_sync_credentials()


def test_legacy_metadata_does_not_import_misplaced_binance_secrets():
    cleaned = local_settings._nonsecret_object({
        "BINANCE_API_KEY": "private-key", "binance_api_secret": "private-secret",
        "nested": {"binanceSecretKey": "private-secret", "keep": True}, "ui_theme": "dark",
    })
    assert cleaned == {"nested": {"keep": True}, "ui_theme": "dark"}
