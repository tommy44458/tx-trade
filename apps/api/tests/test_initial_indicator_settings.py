import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from trade_helper import local_settings
from trade_helper.api import app
from trade_helper.db import connect
from trade_helper.indicator_preferences import (
    INITIAL_INDICATOR_DEFAULT_PARAMETERS,
    initial_indicator_catalog,
    normalize_initial_indicators,
)
from trade_helper.indicators import validate_tool_parameters
from trade_helper.models import AnalysisRequest


def test_default_catalog_is_complete_and_matches_real_tool_parameters(monkeypatch):
    monkeypatch.setattr(local_settings, "load_credentials", lambda *_args, **_kwargs:
                        pytest.fail("Indicator settings must not decrypt API keys"))
    with TestClient(app) as client:
        settings = client.get("/api/v1/settings").json()
    assert settings["initial_indicators"] == []
    assert settings["initial_indicator_catalog"] == initial_indicator_catalog()
    assert {item["name"] for item in settings["initial_indicator_catalog"]} == {
        "bollinger", "fibonacci", "adx_dmi", "obv", "donchian", "keltner", "stochastic",
    }
    for item in settings["initial_indicator_catalog"]:
        assert item["name"] == item["tool"]
        validate_tool_parameters(item["tool"], {"reason": "Initial strategy evidence",
                                                **item["parameters"]})
    # A caller cannot change calculation defaults through a returned catalog.
    settings["initial_indicator_catalog"][0]["parameters"]["period"] = 999
    assert INITIAL_INDICATOR_DEFAULT_PARAMETERS["bollinger"]["period"] == 20


def test_patch_persists_selection_and_preserves_other_preferences():
    local_settings.patch_preferences({
        "model_provider": "codex", "models": {"codex": "current-model"},
        "favorite_market_ids": ["binance:perp:SOLUSDT"], "ui_locale": "en-US",
        "trading_preferences": {"risk_tolerance": "high", "trading_style": "left"},
        "integration_status": {"jev": {"configured": False}},
    })
    original = local_settings.preferences()
    with TestClient(app) as client:
        changed = client.patch("/api/v1/settings", json={
            "initial_indicators": ["obv", "bollinger", "obv"],
        })
        assert changed.status_code == 200
        assert changed.json()["initial_indicators"] == ["bollinger", "obv"]
        saved = local_settings.preferences()
        assert saved == {**original, "initial_indicators": ["bollinger", "obv"]}
        # Other partial writers and reloads preserve the indicator selection.
        client.patch("/api/v1/settings", json={"ui_locale": "zh-TW"})
        local_settings._INITIALIZED_PREFERENCES.clear()
        assert client.get("/api/v1/settings").json()["initial_indicators"] == ["bollinger", "obv"]
        client.patch("/api/v1/settings", json={"initial_indicators": None})
        assert client.get("/api/v1/settings").json()["initial_indicators"] == ["bollinger", "obv"]
        cleared = client.patch("/api/v1/settings", json={"initial_indicators": []}).json()
        assert cleared["initial_indicators"] == []
        assert cleared["model"] == "current-model"
        assert cleared["favorite_market_ids"] == ["binance:perp:SOLUSDT"]


@pytest.mark.parametrize("invalid", [
    ["made_up"], ["support_resistance"], ["rsi"], [True], [1], {"obv": True}, "obv",
])
def test_invalid_selection_is_rejected_without_changing_settings(invalid):
    local_settings.patch_preferences({"initial_indicators": ["obv"]})
    with TestClient(app) as client:
        response = client.patch("/api/v1/settings", json={"initial_indicators": invalid})
        assert response.status_code == 422
        assert client.get("/api/v1/settings").json()["initial_indicators"] == ["obv"]
    with pytest.raises(ValidationError):
        AnalysisRequest(market_id="binance:perp:BTCUSDT", initial_indicators=invalid)


@pytest.mark.parametrize(("legacy", "expected"), [
    (None, []), ("obv", []), ({"obv": True}, []), (True, []),
    (["unknown", "obv", None, {}, True, "obv", "bollinger"], ["bollinger", "obv"]),
])
def test_legacy_selection_is_read_safely_without_rewriting_it(legacy, expected):
    local_settings.patch_preferences({"initial_indicators": legacy})
    with TestClient(app) as client:
        assert client.get("/api/v1/settings").json()["initial_indicators"] == expected
    assert local_settings.preferences()["initial_indicators"] == legacy
    assert normalize_initial_indicators(legacy) == expected


def test_indicator_preferences_are_isolated_by_local_user(monkeypatch):
    local_settings.patch_preferences({"initial_indicators": ["obv"]})
    monkeypatch.setenv("APP_LOCAL_USER_ID", "another-user")
    assert local_settings.initial_indicators() == []
    local_settings.patch_preferences({"initial_indicators": ["bollinger"]})
    monkeypatch.delenv("APP_LOCAL_USER_ID", raising=False)
    assert local_settings.initial_indicators() == ["obv"]


@pytest.mark.parametrize("kind", ["market", "positions"])
def test_new_job_freezes_settings_and_idempotent_retry_preserves_old_selection(kind):
    with TestClient(app) as client:
        body = {"kind": kind, "market_id": "binance:perp:BTCUSDT", "timeframe": "1h"}
        if kind == "positions":
            position = client.post("/api/v1/positions", json={
                "market_id": body["market_id"], "entry_price": "100", "quantity": "2",
            }).json()
            body["position_ids"] = [position["id"]]
        client.patch("/api/v1/settings", json={"initial_indicators": ["bollinger", "obv"]})
        headers = {"Idempotency-Key": f"frozen-indicators-{kind}"}
        first = client.post("/api/v1/analyses", json=body, headers=headers)
        assert first.status_code == 202
        assert first.json()["submitted_input"]["initial_indicators"] == ["bollinger", "obv"]
        assert first.json()["submitted_input"]["initial_indicator_parameters"] == {
            "bollinger": {"period": 20, "multiplier": 2}, "obv": {"period": 20},
        }
        assert first.json()["submitted_input"]["initial_indicator_catalog_version"] == "optional_indicators_v1"
        job_id = first.json()["id"]
        with connect(readonly=True) as db:
            original = db.execute("SELECT request_json FROM analyses WHERE id=?", (job_id,)).fetchone()
        client.patch("/api/v1/settings", json={"initial_indicators": ["adx_dmi"]})
        replay = client.post("/api/v1/analyses", json=body, headers=headers)
        assert replay.status_code == 202
        assert replay.json()["id"] == job_id
        assert replay.json()["submitted_input"]["initial_indicators"] == ["bollinger", "obv"]
        changed = client.post("/api/v1/analyses", json=body, headers={
            "Idempotency-Key": f"new-indicators-{kind}",
        })
        assert changed.json()["submitted_input"]["initial_indicators"] == ["adx_dmi"]
        with connect(readonly=True) as db:
            assert db.execute("SELECT request_json FROM analyses WHERE id=?", (job_id,)).fetchone() == original


@pytest.mark.parametrize(("explicit", "expected"), [
    (None, ["bollinger"]), ([], []), (["obv", "obv"], ["obv"]),
    (["stochastic", "fibonacci"], ["fibonacci", "stochastic"]),
])
def test_explicit_request_selection_overrides_saved_setting(explicit, expected):
    local_settings.patch_preferences({"initial_indicators": ["bollinger"]})
    with TestClient(app) as client:
        body = {"market_id": "binance:perp:BTCUSDT", "initial_indicators": explicit}
        response = client.post("/api/v1/analyses", json=body, headers={
            "Idempotency-Key": "explicit-initial-indicators",
        })
        assert response.status_code == 202
        assert response.json()["submitted_input"]["initial_indicators"] == expected
        with connect(readonly=True) as db:
            row = db.execute("SELECT request_json FROM analyses WHERE id=?",
                             (response.json()["id"],)).fetchone()
        assert json.loads(row["request_json"])["initial_indicators"] == expected
        assert local_settings.initial_indicators() == ["bollinger"]


def test_explicit_selection_is_an_idempotent_input():
    with TestClient(app) as client:
        body = {"market_id": "binance:perp:BTCUSDT", "initial_indicators": ["obv", "bollinger"]}
        headers = {"Idempotency-Key": "explicit-idempotent-selection"}
        first = client.post("/api/v1/analyses", json=body, headers=headers)
        replay = client.post("/api/v1/analyses", json={
            **body, "initial_indicators": ["bollinger", "obv", "obv"],
        }, headers=headers)
        assert first.status_code == replay.status_code == 202
        assert first.json()["id"] == replay.json()["id"]
        assert client.post("/api/v1/analyses", json={
            **body, "initial_indicators": ["fibonacci"],
        }, headers=headers).status_code == 409


def test_missing_preferences_freezes_an_empty_selection():
    with TestClient(app) as client:
        response = client.post("/api/v1/analyses", json={"market_id": "binance:perp:BTCUSDT"},
                               headers={"Idempotency-Key": "no-saved-indicators"})
    assert response.status_code == 202
    assert response.json()["submitted_input"]["initial_indicators"] == []


def test_queued_parameter_defaults_do_not_change_after_upgrade(monkeypatch):
    with TestClient(app) as client:
        body = {"market_id": "binance:perp:BTCUSDT", "initial_indicators": ["bollinger"]}
        headers = {"Idempotency-Key": "frozen-parameter-defaults"}
        first = client.post("/api/v1/analyses", json=body, headers=headers).json()
        monkeypatch.setitem(INITIAL_INDICATOR_DEFAULT_PARAMETERS, "bollinger",
                            {"period": 30, "multiplier": 2})
        assert client.post("/api/v1/analyses", json=body, headers=headers).json()["id"] == first["id"]
        saved = client.get(f"/api/v1/analyses/{first['id']}").json()["submitted_input"]
        assert saved["initial_indicator_parameters"]["bollinger"] == {"period": 20, "multiplier": 2}
        new = client.post("/api/v1/analyses", json=body,
                          headers={"Idempotency-Key": "new-parameter-defaults"}).json()
        assert new["submitted_input"]["initial_indicator_parameters"]["bollinger"] == {"period": 30, "multiplier": 2}


def test_server_frozen_parameters_cannot_be_overridden_by_client():
    with TestClient(app) as client:
        response = client.post("/api/v1/analyses", json={
            "market_id": "binance:perp:BTCUSDT", "initial_indicators": ["bollinger"],
            "initial_indicator_parameters": {"bollinger": {"period": 99, "multiplier": 9}},
            "initial_indicator_catalog_version": "fake",
        }, headers={"Idempotency-Key": "server-owned-parameters"})
    assert response.status_code == 202
    saved = response.json()["submitted_input"]
    assert saved["initial_indicator_parameters"]["bollinger"] == {"period": 20, "multiplier": 2}
    assert saved["initial_indicator_catalog_version"] == "optional_indicators_v1"
