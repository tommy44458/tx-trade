import pytest
from fastapi.testclient import TestClient

from trade_helper import local_settings
from trade_helper.api import app


@pytest.mark.parametrize("theme", ["system", "light", "dark"])
def test_theme_roundtrip_and_reload_preserve_other_local_settings(theme):
    original = {
        "ui_locale": "en-US", "model_provider": "codex", "models": {"codex": "chosen-model"},
        "favorite_market_ids": ["binance:perp:SOLUSDT"], "initial_indicators": ["obv"],
        "trading_preferences": {"risk_tolerance": "high", "trading_style": "left", "leverage": 40},
        "codex_auth_scope": "existing", "codex_account_status": {"authenticated": True},
    }
    local_settings.patch_preferences(original)
    with TestClient(app) as client:
        changed = client.patch("/api/v1/settings", json={"ui_theme": theme})
        assert changed.status_code == 200
        assert changed.json()["ui_theme"] == theme
        assert local_settings.preferences() == {**original, "ui_theme": theme}
        # Simulate the preferences reader being initialized by a later process.
        local_settings._INITIALIZED_PREFERENCES.clear()
        reloaded = client.get("/api/v1/settings").json()
        assert reloaded["ui_theme"] == theme
        assert reloaded["model"] == "chosen-model"
        assert reloaded["favorite_market_ids"] == original["favorite_market_ids"]
        assert reloaded["initial_indicators"] == original["initial_indicators"]


@pytest.mark.parametrize("theme", ["system", "light", "dark"])
def test_omitted_and_null_theme_do_not_reset_saved_selection(theme):
    local_settings.patch_preferences({"ui_theme": theme})
    with TestClient(app) as client:
        assert client.put("/api/v1/settings", json={"ui_locale": "en-US"}).json()["ui_theme"] == theme
        assert client.patch("/api/v1/settings", json={"ui_theme": None}).json()["ui_theme"] == theme
        assert local_settings.ui_theme() == theme
        assert local_settings.preferences()["ui_theme"] == theme


@pytest.mark.parametrize("invalid", ["auto", "Dark", "", True, 0, [], {}, ["dark"]])
def test_invalid_theme_is_rejected_without_overwriting_preferences(invalid):
    local_settings.patch_preferences({"ui_theme": "dark", "ui_locale": "en-US"})
    with TestClient(app) as client:
        response = client.patch("/api/v1/settings", json={"ui_theme": invalid})
        assert response.status_code == 422
        assert local_settings.preferences() == {"ui_theme": "dark", "ui_locale": "en-US"}
        assert client.get("/api/v1/settings").json()["ui_theme"] == "dark"


@pytest.mark.parametrize("legacy", [None, "unknown", "LIGHT", True, 0, [], {}])
def test_legacy_malformed_theme_defaults_to_system_without_rewriting_metadata(legacy):
    local_settings.patch_preferences({"ui_theme": legacy})
    with TestClient(app) as client:
        assert client.get("/api/v1/settings").json()["ui_theme"] == "system"
    assert local_settings.preferences()["ui_theme"] == legacy


def test_missing_theme_defaults_to_system_without_a_migration():
    with TestClient(app) as client:
        assert client.get("/api/v1/settings").json()["ui_theme"] == "system"
    assert "ui_theme" not in local_settings.preferences()


def test_theme_get_and_save_do_not_decrypt_credentials_or_contact_model_auth(monkeypatch):
    monkeypatch.setenv("APP_DESKTOP", "1")
    monkeypatch.setenv("APP_DESKTOP_TOKEN", "theme-test-session")
    original = {
        "model_provider": "codex", "models": {"codex": "current-model"},
        "managed_integrations": ["bingx", "jev"],
        "integration_status": {"bingx": {"configured": True, "revision": "unchanged"},
                               "jev": {"configured": True, "revision": "unchanged"}},
        "codex_auth_scope": "application", "codex_disconnected": False,
        "ui_locale": "zh-TW", "favorite_market_ids": ["binance:perp:BTCUSDT"],
    }
    local_settings.patch_preferences(original)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Reading or saving appearance must not use credentials or model authorization")

    for name in ("load_credentials", "save_credentials", "delete_credentials", "integration_credentials"):
        monkeypatch.setattr(local_settings, name, forbidden)
    monkeypatch.setattr(local_settings, "credential_status", lambda name: name in {"bingx", "jev"})
    monkeypatch.setattr("trade_helper.codex_bridge.require_authorized", forbidden)
    monkeypatch.setattr("trade_helper.claude_code_bridge.require_authorized", forbidden)
    monkeypatch.setattr("trade_helper.model_providers.ModelSession", forbidden)
    with TestClient(app, headers={"Authorization": "Bearer theme-test-session"}) as client:
        initial = client.get("/api/v1/settings").json()
        assert initial["ui_theme"] == "system"
        for theme in ("dark", "light", "system"):
            response = client.patch("/api/v1/settings", json={"ui_theme": theme})
            assert response.status_code == 200
            assert response.json()["integrations"] == initial["integrations"]
            assert local_settings.preferences() == {**original, "ui_theme": theme}
