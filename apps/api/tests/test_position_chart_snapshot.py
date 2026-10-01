import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trade_helper import position_chart_snapshot
from trade_helper.config import local_user_id
from trade_helper.db import connect, init_db
from trade_helper.timeframes import FIXED_SECONDS

MARKET = "binance:perp:SOLUSDT"
AS_OF = datetime(2026, 9, 30, 13, 16, tzinfo=UTC)


def candle(opened, timeframe="1h"):
    step = timedelta(seconds=FIXED_SECONDS[timeframe])
    return {"open_time": opened.isoformat(),
            "close_time": (opened + step - timedelta(milliseconds=1)).isoformat(),
            "open": "120", "high": "123", "low": "118", "close": "121", "volume": "30"}


def archive(*, count=1000, timeframe="1h"):
    hours = FIXED_SECONDS[timeframe] // 3600
    opening = AS_OF.replace(hour=AS_OF.hour // hours * hours, minute=0)
    quote = {"price": "121.2", "observed_at": AS_OF.isoformat(),
             "forming_candle": candle(opening, timeframe) | {"fetched_at": AS_OF.isoformat()}}
    snapshot = {"candles": [candle(opening - timedelta(hours=(count - index) * hours),
                                  timeframe) for index in range(count)],
                "quote": quote, "positions": [{"private": "must-not-be-returned"}],
                "news": {"private": "not-chart-data"}}
    request = {"market_id": MARKET, "timeframe": timeframe, "kind": "positions"}
    report = {"market_id": MARKET, "timeframe": timeframe,
              "quote": {"price": "121.20", "observed_at": AS_OF.isoformat()},
              "metrics": {"levels": [{"kind": "resistance", "low": "121", "high": "124"}]}}
    return snapshot, request, report


def save_archive(snapshot, request, report, *, analysis_id="analysis", user_id=None,
                 status="completed", include_hash=True):
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")) if snapshot else None
    if include_hash and encoded and report:
        report["market_snapshot_sha256"] = sha256(encoded.encode()).hexdigest()
    with connect() as db:
        db.execute(
            "INSERT INTO analyses(id,user_id,idempotency_key,request_hash,request_json,"
            "status,phase,report_json,snapshot_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (analysis_id, user_id or local_user_id(), analysis_id, "fixture",
             json.dumps(request), status, "done", json.dumps(report) if report else None,
             encoded, AS_OF.isoformat()),
        )
    return encoded


@pytest.fixture
def client():
    init_db()
    app = FastAPI()
    app.include_router(position_chart_snapshot.router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("timeframe", ["1h", "4h", "12h", "1d"])
def test_archive_chart_matches_frozen_quote_and_only_returns_chart_fields(client, timeframe):
    snapshot, request, report = archive(timeframe=timeframe)
    saved = save_archive(snapshot, request, report)
    response = client.get("/api/v1/analyses/analysis/chart-snapshot")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()
    assert result["status"] == "ready"
    assert result["quote"] == {"price": "121.2", "observed_at": AS_OF.isoformat()}
    assert result["as_of"] == AS_OF.isoformat()
    assert result["timeframe"] == timeframe
    assert result["market_snapshot_sha256"] == report["market_snapshot_sha256"]
    assert result["closed_candle_count"] == 300
    assert result["available_closed_candle_count"] == 1000
    assert len(result["chart_candles"]) == 301
    assert result["chart_candles"][0]["open_time"] == snapshot["candles"][-300]["open_time"]
    assert all(item["closed"] for item in result["chart_candles"][:-1])
    assert result["chart_candles"][-1]["closed"] is False
    assert "fetched_at" not in result["chart_candles"][-1]
    assert "must-not-be-returned" not in response.text
    assert "levels" not in result  # Archived report owns these; never recalculate.
    with connect(readonly=True) as db:
        after = db.execute("SELECT snapshot_json,report_json FROM analyses WHERE id=?",
                           ("analysis",)).fetchone()
    assert after["snapshot_json"] == saved
    assert json.loads(after["report_json"]) == report


def test_archive_endpoint_is_readonly_offline_and_never_loads_credentials(client, monkeypatch):
    save_archive(*archive())
    original = position_chart_snapshot.connect
    calls = []

    def read_only(*args, **kwargs):
        assert kwargs == {"readonly": True}
        calls.append(kwargs)
        return original(*args, **kwargs)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A stored chart must not access network or credentials")

    monkeypatch.setattr(position_chart_snapshot, "connect", read_only)
    monkeypatch.setattr("httpx.get", forbidden)
    monkeypatch.setattr("trade_helper.credential_store.load_credentials", forbidden)
    monkeypatch.setattr("trade_helper.market.fetch_candles", forbidden)
    monkeypatch.setattr("trade_helper.market.fetch_quote", forbidden)
    assert client.get("/api/v1/analyses/analysis/chart-snapshot").json()["status"] == "ready"
    assert calls == [{"readonly": True}]


def test_another_user_cannot_read_a_chart_and_unknown_ids_have_identical_404(client):
    save_archive(*archive(), user_id="different-user")
    other = client.get("/api/v1/analyses/analysis/chart-snapshot")
    missing = client.get("/api/v1/analyses/missing/chart-snapshot")
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json() == {"detail": "Analysis not found"}


@pytest.mark.parametrize("snapshot_missing,report_missing,status,reason", [
    (True, False, "completed", "SNAPSHOT_NOT_SAVED"),
    (False, True, "completed", "REPORT_NOT_SAVED"),
    (False, False, "running", "ANALYSIS_NOT_COMPLETED"),
])
def test_missing_archived_evidence_is_explicit_without_live_replacement(
    client, snapshot_missing, report_missing, status, reason,
):
    snapshot, request, report = archive()
    save_archive(None if snapshot_missing else snapshot, request,
                 None if report_missing else report, status=status)
    result = client.get("/api/v1/analyses/analysis/chart-snapshot").json()
    assert result["status"] == "unavailable"
    assert result["reason"] == reason
    assert result["chart_candles"] == []
    assert "quote" not in result


@pytest.mark.parametrize("field", ["market", "timeframe", "quote", "time", "hash"])
def test_a_report_cannot_silently_mix_another_snapshot(client, field):
    snapshot, request, report = archive()
    if field == "market":
        report["market_id"] = "binance:perp:BTCUSDT"
    elif field == "timeframe":
        report["timeframe"] = "4h"
    elif field == "quote":
        report["quote"]["price"] = "130"
    elif field == "time":
        report["quote"]["observed_at"] = (AS_OF + timedelta(minutes=1)).isoformat()
    elif field == "hash":
        report["market_snapshot_sha256"] = "0" * 64
    save_archive(snapshot, request, report, include_hash=field != "hash")
    result = client.get("/api/v1/analyses/analysis/chart-snapshot").json()
    assert result["status"] == "unavailable"
    assert result["reason"] == "SNAPSHOT_REPORT_MISMATCH"
    assert result["chart_candles"] == []


@pytest.mark.parametrize("case", ["future", "gap", "nan", "bad_timeframe", "naive"])
def test_invalid_or_future_archive_rows_never_enter_a_chart(client, case):
    snapshot, request, report = archive()
    if case == "future":
        snapshot["candles"].append(deepcopy(snapshot["quote"]["forming_candle"]))
    elif case == "gap":
        del snapshot["candles"][-3]
    elif case == "nan":
        snapshot["candles"][-1]["close"] = "NaN"
    elif case == "bad_timeframe":
        request["timeframe"] = report["timeframe"] = "1m"
    elif case == "naive":
        snapshot["candles"][-1]["open_time"] = "2026-09-30T12:00:00"
    save_archive(snapshot, request, report)
    result = client.get("/api/v1/analyses/analysis/chart-snapshot").json()
    assert result["status"] == "unavailable"
    assert result["reason"] == "SNAPSHOT_INVALID"


@pytest.mark.parametrize("case", ["missing", "already_closed", "fetched_after_quote", "bad_ohlc"])
def test_an_unusable_forming_row_is_omitted_without_fabricating_a_current_candle(client, case):
    snapshot, request, report = archive()
    if case == "missing":
        snapshot["quote"].pop("forming_candle")
    elif case == "already_closed":
        snapshot["quote"]["forming_candle"] = deepcopy(snapshot["candles"][-1])
    elif case == "fetched_after_quote":
        snapshot["quote"]["forming_candle"]["fetched_at"] = (
            AS_OF + timedelta(seconds=1)).isoformat()
    elif case == "bad_ohlc":
        snapshot["quote"]["forming_candle"]["high"] = "100"
    save_archive(snapshot, request, report)
    result = client.get("/api/v1/analyses/analysis/chart-snapshot").json()
    assert result["status"] == "ready"
    assert result["forming_candle_status"] == "unavailable"
    assert len(result["chart_candles"]) == 300
    assert all(item["closed"] for item in result["chart_candles"])


def test_old_partial_archive_without_hash_or_volume_still_has_its_original_prices(client):
    snapshot, request, report = archive(count=80)
    snapshot["quote"].pop("forming_candle")
    for item in snapshot["candles"]:
        item.pop("volume")
    save_archive(snapshot, request, report, include_hash=False)
    result = client.get("/api/v1/analyses/analysis/chart-snapshot").json()
    assert result["status"] == "ready"
    assert len(result["chart_candles"]) == 80
    assert all("volume" not in item for item in result["chart_candles"])


def test_unreadable_json_has_safe_status_and_does_not_expose_archive_payload(client):
    save_archive(*archive())
    with connect() as db:
        db.execute("UPDATE analyses SET snapshot_json=? WHERE id=?",
                   ('{"private":"do-not-leak",', "analysis"))
    response = client.get("/api/v1/analyses/analysis/chart-snapshot")
    assert response.json()["reason"] == "SNAPSHOT_INVALID"
    assert "do-not-leak" not in response.text
