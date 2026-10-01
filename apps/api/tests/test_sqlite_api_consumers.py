"""Model/calendar consumers read encrypted SQLite keys, with no desktop fallback."""

import json
from types import SimpleNamespace

import httpx
import pytest

from trade_helper import consensus_probe, credential_store, local_settings, model_providers
from trade_helper.db import connect, database_path


@pytest.fixture
def desktop_sqlite_keys(monkeypatch):
    monkeypatch.setenv("APP_DESKTOP", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-openai-private-key")
    monkeypatch.setenv("JBLANKED_API_KEY", "environment-jblanked-private-key")
    monkeypatch.setenv("OPENAI_MODEL", "ignored-environment-model")
    local_settings.patch_preferences({"model_provider": "openai", "models": {"openai": "sqlite-selected-model"}})


def _save_key(name, key):
    local_settings.update_settings(local_settings.SettingsUpdate(**{name + "_api_key": key}))


def _block_network(monkeypatch):
    monkeypatch.setattr(consensus_probe.httpx, "get", lambda *_args, **_kwargs: pytest.fail("no calendar request expected"))


def test_openai_factory_receives_decrypted_sqlite_key_and_selected_model(desktop_sqlite_keys):
    _save_key("openai", "sqlite-openai-private-key")
    created, closed = [], []
    def factory(**options):
        created.append(options)
        return SimpleNamespace(close=lambda: closed.append(True))
    session = model_providers.ModelSession(openai_factory=factory)
    try:
        assert session.provider == "openai"
        assert session.model == "sqlite-selected-model"
        assert len(created) == 1 and created[0]["api_key"] == "sqlite-openai-private-key"
    finally:
        session.close()
    assert closed == [True]
    assert b"sqlite-openai-private-key" not in database_path().read_bytes()


def test_calendar_request_uses_sqlite_key_and_cache_does_not_save_secret(desktop_sqlite_keys, monkeypatch):
    _save_key("jblanked", "sqlite-jblanked-private-key")
    requests = []
    def calendar(url, **options):
        requests.append((url, options))
        return httpx.Response(200, json=[])
    monkeypatch.setattr(consensus_probe.httpx, "get", calendar)
    result = consensus_probe.probe_jblanked_cached()
    assert result["status"] == "checked" and result["from_cache"] is False
    assert len(requests) == 1
    assert requests[0][0] == consensus_probe.CALENDAR_URL
    assert requests[0][1]["headers"]["Authorization"] == "Api-Key sqlite-jblanked-private-key"
    assert "private-key" not in json.dumps(result)
    cached = consensus_probe.probe_jblanked_cached()
    assert cached["from_cache"] is True and cached["status"] == "checked"
    assert len(requests) == 1
    with connect(readonly=True) as database:
        rows = database.execute("SELECT result FROM consensus_provider_checks").fetchall()
        assert len(rows) == 1
        assert "private-key" not in rows[0]["result"]
    assert b"sqlite-jblanked-private-key" not in database_path().read_bytes()


@pytest.mark.parametrize("name", ["openai", "jblanked"])
def test_missing_sqlite_key_never_uses_environment_or_legacy_vault(desktop_sqlite_keys, monkeypatch, name):
    _block_network(monkeypatch)
    if name == "openai":
        with pytest.raises(RuntimeError) as error:
            model_providers.ModelSession(openai_factory=lambda **_options: pytest.fail("missing key must not create SDK client"))
        assert "private-key" not in str(error.value)
    else:
        assert consensus_probe.probe_jblanked()["status"] == "missing_key"
        assert consensus_probe.probe_jblanked_cached()["status"] == "missing_key"
        with connect(readonly=True) as database:
            assert database.execute("SELECT * FROM consensus_provider_checks").fetchall() == []


@pytest.mark.parametrize("name", ["openai", "jblanked"])
def test_clear_sqlite_key_blocks_environment_and_legacy_fallback(desktop_sqlite_keys, monkeypatch, name):
    _save_key(name, "sqlite-" + name + "-private-key")
    _block_network(monkeypatch)
    local_settings.update_settings(local_settings.SettingsUpdate(**{"clear_" + name: True}))
    local_settings._INITIALIZED_PREFERENCES.clear()
    assert local_settings.integration_status(name) == {"configured": False}
    assert local_settings.integration_credentials(name) is None
    assert credential_store.load_credentials(name) is None
    if name == "openai":
        with pytest.raises(RuntimeError):
            model_providers.ModelSession(openai_factory=lambda **_options: pytest.fail("cleared key must not create SDK client"))
    else:
        assert consensus_probe.probe_jblanked_cached()["status"] == "missing_key"


@pytest.mark.parametrize("name", ["openai", "jblanked"])
def test_corrupt_local_key_fails_before_any_remote_request(desktop_sqlite_keys, monkeypatch, name):
    _save_key(name, "sqlite-" + name + "-private-key")
    _block_network(monkeypatch)
    with connect() as db:
        db.execute("UPDATE credential_local_keys SET master_key=?", (b"x" * 32,))
    if name == "openai":
        with pytest.raises(credential_store.CredentialStoreError):
            model_providers.ModelSession(openai_factory=lambda **_options: pytest.fail("must not create SDK client"))
    else:
        result = consensus_probe.probe_jblanked_cached()
        assert result["status"] == "unavailable" and result["error_code"] == "CREDENTIALS_UNAVAILABLE"
        with connect(readonly=True) as database:
            assert database.execute("SELECT * FROM consensus_provider_checks").fetchall() == []
    assert local_settings.integration_status(name) == {"configured": True}
    assert credential_store.credential_status(name) is True


@pytest.mark.parametrize("key", [None, 123, [], {}])
def test_malformed_legacy_calendar_key_does_not_consume_a_request(monkeypatch, key):
    monkeypatch.setattr(consensus_probe, "integration_credentials", lambda _name: {"api_key": key})
    _block_network(monkeypatch)
    assert consensus_probe.probe_jblanked_cached()["status"] == "missing_key"


def test_bingx_sync_reads_sqlite_only_after_restart_without_system_interaction(desktop_sqlite_keys, monkeypatch):
    from trade_helper import bingx

    local_settings.update_settings(local_settings.SettingsUpdate(
        bingx_api_key='test-bingx-key', bingx_api_secret='test-bingx-secret'))
    local_settings._INITIALIZED_PREFERENCES.clear()
    requests = []
    def fetch(url, **options):
        requests.append((url, options))
        return httpx.Response(200, request=httpx.Request('GET', url), json={'code': 0, 'data': []})
    monkeypatch.setattr(bingx.httpx, 'get', fetch)
    assert bingx.fetch_positions('perpetual') == []
    assert bingx.fetch_positions('standard') == []
    assert len(requests) == 2
    assert all(options['headers']['X-BX-APIKEY'] == 'test-bingx-key' for _, options in requests)
    assert all('test-bingx-secret' not in url for url, _ in requests)
    assert local_settings.public_settings()['integrations']['bingx'] == {'configured': True}
    assert 'test-bingx-key' not in json.dumps(local_settings.public_settings())
