import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APITimeoutError, AuthenticationError, RateLimitError

from trade_helper import worker
from trade_helper.api import app
from trade_helper.codex_bridge import CodexError, CodexTimeoutError
from trade_helper.db import connect
from trade_helper.error_locale import analysis_failure_message, model_error_message, system_error_message
from trade_helper.model_providers import ModelProviderError

from .test_analysis import sample_candles

SECRET = "upstream-secret-never-return"


@pytest.mark.parametrize("exception", [
    ValueError(SECRET), RuntimeError(SECRET), ModelProviderError(SECRET), CodexError(SECRET),
    TimeoutError(SECRET), CodexTimeoutError(SECRET),
    APITimeoutError(request=httpx.Request("POST", "https://model.invalid/" + SECRET)),
    AuthenticationError(SECRET, response=httpx.Response(401, request=httpx.Request(
        "POST", "https://model.invalid/" + SECRET)), body={"api_key": SECRET}),
    RateLimitError(SECRET, response=httpx.Response(429, request=httpx.Request(
        "POST", "https://model.invalid/" + SECRET)), body={"api_key": SECRET}),
])
@pytest.mark.parametrize("locale", ["zh-TW", "en-US"])
def test_model_errors_never_expose_provider_messages_or_request_data(exception, locale):
    message = model_error_message(exception, locale)
    assert SECRET not in message
    assert "https://" not in message
    assert "api_key" not in message
    if locale == "en-US":
        assert all(ord(character) < 128 for character in message)
    else:
        assert "模型" in message


def test_known_error_details_keep_chinese_contract_and_have_english_messages():
    validation = ValueError("Agent entry decision lacks a clear reason")
    assert model_error_message(validation) == (
        "模型回應處理失敗：Agent entry decision lacks a clear reason")
    assert model_error_message(validation, "en-US") == (
        "The model response could not be processed: Agent entry decision lacks a clear reason")
    missing = RuntimeError("OPENAI_API_KEY and OPENAI_MODEL are required for Agent analysis")
    assert model_error_message(missing) == (
        "尚未設定 OPENAI_API_KEY 或 OPENAI_MODEL，無法產生 Agent 策略分析")
    assert "not configured" in model_error_message(missing, "en-US")
    assert model_error_message(ValueError(SECRET)) == "模型工具流程失敗：ValueError"
    setup = ModelProviderError("APP_ANALYSIS_TIMEOUT_SECONDS 必須介於 30 與 900 秒。")
    assert model_error_message(setup) == str(setup)
    assert "between 30 and 900" in model_error_message(setup, "en-US")
    # A future provider's custom class name is not a user-visible diagnostic.
    unknown = type("UpstreamSecretClass", (Exception,), {})(SECRET)
    assert "UpstreamSecretClass" not in model_error_message(unknown, "en-US")
    assert SECRET not in system_error_message(SECRET, "en-US")


def _market_stubs(monkeypatch, *, stale=None):
    def candles(_market, timeframe, *, limit):
        recent = not (stale == "candles" and timeframe == "1h"
                      or stale == "context" and timeframe == "4h")
        return sample_candles(recent=recent, timeframe=timeframe)

    observed = datetime.now(UTC) - timedelta(minutes=4 if stale == "quote" else 0)
    monkeypatch.setattr(worker, "fetch_candles", candles)
    monkeypatch.setattr(worker, "fetch_quote", lambda *_: {
        "price": "120.5", "mark_price": "120.4", "observed_at": observed.isoformat()})
    monkeypatch.setattr(worker, "fetch_forming_candle", lambda *_: None)
    monkeypatch.setattr(worker, "fetch_order_book", lambda *_: None)
    monkeypatch.setattr(worker, "fetch_tick_size", lambda *_: Decimal("0.1"))


@pytest.mark.parametrize(("source", "code"), [
    ("quote", "QUOTE_STALE"), ("candles", "CANDLES_STALE"),
    ("context", "CONTEXT_CANDLES_STALE"),
])
@pytest.mark.parametrize("locale", ["zh-TW", "en-US"])
def test_worker_saves_stale_error_in_frozen_locale_without_changing_market_data(
        monkeypatch, source, code, locale):
    _market_stubs(monkeypatch, stale=source)
    monkeypatch.setattr(worker, "analyze_with_tools", lambda *_args, **_kwargs:
                        pytest.fail("Stale market data must not call a model"))
    with TestClient(app) as client:
        created = client.post("/api/v1/analyses", json={
            "kind": "market", "market_id": "binance:perp:BTCUSDT", "timeframe": "1h",
            "output_locale": locale, "leverage": 20, "risk_tolerance": "high",
        }, headers={"Idempotency-Key": "localized-stale"}).json()
        # Later UI preference changes cannot change the queued error language.
        client.patch("/api/v1/settings", json={
            "ui_locale": "zh-TW" if locale == "en-US" else "en-US"})
        assert worker.run_once()
        result = client.get("/api/v1/analyses/" + created["id"]).json()
        assert result["status"] == "failed"
        assert result["report"] is None
        assert result["error"]["code"] == code
        assert result["error"]["message"] == system_error_message(code, locale)
        with connect(readonly=True) as db:
            saved = db.execute("SELECT error_message,snapshot_json,request_json FROM analyses "
                               "WHERE id=?", (created["id"],)).fetchone()
        assert saved["error_message"] == result["error"]["message"]
        snapshot = json.loads(saved["snapshot_json"])
        submitted = json.loads(saved["request_json"])
        assert snapshot["quote"]["price"] == "120.5"
        assert snapshot["quote"]["mark_price"] == "120.4"
        assert snapshot["quote"]["tick_size"] == "0.1"
        assert snapshot["candles"][0]["volume"] == "100"
        assert type(submitted["leverage"]) is int and submitted["leverage"] == 20
        assert type(snapshot["candles"][0]["close"]) is str


@pytest.mark.parametrize("exception", [ValueError(SECRET), ModelProviderError(SECRET),
                                       TimeoutError(SECRET)])
def test_worker_saves_model_failure_using_prompt_locale_without_raw_payload(monkeypatch, exception):
    _market_stubs(monkeypatch)

    def model(*_args, **kwargs):
        assert kwargs["prompt_bundle"].response_locale == "en-US"
        raise exception

    monkeypatch.setattr(worker, "analyze_with_tools", model)
    with TestClient(app) as client:
        created = client.post("/api/v1/analyses", json={
            "kind": "market", "market_id": "binance:perp:BTCUSDT", "timeframe": "1h",
            "output_locale": "en-US",
        }, headers={"Idempotency-Key": "localized-model-error"}).json()
        # Deliberately disagree with the input to prove the saved artifact wins.
        with connect() as db:
            row = db.execute("SELECT request_json FROM analyses WHERE id=?",
                             (created["id"],)).fetchone()
            request = json.loads(row["request_json"])
            request["output_locale"] = "zh-TW"
            db.execute("UPDATE analyses SET request_json=? WHERE id=?",
                       (json.dumps(request), created["id"]))
        assert worker.run_once()
        result = client.get("/api/v1/analyses/" + created["id"]).json()
        assert result["status"] == "failed"
        assert result["error"]["code"] == "MODEL_CALL_FAILED"
        assert result["error"]["message"] == model_error_message(exception, "en-US")
        assert SECRET not in json.dumps(result)
        with connect(readonly=True) as db:
            row = db.execute("SELECT error_message FROM analyses WHERE id=?",
                             (created["id"],)).fetchone()
        assert row["error_message"] == result["error"]["message"]


def test_worker_does_not_save_unknown_feed_payload(monkeypatch):
    def failed_fetch(*_args, **_kwargs):
        raise RuntimeError(SECRET)

    monkeypatch.setattr(worker, "fetch_candles", failed_fetch)
    with TestClient(app) as client:
        created = client.post("/api/v1/analyses", json={
            "kind": "market", "market_id": "binance:perp:BTCUSDT", "timeframe": "1h",
            "output_locale": "en-US",
        }, headers={"Idempotency-Key": "localized-feed-error"}).json()
        assert worker.run_once()
        result = client.get("/api/v1/analyses/" + created["id"]).json()
        assert result["error"] == {"code": "ANALYSIS_FAILED",
                                    "message": analysis_failure_message("candles", RuntimeError(SECRET), "en-US")}
        assert SECRET not in json.dumps(result)
