import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trade_helper.analysis import position_review
from trade_helper.api import app
from trade_helper.db import connect

pytestmark = pytest.mark.usefixtures("pg_schema")
MARKET = "binance:perp:BTCUSDT"
BASE = {"market_id": MARKET, "side": "long", "leverage": 5, "margin_mode": "isolated",
        "entry_price": "100", "quantity": "2", "stop_loss": "90", "take_profit": "130"}


def test_position_stop_diagnostics_for_long_and_short():
    long = position_review({"id": "l", "version": 2, **BASE,
                            "previous_stop_loss": "85"}, {"price": "110"})
    assert long["original_risk_reward"] == "3.00"
    assert long["remaining_risk_reward"] == "1.00"
    assert long["stop_risk_delta_usdt"] == "-10"
    assert long["manual_stop_reached"] is False
    protected = position_review({"id": "l", "version": 3, **BASE,
                                 "stop_loss": "105", "previous_stop_loss": "90"},
                                {"price": "110"})
    assert protected["profitable_side_stop"] is True
    assert protected["original_risk_reward"] is None
    assert protected["stop_risk_delta_usdt"] == "-30"
    assert protected["remaining_risk_reward"] == "4.00"
    assert position_review({"id": "l", "version": 3, **BASE, "stop_loss": "105"},
                           {"price": "104"})["manual_stop_reached"] is True
    short = position_review({"id": "s", "version": 1, **BASE, "side": "short",
                             "stop_loss": "110", "take_profit": "70",
                             "previous_stop_loss": "115"}, {"price": "90"})
    assert short["original_risk_reward"] == "3.00"
    assert short["remaining_risk_reward"] == "1.00"
    assert short["stop_risk_delta_usdt"] == "-10"
    assert position_review({"id": "s", "version": 1, **BASE, "side": "short",
                            "stop_loss": "95", "take_profit": "70"},
                           {"price": "96"})["manual_stop_reached"] is True


def test_identity_version_and_old_report_freshness(monkeypatch):
    monkeypatch.setenv("APP_LOCAL_USER_ID", "alice")
    with TestClient(app) as client:
        position = client.post("/api/v1/positions", json=BASE).json()
        job = client.post("/api/v1/analyses", json={
            "kind": "positions", "market_id": MARKET, "timeframe": "1h",
            "position_ids": [position["id"]]},
            headers={"Idempotency-Key": str(uuid4())}).json()
        assert job["freshness"] == "fresh"
        monkeypatch.setenv("APP_LOCAL_USER_ID", "bob")
        assert client.get("/api/v1/positions").json() == []
        assert client.get(f"/api/v1/analyses/{job['id']}").status_code == 404
        assert client.patch(f"/api/v1/positions/{position['id']}",
                            json=BASE | {"expected_version": 1}).status_code == 409
        assert client.post("/api/v1/analyses", json={
            "kind": "positions", "market_id": MARKET, "position_ids": [position["id"]]},
            headers={"Idempotency-Key": str(uuid4())}).status_code == 404
        monkeypatch.setenv("APP_LOCAL_USER_ID", "alice")
        updated = client.patch(f"/api/v1/positions/{position['id']}",
                               json=BASE | {"stop_loss": "95", "expected_version": 1})
        assert updated.status_code == 200
        assert updated.json()["previous_stop_loss"] == "90"
        assert updated.json()["version"] == 2
        assert client.patch(f"/api/v1/positions/{position['id']}",
                            json=BASE | {"expected_version": 1}).status_code == 409
        stale = client.get(f"/api/v1/analyses/{job['id']}").json()
        assert stale["freshness"] == "stale"
        assert stale["stale_reasons"] == ["position_changed"]
        with connect() as db:
            assert db.execute("SELECT count(*) AS n FROM analyses").fetchone()["n"] == 1
        assert client.post(f"/api/v1/positions/{position['id']}/close").status_code == 200
        assert client.get(f"/api/v1/analyses/{job['id']}").json()["freshness"] == "stale"


def test_delete_removes_position_and_derived_personal_report(monkeypatch):
    monkeypatch.setenv("APP_LOCAL_USER_ID", "alice")
    with TestClient(app) as client:
        position = client.post("/api/v1/positions", json=BASE).json()
        job = client.post("/api/v1/analyses", json={
            "kind": "positions", "market_id": MARKET, "position_ids": [position["id"]]},
            headers={"Idempotency-Key": str(uuid4())}).json()
        with connect() as db:
            db.execute(
                "INSERT INTO analysis_embeddings(analysis_id,user_id,summary,content_hash) "
                "VALUES(?,?,?,?)", (job["id"], "alice", "private summary", "hash")
            )
            db.commit()
        monkeypatch.setenv("APP_LOCAL_USER_ID", "bob")
        assert client.delete(f"/api/v1/positions/{position['id']}").status_code == 404
        monkeypatch.setenv("APP_LOCAL_USER_ID", "alice")
        assert client.delete(f"/api/v1/positions/{position['id']}").json()["status"] == "deleted"
        assert client.get(f"/api/v1/analyses/{job['id']}").status_code == 404
        assert client.get("/api/v1/positions").json() == []
        with connect() as db:
            assert db.execute(
                "SELECT count(*) AS n FROM analysis_embeddings WHERE user_id=?", ("alice",)
            ).fetchone()["n"] == 0


def test_manual_analysis_freezes_all_same_market_positions_in_requested_order(monkeypatch):
    monkeypatch.setenv("APP_LOCAL_USER_ID", "alice")
    with TestClient(app) as client:
        ids = [client.post("/api/v1/positions", json=BASE).json()["id"] for _ in range(12)][::-1]
        body = {"kind": "positions", "market_id": MARKET, "timeframe": "4h",
                "position_ids": ids}
        headers = {"Idempotency-Key": str(uuid4())}
        first = client.post("/api/v1/analyses", json=body, headers=headers)
        assert first.status_code == 202
        job_id = first.json()["id"]
        assert first.json()["submitted_input"]["position_ids"] == ids
        with connect(readonly=True) as db:
            original = db.execute("SELECT positions_json FROM analyses WHERE id=?", (job_id,)).fetchone()
        assert [item["id"] for item in json.loads(original["positions_json"])] == ids
        assert all(item["version"] == 1 for item in json.loads(original["positions_json"]))
        assert client.patch(f"/api/v1/positions/{ids[0]}",
                            json=BASE | {"entry_price": "105", "expected_version": 1}).status_code == 200
        replay = client.post("/api/v1/analyses", json=body, headers=headers)
        assert replay.status_code == 202 and replay.json()["id"] == job_id
        assert replay.json()["freshness"] == "stale"
        with connect(readonly=True) as db:
            assert db.execute("SELECT positions_json FROM analyses WHERE id=?", (job_id,)).fetchone() == original
