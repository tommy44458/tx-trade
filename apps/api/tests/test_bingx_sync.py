import hashlib
import hmac
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from trade_helper.api import app
from trade_helper.bingx import BingXError, fetch_positions, normalize_positions
from trade_helper.db import connect

pytestmark = pytest.mark.usefixtures("pg_schema")

SWAP = {
    "positionId": "swap-1", "symbol": "BTC-USDT", "positionAmt": "0.02",
    "positionSide": "LONG", "avgPrice": "90000", "leverage": "10",
    "isolated": True, "liquidationPrice": "82000",
}
STANDARD = {
    "symbol": "ETHUSDT", "positionAmt": "0.5", "positionSide": "SHORT",
    "entryPrice": "3200", "leverage": "5", "isolated": False,
    "time": 1700000000000,
}


def test_signed_read_only_request_never_exposes_secret(monkeypatch):
    monkeypatch.setenv("BINGX_API_KEY", "test-key")
    monkeypatch.setenv("BINGX_API_SECRET", "test-secret")
    monkeypatch.setattr("trade_helper.bingx.time.time", lambda: 1700000000)
    observed = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"code": 0, "data": [SWAP]}

    def fake_get(url, **kwargs):
        observed.append((url, kwargs))
        return Response()

    monkeypatch.setattr("trade_helper.bingx.httpx.get", fake_get)
    assert fetch_positions("perpetual") == [SWAP]
    url, options = observed[0]
    assert urlsplit(url).path == "/openApi/swap/v2/user/positions"
    assert options["headers"]["X-BX-APIKEY"] == "test-key"
    query = urlsplit(url).query
    signed, signature = query.rsplit("&signature=", 1)
    assert signature == hmac.new(b"test-secret", signed.encode(), hashlib.sha256).hexdigest()
    assert parse_qs(signed)["timestamp"] == ["1700000000000"]


def test_normalizes_two_contract_types_without_inventing_stops():
    swap, _ = normalize_positions("perpetual", [SWAP])
    standard, _ = normalize_positions("standard", [STANDARD])
    assert swap[0]["contract_type"] == "perpetual"
    assert swap[0]["exchange_liquidation_price"] == "82000"
    assert standard[0]["contract_type"] == "standard"
    assert standard[0]["side"] == "short"
    assert standard[0]["entry_time"] == "2023-11-14T22:13:20+00:00"
    assert "stop_loss" not in swap[0]
    assert "take_profit" not in standard[0]


def test_sync_is_idempotent_preserves_manual_and_closes_only_missing_bingx(monkeypatch):
    monkeypatch.setenv("APP_LOCAL_USER_ID", "bingx-test")
    monkeypatch.setenv("BINGX_API_KEY", "test-key")
    monkeypatch.setenv("BINGX_API_SECRET", "test-secret")
    state = {"perpetual": [SWAP], "standard": [STANDARD]}
    monkeypatch.setattr("trade_helper.bingx_sync.fetch_positions", lambda kind: state[kind])
    with TestClient(app) as client:
        manual = client.post("/api/v1/positions", json={
            "market_id": "binance:perp:BTCUSDT", "side": "long", "leverage": 5,
            "margin_mode": "isolated", "entry_price": "80000", "quantity": "0.1",
        }).json()
        first = client.post("/api/v1/positions/bingx/sync")
        assert first.status_code == 200, first.text
        assert first.json()["created"] == 2
        assert client.get("/api/v1/integrations/bingx").json()["last_sync"] == first.json()
        positions = client.get("/api/v1/positions").json()
        assert len(positions) == 3
        standard = next(p for p in positions if p["contract_type"] == "standard")
        swap = next(p for p in positions if p["contract_type"] == "perpetual")
        assert swap["stop_loss"] is None
        assert swap["source"] == "bingx"
        assert client.post(f"/api/v1/positions/{swap['id']}/close").status_code == 409
        assert client.post("/api/v1/analyses", json={
            "kind": "positions", "market_id": standard["market_id"],
            "position_ids": [standard["id"]],
        }, headers={"Idempotency-Key": "standard-position-test"}).status_code == 202
        assert client.post("/api/v1/positions/bingx/sync").json()["updated"] == 0
        state["standard"] = []
        result = client.post("/api/v1/positions/bingx/sync").json()
        assert result["closed"] == 1
        positions = client.get("/api/v1/positions").json()
        assert {p["id"] for p in positions} == {manual["id"], swap["id"]}
        with connect() as db:
            assert db.execute("SELECT status FROM positions WHERE id=?", (standard["id"],)).fetchone()["status"] == "closed"


def test_failed_second_endpoint_does_not_close_saved_positions(monkeypatch):
    monkeypatch.setenv("APP_LOCAL_USER_ID", "bingx-test")
    monkeypatch.setenv("BINGX_API_KEY", "test-key")
    monkeypatch.setenv("BINGX_API_SECRET", "test-secret")
    state = {"perpetual": [SWAP], "standard": []}
    monkeypatch.setattr("trade_helper.bingx_sync.fetch_positions", lambda kind: state[kind])
    with TestClient(app) as client:
        assert client.post("/api/v1/positions/bingx/sync").status_code == 200
        last_success = client.get("/api/v1/integrations/bingx").json()["last_sync"]
        state["perpetual"] = []

        def fail_standard(kind):
            if kind == "standard":
                raise BingXError("standard unavailable")
            return []

        monkeypatch.setattr("trade_helper.bingx_sync.fetch_positions", fail_standard)
        assert client.post("/api/v1/positions/bingx/sync").status_code == 503
        assert len(client.get("/api/v1/positions").json()) == 1
        assert client.get("/api/v1/integrations/bingx").json()["last_sync"] == last_success
