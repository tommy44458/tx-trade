import base64
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trade_helper import chatgpt_auth as auth
from trade_helper.auth_metadata import read_metadata
from trade_helper.db import database_path


@pytest.fixture
def vault(monkeypatch, tmp_path):
    records = {}
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(auth, "load_credentials", lambda name, **_options: json.loads(records[name]) if name in records else None)
    monkeypatch.setattr(auth, "save_credentials", lambda name, value: records.__setitem__(name, json.dumps(value)))
    auth.cancel_login()
    monkeypatch.setattr(auth, "_last_error", None)
    monkeypatch.setattr(auth, "_jwks_cache", None)
    yield records
    auth.cancel_login()


@pytest.fixture(scope="module")
def signing_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public["kid"], public["alg"] = "test-signing-key", "RS256"
    return key, public


def tokens(attempt, signing_key, **claims_override):
    key, _ = signing_key
    claims = {"iss": auth.ISSUER, "aud": "oaiapp_testing", "sub": "user-123",
              "exp": int(time.time()) + 3600, "iat": int(time.time()),
              "nonce": attempt.nonce, "email": "trader@example.test", **claims_override}
    return {"id_token": jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-signing-key"}),
            "access_token": "access-secret", "refresh_token": "refresh-secret", "token_type": "Bearer",
            "expires_in": 3600, "scope": auth.SCOPES}


def fake_flow(monkeypatch, signing_key, claims_override=None, scope=None):
    observed = []
    def request(method, url, **options):
        observed.append((method, url, options))
        if url == auth.JWKS_ENDPOINT:
            return {"keys": [signing_key[1]]}
        if url == auth.TOKEN_ENDPOINT:
            record = auth.load_credentials(auth._RECORD)
            assert record["pending_client_id"] == "oaiapp_testing"
            response = tokens(auth._pending, signing_key, **(claims_override or {}))
            if scope is not None:
                response["scope"] = scope
            return response
        raise AssertionError(url)
    monkeypatch.setattr(auth, "_http_json", request)
    return observed


def complete_login():
    attempt = auth._pending
    return auth._callback({"state": [attempt.state], "code": ["one-time-code"],
                           "client_id": ["oaiapp_testing"]})


def test_registration_pkce_callback_identity_and_safe_status(vault, monkeypatch, signing_key):
    observed = fake_flow(monkeypatch, signing_key)
    result = auth.start_login()
    attempt = auth._pending
    query = parse_qs(urlsplit(result["auth_url"]).query)
    assert query["client_id"] == ["dynamic_agent_client"]
    assert query["agent_name_hint"] == ["AI Trade Helper"]
    assert query["resource"] == [auth.RESOURCE]
    assert query["code_challenge"] == [base64.urlsafe_b64encode(
        hashlib.sha256(attempt.verifier.encode()).digest()).decode().rstrip("=")]
    assert urlsplit(attempt.redirect_uri).hostname == "127.0.0.1"
    # Real loopback listener, with no desktop header, still requires OAuth state.
    response = httpx.get(attempt.redirect_uri + "?" + urlencode({
        "state": attempt.state, "code": "one-time-code", "client_id": "oaiapp_testing"}))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    current = auth.status()
    assert current["authenticated"] and current["plan_usage_enabled"]
    assert current["email"] == "trader@example.test"
    assert "access-secret" not in json.dumps(current)
    assert "refresh-secret" not in response.text
    assert auth.get_access_token() == "access-secret"
    exchange = next(item for item in observed if item[1] == auth.TOKEN_ENDPOINT)
    assert exchange[2]["data"]["redirect_uri"] == attempt.redirect_uri
    assert exchange[2]["data"]["client_id"] == "oaiapp_testing"
    assert "client_secret" not in exchange[2]["data"]
    assert auth._callback({"state": [attempt.state], "code": ["replay"]})[0] == 400
    assert len([item for item in observed if item[1] == auth.TOKEN_ENDPOINT]) == 1
    # Routine reauthorization keeps host/client and includes only its validated hints.
    renewed = auth.start_login()
    renewed_query = parse_qs(urlsplit(renewed["auth_url"]).query)
    assert renewed_query["ext_agent_host_id"] == query["ext_agent_host_id"]
    assert renewed_query["client_id"] == ["oaiapp_testing"]
    assert "agent_name_hint" not in renewed_query
    assert "id_token_hint" in renewed_query


@pytest.mark.parametrize("claims", [
    {"iss": "https://attacker.example"}, {"aud": "wrong-app"}, {"nonce": "wrong-nonce"},
    {"exp": int(time.time()) - 100}, {"sub": ""},
])
def test_unverified_identity_never_replaces_credentials(vault, monkeypatch, signing_key, claims):
    fake_flow(monkeypatch, signing_key, claims)
    auth.start_login()
    assert complete_login()[0] == 400
    assert not auth.status()["authenticated"]
    assert auth.load_credentials(auth._RECORD)["accounts"] == {}
    assert auth.load_credentials(auth._RECORD)["pending_client_id"] == "oaiapp_testing"


def test_invalid_signature_is_rejected(vault, monkeypatch, signing_key):
    fake_flow(monkeypatch, signing_key)
    auth.start_login()
    wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    raw = jwt.encode({"iss": auth.ISSUER, "aud": "oaiapp_testing", "sub": "user",
                      "exp": int(time.time()) + 3600, "iat": int(time.time()),
                      "nonce": auth._pending.nonce}, wrong, algorithm="RS256",
                     headers={"kid": "test-signing-key"})
    with pytest.raises(auth.ChatGPTAuthError):
        auth._validate_identity(raw, "oaiapp_testing", auth._pending.nonce)


def test_missing_plan_scope_is_not_inference_permission(vault, monkeypatch, signing_key):
    fake_flow(monkeypatch, signing_key, scope="openid email profile")
    auth.start_login()
    assert complete_login()[0] == 200
    assert auth.status()["authenticated"]
    assert not auth.status()["plan_usage_enabled"]
    with pytest.raises(auth.ChatGPTAuthError, match="方案額度"):
        auth.get_access_token()


def test_state_cancel_expiry_and_access_denied_never_redeem(vault, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "_http_json", lambda *args, **kwargs: calls.append(args))
    auth.start_login()
    attempt = auth._pending
    assert auth._callback({"state": ["wrong"], "code": ["attack"]})[0] == 400
    assert auth._pending is attempt
    assert auth._callback({"state": [attempt.state], "error": ["access_denied"]})[0] == 400
    assert not auth.status()["login_pending"]
    auth.start_login()
    attempt = auth._pending
    auth.cancel_login()
    assert auth._callback({"state": [attempt.state], "code": ["late"]})[0] == 400
    auth.start_login()
    attempt = auth._pending
    attempt.deadline = time.monotonic() - 1
    assert auth._callback({"state": [attempt.state], "code": ["expired"]})[0] == 400
    assert not calls


def test_exchange_failure_retains_registration_for_retry(vault, monkeypatch):
    def failed(*args, **kwargs):
        raise auth.ChatGPTAuthError("授權碼已到期，請重試。")
    monkeypatch.setattr(auth, "_http_json", failed)
    original = auth.start_login()
    assert complete_login()[0] == 400
    retry = auth.start_login()
    assert parse_qs(urlsplit(retry["auth_url"]).query)["client_id"] == ["oaiapp_testing"]
    assert parse_qs(urlsplit(retry["auth_url"]).query)["ext_agent_host_id"] == parse_qs(urlsplit(original["auth_url"]).query)["ext_agent_host_id"]


def test_cancel_during_exchange_cannot_activate_account(vault, monkeypatch, signing_key):
    ready, release = threading.Event(), threading.Event()
    def request(method, url, **kwargs):
        if url == auth.TOKEN_ENDPOINT:
            attempt = auth._pending
            ready.set()
            release.wait(3)
            return tokens(attempt, signing_key)
        return {"keys": [signing_key[1]]}
    monkeypatch.setattr(auth, "_http_json", request)
    auth.start_login()
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(complete_login)
        assert ready.wait(2)
        auth.cancel_login()
        release.set()
        assert future.result()[0] == 400
    assert auth.load_credentials(auth._RECORD)["accounts"] == {}


def _expired_account(vault):
    record = {"host_id": "urn:uuid:stable", "active_account_id": "first", "accounts": {
        "first": {"client_id": "oaiapp_first", "issuer": auth.ISSUER, "subject": "user-first",
                  "email": "one@example.test", "access_token": "old-access",
                  "refresh_token": "old-refresh", "expires_at": 0, "scopes": auth.SCOPES.split()}}}
    vault[auth._RECORD] = json.dumps(record)
    return record


def test_refresh_rotation_is_serialized_and_preserves_grants(vault, monkeypatch):
    _expired_account(vault)
    calls = []
    def request(method, url, **kwargs):
        calls.append(kwargs["data"])
        time.sleep(0.03)
        return {"access_token": "new-access", "refresh_token": "rotated-refresh",
                "token_type": "Bearer", "expires_in": 3600}
    monkeypatch.setattr(auth, "_http_json", request)
    with ThreadPoolExecutor(4) as pool:
        assert list(pool.map(lambda _: auth.get_access_token(), range(4))) == ["new-access"] * 4
    assert len(calls) == 1
    assert calls[0]["client_id"] == "oaiapp_first"
    assert "scope" not in calls[0]
    saved = auth.load_credentials(auth._RECORD)["accounts"]["first"]
    assert saved["refresh_token"] == "rotated-refresh"
    assert saved["scopes"] == auth.SCOPES.split()


def test_logout_revokes_only_selected_account_and_retains_registration(vault, monkeypatch):
    record = _expired_account(vault)
    record["accounts"]["second"] = {**record["accounts"]["first"], "client_id": "oaiapp_second"}
    vault[auth._RECORD] = json.dumps(record)
    monkeypatch.setattr(auth, "_http_json", lambda *_args: {"revocation_endpoint": auth.ISSUER + "/revoke"})
    calls = []
    def revoke(method, url, **kwargs):
        calls.append((method, url, kwargs["data"]))
        return httpx.Response(200)
    monkeypatch.setattr(auth.httpx, "request", revoke)
    result = auth.logout()
    saved = auth.load_credentials(auth._RECORD)
    assert not result["authenticated"]
    assert saved["host_id"] == "urn:uuid:stable"
    assert saved["accounts"]["first"]["client_id"] == "oaiapp_first"
    assert "refresh_token" not in saved["accounts"]["first"]
    assert saved["accounts"]["second"]["refresh_token"] == "old-refresh"
    assert calls[0][2]["token_type_hint"] == "refresh_token"
    assert auth.select_account("second")["authenticated"]


def test_routes_return_safe_status_and_model_catalog(vault, monkeypatch):
    record = _expired_account(vault)
    record["accounts"]["first"]["expires_at"] = time.time() + 3600
    vault[auth._RECORD] = json.dumps(record)
    monkeypatch.setattr(auth, "_http_json", lambda *_args, **_kwargs: {
        "models": [{"slug": "gpt-test", "display_name": "Test", "visibility": "list"},
                   {"slug": "hidden", "visibility": "hidden"}]})
    app = FastAPI()
    app.include_router(auth.router)
    with TestClient(app) as client:
        status = client.get("/api/v1/auth/chatgpt/status")
        assert status.status_code == 200
        assert "old-access" not in status.text
        assert client.get("/api/v1/auth/chatgpt/models").json() == []
        assert client.post("/api/v1/auth/chatgpt/check").json()["authenticated"]
        assert client.post("/api/v1/auth/chatgpt/models").json() == [{"id": "gpt-test", "name": "Test"}]
        assert client.get("/api/v1/auth/chatgpt/models").json() == [{"id": "gpt-test", "name": "Test"}]


def test_passive_account_status_and_models_never_open_keychain(vault, monkeypatch, tmp_path):
    record = _expired_account(vault)
    auth._cache_registry(record)
    metadata = json.dumps(read_metadata("chatgpt"))
    assert "old-access" not in metadata and "old-refresh" not in metadata
    assert "subject" not in metadata and "client_id" not in metadata
    assert database_path().stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "auth-chatgpt.json").exists()
    monkeypatch.setattr(auth, "load_credentials", lambda *_args, **_kwargs: pytest.fail("passive UI must not open Keychain"))
    monkeypatch.setattr(auth, "_http_json", lambda *_args, **_kwargs: pytest.fail("passive UI must not fetch authenticated models"))
    app = FastAPI()
    app.include_router(auth.router)
    with TestClient(app) as client:
        for _ in range(3):
            assert client.get("/api/v1/auth/chatgpt/status").json()["authenticated"]
            assert client.get("/api/v1/auth/chatgpt/models").json() == []


def test_unknown_metadata_does_not_claim_authorization_or_open_keychain(vault, monkeypatch):
    monkeypatch.setattr(auth, "load_credentials", lambda *_args, **_kwargs: pytest.fail("must not read Keychain"))
    result = auth.status()
    assert not result["status_known"]
    assert not result["authenticated"]


def test_cached_authenticated_status_cannot_authorize_actual_inference(vault):
    record = _expired_account(vault)
    auth._cache_registry(record)
    assert auth.status()["authenticated"]
    vault.clear()
    with pytest.raises(auth.ChatGPTAuthError, match="登入"):
        auth.get_access_token()
    assert not auth.status()["authenticated"]


def test_returning_account_identity_cannot_be_replaced(vault, monkeypatch, signing_key):
    fake_flow(monkeypatch, signing_key)
    auth.start_login()
    assert complete_login()[0] == 200
    original_id = auth.status()["active_account_id"]
    fake_flow(monkeypatch, signing_key, {"sub": "different-user"})
    auth.start_login()
    assert complete_login()[0] == 400
    assert auth.status()["active_account_id"] == original_id
    assert auth.load_credentials(auth._RECORD)["accounts"][original_id]["subject"] == "user-123"
    assert len(auth.status()["accounts"]) == 1


def test_forced_refresh_does_not_reuse_unexpired_access(vault, monkeypatch):
    record = _expired_account(vault)
    record["accounts"]["first"]["expires_at"] = time.time() + 3600
    vault[auth._RECORD] = json.dumps(record)
    monkeypatch.setattr(auth, "_http_json", lambda *_args, **_kwargs: {
        "access_token": "new-access", "refresh_token": "new-refresh",
        "token_type": "Bearer", "expires_in": 3600})
    assert auth.get_access_token(force_refresh=True) == "new-access"


def test_revoked_session_clears_tokens_and_keeps_registration(vault, monkeypatch):
    _expired_account(vault)
    def revoked(*_args, **_kwargs):
        raise auth.ChatGPTAuthError("ChatGPT 授權已失效；請重新登入。", reauthorize=True)
    monkeypatch.setattr(auth, "_http_json", revoked)
    with pytest.raises(auth.ChatGPTAuthError):
        auth.get_access_token()
    assert not auth.status()["authenticated"]
    saved = auth.load_credentials(auth._RECORD)["accounts"]["first"]
    assert saved["client_id"] == "oaiapp_first"
    assert "access_token" not in saved
    assert "refresh_token" not in saved


def test_remote_logout_retry_and_local_logout_warning(vault, monkeypatch):
    _expired_account(vault)
    monkeypatch.setattr(auth, "_http_json", lambda *_args: {"revocation_endpoint": auth.ISSUER + "/revoke"})
    calls = []
    def transient(*_args, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("secret must not reach UI")
        return httpx.Response(200)
    monkeypatch.setattr(auth.httpx, "request", transient)
    assert auth.logout()["error"] is None
    assert len(calls) == 2
    _expired_account(vault)
    monkeypatch.setattr(auth.httpx, "request", lambda *_args, **_kwargs: httpx.Response(503))
    result = auth.logout()
    assert not result["authenticated"]
    assert "遠端撤銷未確認" in result["error"]
    assert "secret" not in result["error"]
