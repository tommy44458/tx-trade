"""Private account snapshots use only synthetic credentials and offline adapters."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trade_helper import binance_sync, local_settings
from trade_helper.api import app
from trade_helper.binance import BinanceError
from trade_helper.credential_store import delete_credentials, save_credentials
from trade_helper.db import SCHEMA_VERSION, connect, init_db, utc_now

MARKET = "binance:perp:BTCUSDT"
SCOPE = "a" * 32
POSITION = {
    "external_position_id": "BTCUSDT:LONG", "market_id": MARKET,
    "exchange_symbol": "BTCUSDT", "contract_type": "perpetual", "side": "long",
    "quantity": "0.0200", "entry_price": "90000.00", "leverage": 10,
    "margin_mode": "isolated", "entry_time": None,
    "exchange_liquidation_price": "82000", "mark_price": "91000",
    "unrealized_profit": "20", "exchange_update_time": "2026-10-01T00:00:00+00:00",
}


def _account(scope=SCOPE, key="synthetic-key"):
    save_credentials("binance", {"api_key": key, "api_secret": "synthetic-secret",
                                 "account_scope": scope})
    local_settings.patch_preferences({"binance_account_scope": scope,
                                      "managed_integrations": ["binance"]})


def _snapshot(positions, *, closure_allowed=True, unsupported=0):
    return {"positions": deepcopy(positions), "unsupported": unsupported,
            "skipped_symbols": ["UNKNOWNUSDT"] if unsupported else [],
            "closure_allowed": closure_allowed,
            "started_at": utc_now(), "completed_at": utc_now()}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_LOCAL_USER_ID", "binance-test")
    _account()
    with TestClient(app) as value:
        yield value


def _stored():
    with connect(readonly=True) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("positions", "position_exchange_estimates", "integration_sync_state")}


def _mock(monkeypatch, positions):
    state = {"snapshot": _snapshot(positions), "credentials": []}

    def fetch(*, credentials):
        state["credentials"].append(credentials)
        return deepcopy(state["snapshot"])

    monkeypatch.setattr(binance_sync.binance, "fetch_snapshot", fetch)
    return state


def test_two_hedge_legs_decimal_idempotency_and_local_metadata(client, monkeypatch):
    short = POSITION | {"external_position_id": "BTCUSDT:SHORT", "side": "short",
                        "quantity": "0.03", "entry_price": "92000"}
    state = _mock(monkeypatch, [POSITION, short])
    first = client.post("/api/v1/positions/binance/sync")
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 2
    positions = client.get("/api/v1/positions").json()
    assert {row["side"] for row in positions} == {"long", "short"}
    assert {row["external_position_id"] for row in positions} == {
        f"{SCOPE}:BTCUSDT:LONG", f"{SCOPE}:BTCUSDT:SHORT"}
    assert all(row["source"] == "binance" and row["account_scope"] == SCOPE for row in positions)
    assert all(row["stop_loss"] is None and row["take_profit"] is None for row in positions)
    state["snapshot"]["positions"][0].update(entry_price="90000", quantity="0.02")
    again = client.post("/api/v1/positions/binance/sync")
    assert again.json()["created"] == again.json()["updated"] == 0
    assert all(row["version"] == 1 for row in client.get("/api/v1/positions").json())
    assert state["credentials"][0]["account_scope"] == SCOPE
    assert state["credentials"][0]["_credential_revision"]

    monkeypatch.setattr(local_settings, "load_credentials_with_revision",
                        lambda *_args, **_kwargs: pytest.fail("status must not decrypt credentials"))
    monkeypatch.setattr(binance_sync.binance, "fetch_snapshot",
                        lambda **_kwargs: pytest.fail("status must not query private account"))
    status = client.get("/api/v1/integrations/binance")
    assert status.status_code == 200
    assert status.json() == {"configured": True, "contracts": ["perpetual"],
                             "last_sync": again.json()}
    assert "synthetic" not in status.text


def test_estimate_refresh_keeps_position_version_and_saved_report_fresh(client, monkeypatch):
    state = _mock(monkeypatch, [POSITION])
    client.post("/api/v1/positions/binance/sync")
    position = client.get("/api/v1/positions").json()[0]
    assert position["exchange_liquidation_price"] == "82000"
    created = client.post("/api/v1/analyses", json={
        "kind": "positions", "market_id": MARKET, "position_ids": [position["id"]]},
        headers={"Idempotency-Key": str(uuid4())})
    assert created.status_code == 202, created.text
    job = created.json()
    with connect(readonly=True) as db:
        row = db.execute("SELECT positions_json FROM analyses WHERE id=?", (job["id"],)).fetchone()
        frozen = json.loads(row["positions_json"])[0]
        assert frozen["exchange_liquidation_price"] == "82000"
        assert frozen["mark_price"] == "91000"
        original = db.execute("SELECT * FROM positions WHERE id=?", (position["id"],)).fetchone()
        assert original["exchange_liquidation_price"] is None
    state["snapshot"]["positions"][0].update(
        mark_price="95000", unrealized_profit="100", exchange_liquidation_price="82500",
        exchange_update_time="2026-10-01T01:00:00+00:00")
    refreshed = client.post("/api/v1/positions/binance/sync").json()
    assert refreshed["updated"] == 0
    newest = client.get("/api/v1/positions").json()[0]
    assert newest["exchange_liquidation_price"] == "82500"
    assert newest["mark_price"] == "95000"
    assert newest["version"] == position["version"]
    assert newest["updated_at"] == original["updated_at"]
    assert client.get(f"/api/v1/analyses/{job['id']}").json()["freshness"] == "fresh"
    state["snapshot"]["positions"][0]["quantity"] = "0.03"
    assert client.post("/api/v1/positions/binance/sync").json()["updated"] == 1
    assert client.get(f"/api/v1/analyses/{job['id']}").json()["freshness"] == "stale"


def test_decimal_canonicalization_is_lossless_even_beyond_context_precision(client, monkeypatch):
    quantity = "0.123456789012345678901234567890123456789"
    state = _mock(monkeypatch, [POSITION | {"quantity": quantity + "000"}])
    assert client.post("/api/v1/positions/binance/sync").json()["created"] == 1
    stored = client.get("/api/v1/positions").json()[0]
    assert stored["quantity"] == quantity
    state["snapshot"]["positions"][0]["quantity"] = quantity
    assert client.post("/api/v1/positions/binance/sync").json()["updated"] == 0
    assert client.get("/api/v1/positions").json()[0]["version"] == 1


def test_complete_empty_snapshot_closes_only_current_binance_scope(client, monkeypatch):
    state = _mock(monkeypatch, [POSITION])
    client.post("/api/v1/positions/binance/sync")
    with connect() as db:
        now = utc_now()
        for identifier, source, scope in (("manual", "manual", None), ("bingx", "bingx", None),
                                           ("old-scope", "binance", "b" * 32),
                                           ("other-owner", "binance", SCOPE)):
            db.execute("""INSERT INTO positions(
                id,user_id,market_id,version,side,entry_price,quantity,status,created_at,updated_at,
                source,external_position_id,account_scope)
                VALUES(?,?,?,1,'long','10','1','open',?,?,?,?,?)""",
                       (identifier, "someone-else" if identifier == "other-owner" else "binance-test",
                        MARKET, now, now, source, identifier, scope))
    state["snapshot"] = _snapshot([])
    response = client.post("/api/v1/positions/binance/sync")
    assert response.json()["closed"] == 1
    assert response.json()["active"] == 0
    with connect(readonly=True) as db:
        assert {row["id"] for row in db.execute("SELECT id FROM positions WHERE status='open'")} == {
            "manual", "bingx", "old-scope", "other-owner"}


def test_unknown_market_never_closes_missing_saved_positions(client, monkeypatch):
    state = _mock(monkeypatch, [POSITION])
    client.post("/api/v1/positions/binance/sync")
    state["snapshot"] = _snapshot([], closure_allowed=False, unsupported=1)
    response = client.post("/api/v1/positions/binance/sync")
    assert response.json()["closed"] == 0
    assert response.json()["closure_allowed"] is False
    assert len(client.get("/api/v1/positions").json()) == 1


@pytest.mark.parametrize("code", ["INVALID_POSITION", "INVALID_CONFIG", "MARKET_CATALOG_UNAVAILABLE",
                                  "AUTHENTICATION_FAILED", "RATE_LIMITED", "NETWORK_ERROR"])
def test_adapter_failures_atomically_preserve_positions_estimates_and_last_success(
    client, monkeypatch, code,
):
    _mock(monkeypatch, [POSITION])
    client.post("/api/v1/positions/binance/sync")
    before = _stored()

    def fail(**_kwargs):
        raise BinanceError(code, "synthetic-secret key=private-provider-payload",
                           exchange_code=-2015, retry_after=5)

    monkeypatch.setattr(binance_sync.binance, "fetch_snapshot", fail)
    response = client.post("/api/v1/positions/binance/sync")
    assert response.status_code == 503
    assert response.json()["detail"]["exchange_code"] == -2015
    assert response.json()["detail"]["retry_after"] == 5
    assert "synthetic" not in response.text and "private-provider" not in response.text
    assert _stored() == before


@pytest.mark.parametrize("change", ["scope", "revision", "clear"])
def test_changed_connection_discards_late_snapshot_before_any_write(client, monkeypatch, change):
    _mock(monkeypatch, [POSITION])
    client.post("/api/v1/positions/binance/sync")
    before = _stored()

    def late(*, credentials):
        assert credentials["account_scope"] == SCOPE
        if change == "scope":
            _account("b" * 32, key="changed-key")
        elif change == "revision":
            # Key replacement is already committed but the public scope mirror
            # is not updated yet: the revision guard still rejects the response.
            save_credentials("binance", {"api_key": "changed-key", "api_secret": "changed-secret",
                                         "account_scope": "b" * 32})
        else:
            delete_credentials("binance")
        return _snapshot([])

    monkeypatch.setattr(binance_sync.binance, "fetch_snapshot", late)
    response = client.post("/api/v1/positions/binance/sync")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "BINANCE_ACCOUNT_CHANGED"
    assert _stored() == before


def test_scope_rotation_isolated_history_and_last_sync_survives_reload(client, monkeypatch):
    _mock(monkeypatch, [POSITION])
    first = client.post("/api/v1/positions/binance/sync").json()
    old = _stored()
    _account("b" * 32, key="another-key")
    assert client.get("/api/v1/integrations/binance").json()["last_sync"] is None
    second = client.post("/api/v1/positions/binance/sync").json()
    assert first["created"] == second["created"] == 1
    assert len(client.get("/api/v1/positions").json()) == 2
    assert _stored()["positions"][0] in old["positions"] or _stored()["positions"][1] in old["positions"]
    init_db()
    assert client.get("/api/v1/integrations/binance").json()["last_sync"] == second
    local_settings.patch_preferences(remove=("binance_account_scope",))
    delete_credentials("binance")
    assert client.get("/api/v1/integrations/binance").json()["last_sync"] is None
    assert len(_stored()["integration_sync_state"]) == 2


def test_database_failure_rolls_back_entire_snapshot_and_summary(client, monkeypatch):
    state = _mock(monkeypatch, [POSITION])
    client.post("/api/v1/positions/binance/sync")
    before = _stored()
    state["snapshot"]["positions"][0]["quantity"] = "0.03"

    def fail(*_args):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(binance_sync, "save_sync_state", fail)
    with pytest.raises(RuntimeError, match="simulated database"):
        binance_sync.sync_positions()
    assert _stored() == before


def test_imported_facts_immutable_notes_editable_manual_close_blocked_local_delete_allowed(
    client, monkeypatch,
):
    _mock(monkeypatch, [POSITION])
    client.post("/api/v1/positions/binance/sync")
    imported = client.get("/api/v1/positions").json()[0]
    response = client.patch(f"/api/v1/positions/{imported['id']}", json={
        "expected_version": imported["version"], "market_id": "binance:perp:ETHUSDT",
        "side": "short", "leverage": 100, "margin_mode": "cross", "entry_price": "1",
        "quantity": "100", "exchange_liquidation_price": "2", "stop_loss": "85000",
        "take_profit": "98000", "notes": "my local note"})
    assert response.status_code == 200, response.text
    result = response.json()
    for field in binance_sync.EXCHANGE_FIELDS + ("exchange_liquidation_price",):
        assert result[field] == imported[field]
    assert result["notes"] == "my local note"
    assert result["stop_loss"] == "85000"
    assert client.post(f"/api/v1/positions/{imported['id']}/close").status_code == 409
    assert client.delete(f"/api/v1/positions/{imported['id']}").status_code == 200
    assert _stored()["positions"] == _stored()["position_exchange_estimates"] == []


def test_explicit_connection_test_never_writes_positions_or_sync_summary(client, monkeypatch):
    from trade_helper import api

    before = _stored()
    result = {"readable": True, "active": 1, "active_symbols": ["BTCUSDT"], "unsupported": 0,
              "started_at": utc_now(), "completed_at": utc_now(),
              "permissions": {"verified": False, "read_only": None, "reading": None,
                              "write_permissions": [], "error_code": "UNAUTHORIZED"}}
    observed = []
    monkeypatch.setattr(api, "test_binance_connection",
                        lambda *, credentials: (observed.append(credentials), result)[1])
    response = client.post("/api/v1/integrations/binance/test")
    assert response.status_code == 200 and response.json() == result
    assert observed[0]["account_scope"] == SCOPE
    assert _stored() == before


@pytest.mark.parametrize("clear", [False, True])
def test_connection_test_does_not_report_success_after_key_rotation(client, monkeypatch, clear):
    from trade_helper import api

    before = _stored()

    def test(*, credentials):
        assert credentials["account_scope"] == SCOPE
        if clear:
            delete_credentials("binance")
        else:
            _account("b" * 32)
        return {"readable": True, "permissions": {"verified": True, "read_only": True}}

    monkeypatch.setattr(api, "test_binance_connection", test)
    response = client.post("/api/v1/integrations/binance/test")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "BINANCE_ACCOUNT_CHANGED"
    assert _stored() == before


def test_connection_error_keeps_only_fixed_message_and_numeric_metadata(client, monkeypatch):
    from trade_helper import api

    def fail(**_kwargs):
        raise BinanceError("AUTHENTICATION_FAILED", "synthetic-secret provider-payload",
                           exchange_code=-2015, retry_after=3)

    monkeypatch.setattr(api, "test_binance_connection", fail)
    response = client.post("/api/v1/integrations/binance/test")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "AUTHENTICATION_FAILED"
    assert response.json()["detail"]["exchange_code"] == -2015
    assert response.json()["detail"]["retry_after"] == 3
    assert "synthetic" not in response.text and "provider-payload" not in response.text
    assert _stored()["integration_sync_state"] == []


@pytest.mark.parametrize("older_positions", [[], [POSITION | {"quantity": "0.01"}]])
def test_late_older_snapshot_cannot_overwrite_newer_completed_sync(client, monkeypatch, older_positions):
    state = _mock(monkeypatch, [POSITION])
    state["snapshot"]["started_at"] = "2026-10-01T00:00:00+00:00"
    client.post("/api/v1/positions/binance/sync")
    newer = _snapshot([POSITION | {"quantity": "0.03"}])
    newer["started_at"] = "2026-10-01T00:02:00+00:00"
    older = _snapshot(older_positions)
    older["started_at"] = "2026-10-01T00:01:00+00:00"
    waiting, release = Event(), Event()

    def fetch(*, credentials):
        if not waiting.is_set():
            waiting.set()
            assert release.wait(5), "mock newer sync did not finish"
            return older
        return newer

    monkeypatch.setattr(binance_sync.binance, "fetch_snapshot", fetch)
    with ThreadPoolExecutor(max_workers=1) as pool:
        late = pool.submit(binance_sync.sync_positions)
        try:
            assert waiting.wait(5)
            latest = binance_sync.sync_positions()
            assert latest["updated"] == 1
            preserved = _stored()
        finally:
            release.set()
        with pytest.raises(binance_sync.BinanceSyncSupersededError):
            late.result(timeout=5)
    assert _stored() == preserved
    position = client.get("/api/v1/positions").json()[0]
    assert position["quantity"] == "0.03" and position["version"] == 2
    assert client.get("/api/v1/integrations/binance").json()["last_sync"] == latest
    assert latest["started_at"] == newer["started_at"]
    assert latest["completed_at"] == newer["completed_at"]
    monkeypatch.setattr(binance_sync.binance, "fetch_snapshot", lambda **_kwargs: older)
    response = client.post("/api/v1/positions/binance/sync")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "BINANCE_SYNC_SUPERSEDED"
    assert _stored() == preserved


def test_missing_credentials_returns_safe_structured_error_and_owner_isolation(client, monkeypatch):
    _mock(monkeypatch, [POSITION])
    client.post("/api/v1/positions/binance/sync")
    monkeypatch.setenv("APP_LOCAL_USER_ID", "another-owner")
    assert client.get("/api/v1/positions").json() == []
    assert client.get("/api/v1/integrations/binance").json()["last_sync"] is None
    delete_credentials("binance")
    response = client.post("/api/v1/positions/binance/sync")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "BINANCE_CREDENTIALS_UNAVAILABLE"
    assert "synthetic" not in response.text


def test_migration_seven_is_additive_and_repeated_initialization_is_safe(client):
    with connect() as db:
        db.execute("DROP TABLE position_exchange_estimates")
        db.execute("DROP TABLE integration_sync_state")
        db.execute("DROP INDEX positions_exchange_scope")
        db.execute("ALTER TABLE positions DROP COLUMN account_scope")
        db.execute("DELETE FROM schema_migrations WHERE version=7")
        db.execute("PRAGMA user_version=6")
    init_db()
    init_db()
    with connect(readonly=True) as db:
        assert db.execute("PRAGMA user_version").fetchone()["user_version"] == SCHEMA_VERSION == 7
        assert db.execute("SELECT * FROM position_exchange_estimates").fetchall() == []
        assert db.execute("SELECT * FROM integration_sync_state").fetchall() == []
        assert db.execute("SELECT count(*) AS n FROM schema_migrations WHERE version=7").fetchone()["n"] == 1
