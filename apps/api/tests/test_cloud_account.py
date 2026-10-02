import base64
import hashlib
import json
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from trade_helper import cloud_account
from trade_helper.api import app
from trade_helper.credential_store import load_credentials

ORIGIN = "https://cloud.test"
TOKEN = "st_" + "a" * 64
CODE = "c" * 64


@pytest.fixture(autouse=True)
def cloud(monkeypatch):
    monkeypatch.setenv("TXINTRADE_CLOUD_ORIGIN", ORIGIN)
    calls = []
    state = {"token_status": 201, "logout_fails": False}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/auth/desktop/token":
            if state["token_status"] != 201:
                return httpx.Response(state["token_status"], json={"error": {"code": "invalid_grant"}})
            return httpx.Response(201, json={"session_token": TOKEN, "account_id": "acc_1",
                                             "expires_at": int(time.time() * 1000) + 86_400_000})
        if request.url.path == "/api/v1/me":
            assert request.headers["Authorization"] == f"Bearer {TOKEN}"
            return httpx.Response(200, json={"account_id": "acc_1", "entitlements": [], "profile": {
                "email": "tester@gmail.com", "display_name": "Tester",
                "picture_url": "https://lh3.googleusercontent.com/a/test"}})
        return httpx.Response(404)

    monkeypatch.setattr(cloud_account.httpx, "Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))

    def logout(url, headers, timeout):
        calls.append(httpx.Request("POST", url, headers=headers))
        if state["logout_fails"]:
            raise httpx.ConnectError("offline")
        return httpx.Response(204)

    monkeypatch.setattr(cloud_account.httpx, "post", logout)
    yield {"calls": calls, "state": state}
    cloud_account.cancel_sign_in()


def browser_returns(start_url: str, **params) -> tuple[int, str]:
    """Follow the cloud redirect back to the app's loopback, as a browser would."""
    query = parse_qs(urlsplit(start_url).query)
    values = {"state": query["state"][0], **params}
    url = f"http://127.0.0.1:{query['port'][0]}/callback?" + "&".join(f"{k}={v}" for k, v in values.items())
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


def test_desktop_sign_in_brings_its_own_pkce_and_stores_the_session_privately(cloud):
    client = TestClient(app)
    started = client.post("/api/v1/cloud-account/sign-in").json()
    url = urlsplit(started["start_url"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ORIGIN}/auth/google/start"
    assert query["client"] == ["desktop"] and 1024 <= int(query["port"][0]) <= 65535
    assert started["status"]["pending"] is True

    status, page = browser_returns(started["start_url"], code=CODE)
    assert status == 200 and "Signed in" in page and CODE not in page
    redeem = next(call for call in cloud["calls"] if call.url.path == "/auth/desktop/token")
    body = json.loads(redeem.content)
    assert body["code"] == CODE
    # The challenge sent to the cloud is the S256 of the verifier redeemed here.
    challenge = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).decode().rstrip("=")
    assert challenge == query["code_challenge"][0]

    account = client.get("/api/v1/cloud-account").json()
    assert account["signed_in"] is True and account["pending"] is False and account["error"] is None
    assert account["profile"] == {"email": "tester@gmail.com", "display_name": "Tester",
                                  "picture_url": "https://lh3.googleusercontent.com/a/test"}
    assert load_credentials("txintrade_cloud", allow_interaction=True)["session_token"] == TOKEN
    session = client.get("/api/v1/session").json()
    assert session["cloud_account"]["email"] == "tester@gmail.com"
    for response in (account, session, started):
        assert TOKEN not in json.dumps(response)


def test_a_session_from_another_cloud_asks_to_sign_in_again(cloud, monkeypatch):
    client = TestClient(app)
    started = client.post("/api/v1/cloud-account/sign-in").json()
    assert browser_returns(started["start_url"], code=CODE)[0] == 200
    assert client.get("/api/v1/cloud-account").json()["signed_in"] is True
    # The app now points at production instead of the development cloud.
    monkeypatch.setenv("TXINTRADE_CLOUD_ORIGIN", "https://api.txintrade.com")
    assert client.get("/api/v1/cloud-account").json()["signed_in"] is False
    assert cloud_account.session_record() is None


def test_a_wrong_state_neither_signs_in_nor_cancels_the_real_attempt(cloud):
    started = TestClient(app).post("/api/v1/cloud-account/sign-in").json()
    query = parse_qs(urlsplit(started["start_url"]).query)
    url = f"http://127.0.0.1:{query['port'][0]}/callback?state=forged&code={CODE}"
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(url, timeout=10)
    assert error.value.code == 400
    assert not cloud["calls"]
    assert cloud_account.status()["pending"] is True
    assert browser_returns(started["start_url"], code=CODE)[0] == 200


@pytest.mark.parametrize(("params", "token_status", "expected"), [
    ({"error": "access_denied"}, 201, "cancelled"),
    ({"code": "not-a-code"}, 201, "invalid_response"),
    ({"code": CODE}, 400, "rejected"),
])
def test_unfinished_sign_in_reports_a_stable_code_and_stores_nothing(cloud, params, token_status, expected):
    cloud["state"]["token_status"] = token_status
    started = cloud_account.start_sign_in()
    status, page = browser_returns(started["start_url"], **params)
    assert status == 400 and "did not finish" in page
    account = cloud_account.status()
    assert account == {**account, "signed_in": False, "pending": False, "error": expected}
    assert load_credentials("txintrade_cloud", allow_interaction=True) is None


def test_sign_out_revokes_in_the_cloud_and_always_removes_the_local_session(cloud):
    for offline in (False, True):
        cloud["state"]["logout_fails"] = offline
        started = cloud_account.start_sign_in()
        assert browser_returns(started["start_url"], code=CODE)[0] == 200
        result = TestClient(app).post("/api/v1/cloud-account/sign-out").json()
        logout = cloud["calls"][-1]
        assert str(logout.url) == f"{ORIGIN}/auth/logout"
        assert logout.headers["Authorization"] == f"Bearer {TOKEN}"
        assert result["signed_in"] is False and result["profile"] is None
        assert load_credentials("txintrade_cloud", allow_interaction=True) is None
        assert TestClient(app).get("/api/v1/session").json()["cloud_account"] is None


def test_expired_sessions_and_timeouts_are_not_shown_as_signed_in(cloud):
    started = cloud_account.start_sign_in()
    assert browser_returns(started["start_url"], code=CODE)[0] == 200
    cloud_account.write_metadata("txintrade_cloud", {
        **cloud_account.read_metadata("txintrade_cloud"), "expires_at": int(time.time() * 1000) - 1})
    assert cloud_account.status()["signed_in"] is False
    cloud_account.start_sign_in()
    cloud_account._expire(cloud_account._pending)
    assert cloud_account.status()["pending"] is False and cloud_account.status()["error"] == "timeout"


@pytest.mark.parametrize("origin", ["http://cloud.test", "https://cloud.test/path", "ftp://cloud.test"])
def test_only_https_or_a_development_loopback_origin_is_used(monkeypatch, origin):
    monkeypatch.setenv("TXINTRADE_CLOUD_ORIGIN", origin)
    assert cloud_account.status() == {"configured": False, "signed_in": False, "pending": False,
                                      "error": "misconfigured", "profile": None, "expires_at": None}
    assert TestClient(app).post("/api/v1/cloud-account/sign-in").status_code == 400
    monkeypatch.setenv("TXINTRADE_CLOUD_ORIGIN", "http://localhost:8787")
    assert cloud_account.status()["configured"] is True


def test_cloud_routes_are_left_out_of_api_documentation():
    paths = TestClient(app).get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith("/api/v1/cloud-account")]
    assert "/api/v1/settings" in paths


def test_the_remote_web_page_address_follows_the_cloud(monkeypatch):
    monkeypatch.delenv("TXINTRADE_CLOUD_ORIGIN", raising=False)
    monkeypatch.delenv("TXINTRADE_WEB_ORIGIN", raising=False)
    assert cloud_account.status()["web_url"] == "https://app.txintrade.com"
    # A development cloud has no remote page unless one is configured, and only over HTTPS.
    monkeypatch.setenv("TXINTRADE_CLOUD_ORIGIN", "http://localhost:8787")
    assert cloud_account.status()["web_url"] is None
    monkeypatch.setenv("TXINTRADE_WEB_ORIGIN", "https://remote.example.test")
    assert cloud_account.status()["web_url"] == "https://remote.example.test"
    monkeypatch.setenv("TXINTRADE_WEB_ORIGIN", "http://remote.example.test")
    assert cloud_account.status()["web_url"] is None
