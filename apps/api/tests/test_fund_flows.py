import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from trade_helper import fund_flows
from trade_helper.api import app
from trade_helper.db import connect, init_db

NOW = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
HOUR = 3_600_000


def ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def totals(inflow=0.0, outflow=0.0, price=1.0, whales=0):
    return {"inflow": inflow, "outflow": outflow, "inflow_usd": inflow * price,
            "outflow_usd": outflow * price, "whale_count": whales, "whale_usd": whales * 2e6,
            "net": inflow - outflow, "net_usd": (inflow - outflow) * price}


def hourly(per_hour_in: float, per_hour_out: float, price: float, hours: int = 168):
    start = ms(NOW) - (hours - 1) * HOUR
    return {"step": "hour", "points": [
        {"t": start + i * HOUR, "inflow": per_hour_in, "outflow": per_hour_out,
         "inflow_usd": per_hour_in * price, "outflow_usd": per_hour_out * price} for i in range(hours)]}


def event(kind, usd, hours_ago, to_entity=None, from_entity=None):
    return {"kind": kind, "amount": usd / 2000, "usd": usd, "from_entity": from_entity,
            "to_entity": to_entity, "from_address": "0xa", "to_address": "0xb", "tx": "0x1",
            "chain": "eth", "ts": ms(NOW) - hours_ago * HOUR}


def overview(sync_minutes_ago=3, stale=False):
    def summary(asset, price):
        return {"asset": asset, "coverage": "large_only" if asset == "BTC" else "exchange_and_whales",
                "1d": totals(240, 120, price, 2), "7d": totals(1680, 1000, price, 9),
                "series": hourly(10, 5, price),
                "events": [event("exchange_in", 3e6, 2, to_entity="Binance"), event("whale", 9e6, 5)]}
    body = {"generated_at": ms(NOW), "assets": [summary("BTC", 80000), summary("ETH", 2000)],
            "stablecoins": {"total_usd": 3.1e11, "change_1d": 4e8, "change_7d": 4e9, "change_30d": None,
                            "series": [], "exchange": {"1d": {"inflow_usd": 5e6, "outflow_usd": 2e7, "net_usd": -1.5e7},
                                                       "7d": {"inflow_usd": 9e7, "outflow_usd": 1e8, "net_usd": -1e7}}},
            "sync": {chain: {"height": 1, "updated_at": ms(datetime.now(UTC) - timedelta(minutes=sync_minutes_ago))}
                     for chain in ("btc", "eth")}}
    if stale:
        body["stale"] = True
    return body


def link_detail(window="7d"):
    return {"generated_at": ms(NOW), "asset": "LINK", "window": window, "coverage": "exchange",
            "totals": totals(7000, 4000, 14), "series": hourly(50, 20, 14),
            "events": [event("exchange_out", 2e6, 3, from_entity="OKX"), event("exchange_in", 1.5e6, 30, to_entity="Binance"),
                       event("whale", 1.2e6, 10)], "sync": {}}


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setenv("TRADE_FUND_FLOWS_ENABLED", "1")
    state = {"down": False, "overview": overview(), "calls": []}

    def fetch(path):
        state["calls"].append(path)
        if state["down"]:
            raise HTTPException(503, {"code": "smart_money_unavailable"})
        if path == "/smart-money/overview":
            return state["overview"]
        if path.startswith("/smart-money/assets/LINK"):
            return link_detail(path.rsplit("=", 1)[-1])
        raise HTTPException(404, {"code": "asset_not_tracked"})

    monkeypatch.setattr(fund_flows, "fetch_cloud", fetch)
    return state


def test_markets_name_their_coins():
    assert fund_flows.asset_for_market("binance:perp:BTCUSDT") == "BTC"
    assert fund_flows.asset_for_market("binance:perp:1000PEPEUSDT") == "PEPE"
    assert fund_flows.asset_for_market("binance:perp:1INCHUSDT") == "1INCH"


def test_analysis_always_gets_btc_eth_and_stablecoins_and_the_pair_when_tracked(cloud):
    context = fund_flows.fund_flows_context("binance:perp:LINKUSDT")
    assert context["status"] == "available"
    assert set(context["assets"]) == {"BTC", "ETH"}
    eth = context["assets"]["ETH"]
    assert eth["24h"] == {"inflow": 240, "outflow": 120, "net": 120, "inflow_usd": 480000,
                          "outflow_usd": 240000, "net_usd": 240000, "whale_count": 2, "whale_usd": 4000000}
    assert [day["net_usd"] for day in eth["daily_net_usd"]][-1] > 0
    assert eth["largest_24h"][0] == {"kind": "whale", "amount": 4500, "usd": 9000000,
                                     "from": "unlabelled wallet", "to": "unlabelled wallet",
                                     "at": (NOW - timedelta(hours=5)).isoformat()}
    assert context["stablecoins"]["exchange_net_24h_usd"] == -15000000
    link = context["pair_asset"]
    assert link["status"] == "tracked" and link["asset"] == "LINK"
    # The last 24 hourly points of the seven-day answer: 24 × 50 in, 24 × 20 out.
    assert link["24h"]["inflow"] == 1200 and link["24h"]["outflow"] == 480
    assert link["24h"]["whale_count"] == 1
    assert link["7d"]["net"] == 3000
    assert [e["usd"] for e in link["largest_7d"]] == [2000000, 1500000, 1200000]


def test_untracked_reference_and_outage_cases_are_labelled_not_guessed(cloud):
    assert fund_flows.fund_flows_context("binance:perp:SOLUSDT")["pair_asset"] == {"asset": "SOL", "status": "not_tracked"}
    assert fund_flows.fund_flows_context("binance:perp:ETHUSDT")["pair_asset"] == {"asset": "ETH", "status": "same_as_reference"}
    cloud["overview"] = overview(sync_minutes_ago=90)
    assert fund_flows.fund_flows_context("binance:perp:BTCUSDT")["status"] == "stale"
    cloud["overview"] = overview(stale=True)
    assert fund_flows.fund_flows_context("binance:perp:BTCUSDT")["status"] == "stale"
    cloud["down"] = True
    assert fund_flows.fund_flows_context("binance:perp:LINKUSDT") == {
        "version": fund_flows.VERSION, "source": fund_flows.SOURCE, "status": "unavailable",
        "pair_asset": {"asset": "LINK", "status": "unavailable"}}


def test_disabled_fund_flows_never_reach_the_cloud(cloud, monkeypatch):
    monkeypatch.setenv("TRADE_FUND_FLOWS_ENABLED", "0")
    assert fund_flows.fund_flows_context("binance:perp:BTCUSDT")["status"] == "unavailable"
    assert cloud["calls"] == []


def test_a_conversation_starts_from_a_frozen_snapshot_and_asks_with_its_data(cloud, monkeypatch):
    from trade_helper import discussions

    init_db()
    client = TestClient(app)
    assert client.get("/api/v1/smart-money/snapshots/latest").json() == {"snapshot": None}
    created = client.post("/api/v1/smart-money/snapshots", json={"asset": "LINK", "window": "30d"})
    assert created.status_code == 201
    snapshot = created.json()
    assert snapshot["asset"] == "LINK" and snapshot["window"] == "30d"
    assert client.get("/api/v1/smart-money/snapshots/latest").json()["snapshot"]["id"] == snapshot["id"]
    assert cloud["calls"][-1] == "/smart-money/assets/LINK?window=30d"

    sent = client.post(f"/api/v1/discussions/fund_flows/{snapshot['id']}/messages", json={
        "message": "LINK 流出交易所代表什麼？", "request_id": "00000000-0000-4000-8000-000000000001",
        "output_locale": "en-US"})
    assert sent.status_code == 202
    assert sent.json()["subject"]["title"] == "Fund flows"

    seen = {}

    def reply(context, messages, *, timeout, instructions=None, on_text=None):
        from trade_helper.discussion_context import build_discussion_input

        seen["instructions"] = instructions
        seen["input"] = json.loads(build_discussion_input(context, messages))
        return "Outflows may be withdrawals to holding; labels are incomplete.", "codex", "test-model"

    monkeypatch.setattr(discussions, "generate_reply", reply)
    discussions.run_once()
    state = client.get(f"/api/v1/discussions/fund_flows/{snapshot['id']}").json()
    assert state["messages"][-1]["status"] == "completed", state["messages"][-1]
    frozen = seen["input"]["frozen_context"]
    assert frozen["fund_flows"]["focus"]["asset"] == "LINK"
    assert "live_market" not in seen["input"]
    assert "frozen_context.fund_flows" in seen["instructions"]


def test_an_unknown_snapshot_or_coin_is_refused(cloud):
    init_db()
    client = TestClient(app)
    assert client.post("/api/v1/smart-money/snapshots", json={"asset": "link", "window": "1d"}).status_code == 422
    assert client.post("/api/v1/smart-money/snapshots", json={"asset": "LINK", "window": "2y"}).status_code == 422
    assert client.post("/api/v1/smart-money/snapshots", json={"asset": "SOL", "window": "1d"}).status_code == 404
    response = client.post("/api/v1/discussions/fund_flows/flow_missing/messages", json={
        "message": "hi", "request_id": "00000000-0000-4000-8000-000000000002"})
    assert response.status_code == 404


def test_upgrade_keeps_existing_conversations_and_their_messages():
    init_db()
    with connect() as db:
        db.execute("INSERT INTO macro_interpretations(id,user_id,status,evidence_version,prompt_version,"
                   "fingerprint,evidence_json,created_at,updated_at) VALUES('m1','local-demo','succeeded',"
                   "'v','p','f','{}','2026-10-01','2026-10-01')")
        db.execute("INSERT INTO discussion_sessions(id,user_id,subject_type,subject_id,macro_id,subject_json,"
                   "context_json,created_at,updated_at) VALUES('s1','local-demo','macro','m1','m1','{}','{}',"
                   "'2026-10-01','2026-10-01')")
        db.execute("INSERT INTO discussion_messages(id,session_id,sequence,role,content,status,request_id,"
                   "created_at) VALUES('u1','s1',1,'user','hello','completed','r1','2026-10-01')")
        db.execute("DELETE FROM schema_migrations WHERE version>=10")
        db.execute("PRAGMA user_version=9")
    init_db()
    with connect(readonly=True) as db:
        assert db.execute("SELECT subject_type FROM discussion_sessions").fetchone()["subject_type"] == "macro"
        assert db.execute("SELECT content FROM discussion_messages").fetchone()["content"] == "hello"
        assert "fund_flows" in db.execute(
            "SELECT sql FROM sqlite_master WHERE name='discussion_sessions'").fetchone()["sql"]
