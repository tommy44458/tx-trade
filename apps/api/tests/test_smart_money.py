import httpx
import pytest
from fastapi.testclient import TestClient

from trade_helper import smart_money
from trade_helper.api import app

ORIGIN = "https://cloud.test"


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("TXINTRADE_CLOUD_ORIGIN", ORIGIN)
    smart_money.clear_cache()
    state = {"down": False, "calls": []}
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"].append(str(request.url))
        assert "Authorization" not in request.headers
        if state["down"]:
            return httpx.Response(503)
        if request.url.path == "/smart-money/overview":
            return httpx.Response(200, json={"assets": [{"asset": "BTC"}, {"asset": "ETH"}]})
        if request.url.path == "/smart-money/assets/LINK":
            return httpx.Response(200, json={"asset": "LINK", "window": request.url.params["window"]})
        return httpx.Response(404, json={"error": {"code": "asset_not_tracked"}})

    monkeypatch.setattr(smart_money.httpx, "Client",
                        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    yield state
    smart_money.clear_cache()


def test_overview_is_relayed_from_the_cloud_without_signing_in(cloud):
    response = TestClient(app).get("/api/v1/smart-money/overview")
    assert response.status_code == 200
    assert [a["asset"] for a in response.json()["assets"]] == ["BTC", "ETH"]
    assert cloud["calls"] == [f"{ORIGIN}/smart-money/overview"]


def test_answers_are_cached_for_a_minute(cloud, monkeypatch):
    client = TestClient(app)
    clock = [1000.0]
    monkeypatch.setattr(smart_money.time, "monotonic", lambda: clock[0])
    client.get("/api/v1/smart-money/assets/LINK?window=7d")
    client.get("/api/v1/smart-money/assets/LINK?window=7d")
    assert len(cloud["calls"]) == 1
    clock[0] += 61
    client.get("/api/v1/smart-money/assets/LINK?window=7d")
    assert len(cloud["calls"]) == 2


def test_the_last_good_answer_is_kept_while_the_cloud_is_unreachable(cloud, monkeypatch):
    client = TestClient(app)
    clock = [1000.0]
    monkeypatch.setattr(smart_money.time, "monotonic", lambda: clock[0])
    assert "stale" not in client.get("/api/v1/smart-money/overview").json()
    cloud["down"] = True
    clock[0] += 120
    stale = client.get("/api/v1/smart-money/overview")
    assert stale.status_code == 200
    assert stale.json()["stale"] is True
    smart_money.clear_cache()
    failed = client.get("/api/v1/smart-money/overview")
    assert failed.status_code == 503
    assert failed.json()["detail"] == {"code": "smart_money_unavailable"}


def test_untracked_assets_are_404_and_malformed_requests_never_reach_the_cloud(cloud):
    client = TestClient(app)
    assert client.get("/api/v1/smart-money/assets/DOGE").status_code == 404
    assert client.get("/api/v1/smart-money/assets/link").status_code == 404
    assert client.get("/api/v1/smart-money/assets/LINK?window=2y").status_code == 422
    assert client.get("/api/v1/smart-money/assets/LINK?window=1d&x=../../me").json()["window"] == "1d"
    assert cloud["calls"] == [f"{ORIGIN}/smart-money/assets/DOGE?window=1d",
                              f"{ORIGIN}/smart-money/assets/LINK?window=1d"]


def test_remote_access_cannot_reach_smart_money_yet():
    from trade_helper.cloud_routes import allowed
    assert not allowed({"method": "GET", "path": "/api/v1/smart-money/overview"})
