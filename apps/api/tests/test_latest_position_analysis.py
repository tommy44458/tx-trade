"""Latest saved reports are selected without starting analysis or market reads."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from trade_helper import api
from trade_helper.config import local_user_id
from trade_helper.db import connect

MARKET = "binance:perp:BTCUSDT"
OTHER_MARKET = "binance:perp:SOLUSDT"
BASE_TIME = datetime(2026, 9, 30, 12, tzinfo=UTC)
MISSING = object()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("APP_LOCAL_USER_ID", "latest-report-owner")
    with TestClient(api.app) as test_client:
        yield test_client


def save_analysis(
    analysis_id,
    *,
    market_id=MARKET,
    kind="positions",
    timeframe="1h",
    created_offset=0,
    completed_offset=None,
    status="completed",
    user_id=None,
    request=MISSING,
    report=MISSING,
    positions=None,
):
    if request is MISSING:
        request = {
            "market_id": market_id,
            "kind": kind,
            "timeframe": timeframe,
            "position_ids": [position["id"] for position in positions or []],
            "output_locale": "en-US",
        }
    if report is MISSING:
        report = {
            "market_id": market_id,
            "analysis_kind": kind,
            "timeframe": timeframe,
            "quote": {"price": "120.12345678"},
            "reasoning": {"strategy": f"Saved advice for {analysis_id}"},
            "metrics": {"levels": []},
        }
    created_at = BASE_TIME + timedelta(seconds=created_offset)
    completed_at = BASE_TIME + timedelta(
        seconds=created_offset + 1 if completed_offset is None else completed_offset,
    )
    with connect() as db:
        db.execute(
            "INSERT INTO analyses(id,user_id,idempotency_key,request_hash,request_json,"
            "positions_json,status,phase,report_json,created_at,completed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                analysis_id, user_id or local_user_id(), analysis_id, "test-input",
                json.dumps(request), json.dumps(positions or []), status, "done",
                None if report is None else json.dumps(report),
                created_at.isoformat(), completed_at.isoformat(),
            ),
        )
    return request, report


def latest(client, market_id=MARKET, **params):
    return client.get("/api/v1/analyses/latest", params={"market_id": market_id, **params})


def test_empty_saved_history_returns_null_without_dynamic_route_404(client):
    response = latest(client)
    assert response.status_code == 200
    assert response.json() is None
    assert response.headers["cache-control"] == "no-store"


def test_latest_pair_report_is_found_beyond_fifty_unrelated_analyses(client):
    request, report = save_analysis("selected-old", timeframe="12h")
    for index in range(65):
        save_analysis(f"other-{index:03d}", market_id=OTHER_MARKET, created_offset=index + 1)
    assert len(client.get("/api/v1/analyses").json()) == 50
    response = latest(client)
    assert response.status_code == 200
    result = response.json()
    assert result["id"] == "selected-old"
    assert result["submitted_input"] == request
    assert result["report"] == report
    assert result["output_locale"] == "en-US"
    assert result["status"] == "completed"
    assert result["freshness"] == "fresh"
    assert result["stale_reasons"] == []
    assert result == client.get("/api/v1/analyses/selected-old").json()


def test_latest_uses_submitted_time_instead_of_slow_old_completion(client):
    save_analysis("older-slower", created_offset=0, completed_offset=120)
    save_analysis("latest-request", created_offset=60, completed_offset=61, timeframe="4h")
    result = latest(client).json()
    assert result["id"] == "latest-request"
    assert result["submitted_input"]["timeframe"] == "4h"


def test_market_kind_returns_the_pairs_newest_market_report_only(client):
    save_analysis("market-old", kind="market", created_offset=0, timeframe="1h")
    save_analysis("market-new", kind="market", created_offset=30, timeframe="12h")
    save_analysis("positions-newest", kind="positions", created_offset=60)
    save_analysis("other-pair-market", kind="market", market_id=OTHER_MARKET, created_offset=90)
    result = latest(client, kind="market").json()
    assert result["id"] == "market-new"
    assert result["submitted_input"]["kind"] == "market"
    assert result["report"]["timeframe"] == "12h"
    assert latest(client).json()["id"] == "positions-newest"
    assert latest(client, market_id=OTHER_MARKET, kind="market").json()["id"] == "other-pair-market"


def test_created_time_ties_have_stable_completion_and_id_order(client):
    save_analysis("a-earlier-completion", created_offset=10, completed_offset=11)
    save_analysis("a-later-completion", created_offset=10, completed_offset=12)
    save_analysis("z-later-completion", created_offset=10, completed_offset=12)
    assert latest(client).json()["id"] == "z-later-completion"
    assert latest(client).json()["id"] == "z-later-completion"


@pytest.mark.parametrize("excluded", [
    {"market_id": OTHER_MARKET},
    {"kind": "market"},
    {"user_id": "another-local-user"},
    {"status": "queued"},
    {"status": "running"},
    {"status": "failed"},
    {"report": None},
    {"report": []},
    {"report": "null"},
    {"report": {}},
    {"request": []},
    {"request": {}},
    {"report": {"market_id": OTHER_MARKET, "analysis_kind": "positions", "timeframe": "1h"}},
    {"report": {"market_id": MARKET, "analysis_kind": "market", "timeframe": "1h"}},
    {"report": {"market_id": MARKET, "analysis_kind": "positions", "timeframe": "4h"}},
    {"report": {"market_id": MARKET, "analysis_kind": None, "timeframe": "1h"}},
])
def test_newer_unusable_or_unrelated_jobs_do_not_replace_valid_report(client, excluded):
    save_analysis("valid-saved")
    save_analysis("invalid-newer", created_offset=100, **excluded)
    assert latest(client).json()["id"] == "valid-saved"


@pytest.mark.parametrize("column,payload", [
    ("request_json", "not valid JSON"),
    ("report_json", "not valid JSON"),
    ("report_json", "null"),
    ("report_json", "false"),
    ("report_json", "42"),
])
def test_corrupt_legacy_rows_are_skipped_instead_of_breaking_history(client, column, payload):
    save_analysis("valid-saved")
    save_analysis("invalid-newer", created_offset=100)
    with connect() as db:
        db.execute(f"UPDATE analyses SET {column}=? WHERE id=?", (payload, "invalid-newer"))
    response = latest(client)
    assert response.status_code == 200
    assert response.json()["id"] == "valid-saved"


@pytest.mark.parametrize("timeframe", ["1h", "4h", "12h", "1d"])
def test_legacy_report_without_analysis_kind_preserves_saved_timeframe(client, timeframe):
    report = {
        "market_id": MARKET,
        "timeframe": timeframe,
        "reasoning": {"strategy": "Legacy saved position advice"},
    }
    save_analysis("legacy-saved", timeframe=timeframe, report=report)
    result = latest(client).json()
    assert result["id"] == "legacy-saved"
    assert result["submitted_input"]["kind"] == "positions"
    assert result["report"] == report


def test_kind_filter_and_user_scope_are_independent(client):
    save_analysis("owner-position")
    save_analysis("owner-market", kind="market", created_offset=10)
    save_analysis("other-user-position", user_id="another-local-user", created_offset=20)
    assert latest(client).json()["id"] == "owner-position"
    assert latest(client, kind="market").json()["id"] == "owner-market"
    assert latest(client, market_id=OTHER_MARKET).json() is None


def test_saved_report_freshness_is_checked_against_current_position_version(client):
    saved_position = {"id": "position-original", "version": 1}
    save_analysis("saved-position-review", positions=[saved_position])
    with connect() as db:
        db.execute(
            "INSERT INTO positions(id,user_id,market_id,version,side,entry_price,quantity,"
            "status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (saved_position["id"], local_user_id(), MARKET, 2, "long", "100", "1", "open",
             BASE_TIME.isoformat(), BASE_TIME.isoformat()),
        )
    result = latest(client).json()
    assert result["id"] == "saved-position-review"
    assert result["freshness"] == "stale"
    assert result["stale_reasons"] == ["position_changed"]
    assert result == client.get("/api/v1/analyses/saved-position-review").json()


def test_lookup_is_readonly_offline_and_allows_saved_delisted_markets(client, monkeypatch):
    delisted_market = "binance:perp:DELISTEDUSDT"
    save_analysis("delisted-saved", market_id=delisted_market)
    with connect(readonly=True) as db:
        before = db.execute("SELECT * FROM analyses ORDER BY id").fetchall()
    original_connect = api.connect
    statements, connections = [], []

    @contextmanager
    def readonly_connection(*args, **kwargs):
        assert kwargs == {"readonly": True}
        connections.append(kwargs)
        with original_connect(*args, **kwargs) as db:
            assert db.readonly is True
            assert db.connection.execute("PRAGMA query_only").fetchone()["query_only"] == 1
            db.connection.set_trace_callback(statements.append)
            yield db

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Reading saved history must not use network or credentials")

    monkeypatch.setattr(api, "connect", readonly_connection)
    monkeypatch.setattr(api, "get_catalog", forbidden)
    monkeypatch.setattr(api, "validate_market_id", forbidden)
    monkeypatch.setattr(api, "fetch_candles", forbidden)
    monkeypatch.setattr(api, "fetch_quote", forbidden)
    monkeypatch.setattr("trade_helper.market_catalog.get_catalog", forbidden)
    monkeypatch.setattr("trade_helper.credential_store.load_credentials", forbidden)
    monkeypatch.setattr("trade_helper.model_providers.ModelSession", forbidden)
    monkeypatch.setattr("httpx.get", forbidden)
    response = latest(client, market_id=delisted_market)
    assert response.status_code == 200
    assert response.json()["id"] == "delisted-saved"
    assert connections == [{"readonly": True}]
    assert all(statement.lstrip().upper().startswith(("SELECT", "COMMIT"))
               for statement in statements)
    with connect(readonly=True) as db:
        assert db.execute("SELECT * FROM analyses ORDER BY id").fetchall() == before


@pytest.mark.parametrize("parameters", [
    {},
    {"market_id": "BTCUSDT"},
    {"market_id": "binance:perp:BTCUSDC"},
    {"market_id": "binance:perp:BTCUSDT?override=1"},
    {"market_id": MARKET, "kind": "unknown"},
])
def test_invalid_lookup_input_is_rejected_without_database_reads(client, monkeypatch, parameters):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Invalid lookup input must not touch the database")

    monkeypatch.setattr(api, "connect", forbidden)
    response = client.get("/api/v1/analyses/latest", params=parameters)
    assert response.status_code == 422


def test_direct_analysis_route_remains_available(client):
    save_analysis("specific-analysis")
    response = client.get("/api/v1/analyses/specific-analysis")
    assert response.status_code == 200
    assert response.json()["id"] == "specific-analysis"
