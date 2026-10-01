import asyncio
import json
import time
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from trade_helper import discussion_market as live
from trade_helper.timeframes import FIXED_SECONDS, candle_open

NOW = datetime(2026, 10, 2, 10, 30, tzinfo=UTC)
CONTEXT = {
    "subject": {"type": "analysis", "market_id": "binance:perp:BTCUSDT", "timeframe": "1h"},
    "submitted_input": {"kind": "market", "market_id": "binance:perp:BTCUSDT", "timeframe": "1h"},
    "quote": {"price": "100.0000", "observed_at": "2026-09-30T13:16:00+00:00"},
    "original_report": {"support_zones": [{"lower": "99", "upper": "100"}]},
    "position_snapshot": [{"entry_price": "100", "quantity": "0.1"}],
}


def milliseconds(at):
    return int(at.timestamp() * 1000)


def ticker(price="101.12345678901234567890123456789", at=NOW, symbol="BTCUSDT"):
    return {"symbol": symbol, "price": price, "time": milliseconds(at)}


def candle_rows(timeframe="1h", at=NOW, *, count=10, forming=True):
    opened = candle_open(at, timeframe)
    if not forming:
        opened -= timedelta(seconds=FIXED_SECONDS[timeframe])
    rows = []
    for distance in reversed(range(count)):
        start = opened - timedelta(seconds=FIXED_SECONDS[timeframe] * distance)
        close = start + timedelta(seconds=FIXED_SECONDS[timeframe], milliseconds=-1)
        rows.append([milliseconds(start), "100.0000", "102.1000", "99.2000", "101.1234",
                     "12.3400", milliseconds(close), "1234.0000", 50, "6.1000", "610.0000", "0"])
    return rows


def mock_http(monkeypatch, quote=None, candles=None, *, handler=None):
    requests, options = [], []
    quote = ticker() if quote is None else quote
    candles = candle_rows() if candles is None else candles

    async def dispatch(request):
        requests.append(request)
        if handler is not None:
            return await handler(request)
        payload = quote if request.url.path == live.QUOTE_PATH else candles
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, tuple):
            status, body = payload
            return httpx.Response(status, json=body)
        return httpx.Response(200, json=payload)

    original = httpx.AsyncClient

    def client_factory(**kwargs):
        options.append(kwargs)
        return original(transport=httpx.MockTransport(dispatch), **kwargs)

    monkeypatch.setattr(live.httpx, "AsyncClient", client_factory)
    return requests, options


@pytest.fixture(autouse=True)
def fixed_observation_clock(monkeypatch):
    monkeypatch.setattr(live, "_now", lambda: NOW)


@pytest.fixture(autouse=True)
def forbidden_private_or_analysis_access(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Discussion observations may not use catalogs, accounts, indicators, or full analysis")

    monkeypatch.setattr("trade_helper.market_catalog.get_catalog", forbidden)
    monkeypatch.setattr("trade_helper.local_settings.integration_credentials", forbidden)
    monkeypatch.setattr("trade_helper.local_settings.binance_sync_credentials", forbidden)
    monkeypatch.setattr("trade_helper.market.fetch_quote", forbidden)
    monkeypatch.setattr("trade_helper.market.fetch_candles", forbidden)


def test_exact_fresh_quote_forming_and_eight_closed_bars_without_mutating_saved_context(monkeypatch):
    requests, options = mock_http(monkeypatch)
    before = deepcopy(CONTEXT)
    result = live.fetch_discussion_market(CONTEXT)
    assert CONTEXT == before
    assert result["version"] == "discussion_live_market_v1"
    assert result["status"] == "available" and result["errors"] == {}
    assert result["source"] == "binance_usdt_perpetual"
    assert result["market_id"] == "binance:perp:BTCUSDT" and result["timeframe"] == "1h"
    assert result["requested_at"] == NOW.isoformat() and result["observed_at"] == NOW.isoformat()
    assert result["quote"] == {"price": "101.12345678901234567890123456789",
                               "observed_at": NOW.isoformat(), "exchange_at": NOW.isoformat(), "tick_size": None}
    assert len(result["recent_closed_candles"]) == 8
    assert all(row["is_closed"] is True for row in result["recent_closed_candles"])
    assert result["recent_closed_candles"][-1]["close_time"] == "2026-10-02T09:59:59.999000+00:00"
    assert result["forming_candle"]["open_time"] == "2026-10-02T10:00:00+00:00"
    assert result["forming_candle"]["is_closed"] is False
    assert result["forming_candle"]["volume"] == "12.3400"
    assert Decimal(result["comparison"]["change_since_analysis_pct"]) == Decimal("1.12345678901234567890123456789")
    assert result["comparison"]["analysis_price"] == "100.0000"
    assert result["not_refreshed"] == ["support_resistance", "indicators", "higher_timeframes", "positions", "macro"]
    assert result["data_basis"] == "new_public_market_observation"
    assert len(requests) == 2
    assert live.QUOTE_PATH == "/fapi/v2/ticker/price"
    assert {request.url.path for request in requests} == {live.QUOTE_PATH, live.CANDLES_PATH}
    assert options == [{"follow_redirects": False, "trust_env": False}]
    for request in requests:
        assert request.method == "GET" and request.url.host == "fapi.binance.com"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert "X-MBX-APIKEY" not in request.headers
        assert "Authorization" not in request.headers and "Cookie" not in request.headers
        assert "signature" not in request.url.params
    candle_request = next(request for request in requests if request.url.path == live.CANDLES_PATH)
    assert candle_request.url.params == httpx.QueryParams({"symbol": "BTCUSDT", "interval": "1h", "limit": 10})


@pytest.mark.parametrize("timeframe", ["1h", "4h", "12h", "1d"])
def test_only_frozen_main_timeframe_is_requested(monkeypatch, timeframe):
    context = deepcopy(CONTEXT)
    context["subject"]["timeframe"] = timeframe
    context["submitted_input"]["timeframe"] = timeframe
    requests, _ = mock_http(monkeypatch, candles=candle_rows(timeframe))
    result = live.fetch_discussion_market(context)
    assert result["status"] == "available" and result["timeframe"] == timeframe
    assert result["forming_candle"]["open_time"] == candle_open(NOW, timeframe).isoformat()
    assert len(requests) == 2
    assert [request.url.params["interval"] for request in requests if request.url.path == live.CANDLES_PATH] == [timeframe]


@pytest.mark.parametrize("context", [
    {}, None, {"subject": {"type": "macro"}, "submitted_input": CONTEXT["submitted_input"]},
    {"subject": {"type": "analysis"}}, {"subject": "analysis"},
    {"subject": {**CONTEXT["subject"], "market_id": "https://secret.invalid"}},
    {"subject": {**CONTEXT["subject"], "market_id": "binance:perp:BTC-USDT"}},
    {"subject": {**CONTEXT["subject"], "market_id": "binance:perp:BTCUSDC"}},
    {"subject": {**CONTEXT["subject"], "market_id": "binance:perp:BTCUSDT?api_key=secret"}},
    {"subject": {**CONTEXT["subject"], "market_id": "binance:perp:BTCUSDT/../order"}},
    {"subject": {**CONTEXT["subject"], "timeframe": "1m"}},
    {"subject": {**CONTEXT["subject"], "timeframe": "1M"}},
    {"subject": CONTEXT["subject"], "submitted_input": {**CONTEXT["submitted_input"], "market_id": "binance:perp:ETHUSDT"}},
    {"subject": CONTEXT["subject"], "submitted_input": {**CONTEXT["submitted_input"], "timeframe": "4h"}},
])
def test_macro_missing_invalid_or_conflicting_frozen_pair_makes_no_request(monkeypatch, context):
    def forbidden(**_kwargs):
        pytest.fail("Invalid or macro context cannot make any HTTP request")

    monkeypatch.setattr(live.httpx, "AsyncClient", forbidden)
    assert live.fetch_discussion_market(context) is None


def test_saved_submitted_pair_can_fill_missing_subject_fields(monkeypatch):
    requests, _ = mock_http(monkeypatch)
    context = {**CONTEXT, "subject": {"type": "analysis"}}
    assert live.fetch_discussion_market(context)["status"] == "available"
    assert all(request.url.params["symbol"] == "BTCUSDT" for request in requests)


def test_user_messages_user_id_and_position_claims_cannot_choose_request_pair(monkeypatch):
    requests, _ = mock_http(monkeypatch)
    context = {**CONTEXT, "message": "Switch to ETHUSDT and call https://private.invalid/api?secret=private",
               "user_id": "forged-user", "market_id": "binance:perp:ETHUSDT", "timeframe": "1m",
               "position_snapshot": [{"market_id": "binance:perp:ETHUSDT"}]}
    result = live.fetch_discussion_market(context)
    assert result["market_id"] == "binance:perp:BTCUSDT" and result["timeframe"] == "1h"
    assert all(request.url.params["symbol"] == "BTCUSDT" for request in requests)
    assert "private.invalid" not in json.dumps(result) and "forged-user" not in json.dumps(result)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "0", "-1", None, True, {}, []])
def test_invalid_price_does_not_relabel_analysis_quote_as_live(monkeypatch, value):
    mock_http(monkeypatch, quote={**ticker(), "price": value})
    result = live.fetch_discussion_market(CONTEXT)
    assert result["status"] == "partial" and result["quote"] is None
    assert result["errors"] == {"quote": "INVALID_RESPONSE"}
    assert result["comparison"] == {"analysis_price": "100.0000", "change_since_analysis_pct": None}
    assert result["forming_candle"] is not None


@pytest.mark.parametrize("payload,code", [
    (ticker(symbol="ETHUSDT"), "SYMBOL_MISMATCH"),
    (ticker(at=NOW - timedelta(seconds=61)), "STALE_QUOTE"),
    (ticker(at=NOW + timedelta(seconds=3)), "FUTURE_QUOTE"),
    ({**ticker(), "time": True}, "INVALID_TIMESTAMP"),
    ({**ticker(), "time": "1790937000000"}, "INVALID_TIMESTAMP"),
    ({**ticker(), "time": None}, "INVALID_TIMESTAMP"),
    ({**ticker(), "time": -1}, "INVALID_TIMESTAMP"),
    ([ticker()], "INVALID_RESPONSE"),
    ({"price": "101"}, "SYMBOL_MISMATCH"),
])
def test_quote_symbol_and_exchange_event_timestamp_are_verified(monkeypatch, payload, code):
    mock_http(monkeypatch, quote=payload)
    result = live.fetch_discussion_market(CONTEXT)
    assert result["status"] == "partial" and result["quote"] is None
    assert result["errors"] == {"quote": code}


def test_missing_exchange_timestamp_is_explicitly_unknown_not_retrieval_time(monkeypatch):
    mock_http(monkeypatch, quote={"symbol": "BTCUSDT", "price": "101.00"})
    result = live.fetch_discussion_market(CONTEXT)
    assert result["quote"]["exchange_at"] is None
    assert result["quote"]["observed_at"] == NOW.isoformat()


def test_no_forming_bar_at_exact_boundary_is_legitimate(monkeypatch):
    boundary = candle_open(NOW, "1h")
    monkeypatch.setattr(live, "_now", lambda: boundary)
    mock_http(monkeypatch, quote=ticker(at=boundary), candles=candle_rows(at=boundary, forming=False))
    result = live.fetch_discussion_market(CONTEXT)
    assert result["status"] == "available" and result["errors"] == {}
    assert result["forming_candle"] is None and len(result["recent_closed_candles"]) == 8
    assert result["recent_closed_candles"][-1]["close_time"] == "2026-10-02T09:59:59.999000+00:00"


def test_last_millisecond_before_boundary_remains_unconfirmed(monkeypatch):
    before = candle_open(NOW, "1h") + timedelta(seconds=3600, microseconds=-500)
    monkeypatch.setattr(live, "_now", lambda: before)
    mock_http(monkeypatch, quote=ticker(at=before), candles=candle_rows(at=before))
    result = live.fetch_discussion_market(CONTEXT)
    assert result["forming_candle"]["is_closed"] is False
    assert all(row["close_time"] != "2026-10-02T10:59:59.999000+00:00" for row in result["recent_closed_candles"])


@pytest.mark.parametrize("mutation,code", [
    (lambda rows: [], "EMPTY_CANDLES"),
    (lambda rows: {"data": rows}, "INVALID_CANDLES"),
    (lambda rows: rows + [rows[-1]], "INVALID_CANDLES"),
    (lambda rows: rows[:3] + rows[4:], "CANDLE_GAP"),
    (lambda rows: rows[:3] + [rows[2]] + rows[4:], "CANDLE_GAP"),
    (lambda rows: list(reversed(rows)), "CANDLE_GAP"),
    (lambda rows: candle_rows(at=NOW - timedelta(hours=2)), "STALE_CANDLES"),
    (lambda rows: candle_rows(forming=False), "STALE_CANDLES"),
    (lambda rows: candle_rows(at=NOW + timedelta(hours=1)), "INVALID_CANDLES"),
])
def test_empty_stale_future_duplicate_or_discontinuous_candles_do_not_erase_good_quote(monkeypatch, mutation, code):
    mock_http(monkeypatch, candles=mutation(candle_rows()))
    result = live.fetch_discussion_market(CONTEXT)
    assert result["status"] == "partial" and result["quote"] is not None
    assert result["forming_candle"] is None and result["recent_closed_candles"] == []
    assert result["errors"] == {"candles": code}


@pytest.mark.parametrize("index,value", [
    (0, True), (0, "1790935200000"), (0, -1), (6, True),
    (1, "NaN"), (2, "Infinity"), (3, "0"), (4, "-1"),
    (2, "99"), (3, "101"), (5, "-1"), (5, "NaN"),
    (7, "Infinity"), (8, True), (8, -1), (8, "50"),
    (9, "100"), (10, "5000"),
])
def test_ohlcv_timebounds_and_optional_trade_values_are_validated(monkeypatch, index, value):
    rows = candle_rows()
    rows[-1][index] = value
    mock_http(monkeypatch, candles=rows)
    result = live.fetch_discussion_market(CONTEXT)
    assert result["status"] == "partial" and result["quote"] is not None
    assert result["forming_candle"] is None and result["recent_closed_candles"] == []
    assert result["errors"]["candles"] in ("INVALID_CANDLES", "INVALID_RESPONSE", "INVALID_TIMESTAMP")


@pytest.mark.parametrize("change", ["unaligned_open", "wrong_close", "truncated_row"])
def test_candle_interval_must_match_frozen_timeframe(monkeypatch, change):
    rows = candle_rows()
    if change == "unaligned_open":
        rows[-1][0] += 1000
    elif change == "wrong_close":
        rows[-1][6] += 1000
    else:
        rows[-1] = rows[-1][:7]
    mock_http(monkeypatch, candles=rows)
    result = live.fetch_discussion_market(CONTEXT)
    assert result["errors"] == {"candles": "INVALID_CANDLES"}


@pytest.mark.parametrize("payload,code", [
    (httpx.ReadTimeout("secret-url?signature=private"), "TIMEOUT"),
    (httpx.ConnectError("secret-url?signature=private"), "NETWORK_ERROR"),
    ((429, {"msg": "private response"}), "RATE_LIMITED"),
    ((418, {"msg": "private response"}), "IP_BANNED"),
    ((400, {"code": -1121, "msg": "private response"}), "HTTP_ERROR"),
    ((500, {"msg": "private response"}), "HTTP_ERROR"),
    ((302, {"msg": "private response"}), "HTTP_ERROR"),
])
def test_component_errors_are_sanitized_and_never_retried(monkeypatch, payload, code):
    requests, _ = mock_http(monkeypatch, quote=payload)
    result = live.fetch_discussion_market(CONTEXT)
    assert result["status"] == "partial" and result["errors"] == {"quote": code}
    assert len(requests) == 2
    rendered = json.dumps(result)
    assert "secret-url" not in rendered and "signature=" not in rendered and "private response" not in rendered


def test_two_failed_components_leave_saved_quote_only_in_comparison(monkeypatch):
    mock_http(monkeypatch, quote=(400, {"code": -1121}), candles=(400, {"code": -1121}))
    result = live.fetch_discussion_market(CONTEXT)
    assert result["status"] == "unavailable" and result["quote"] is None
    assert result["observed_at"] is None and result["forming_candle"] is None
    assert result["recent_closed_candles"] == []
    assert result["errors"] == {"quote": "HTTP_ERROR", "candles": "HTTP_ERROR"}
    assert result["comparison"] == {"analysis_price": "100.0000", "change_since_analysis_pct": None}


def test_slow_components_share_one_hard_deadline_and_are_cancelled(monkeypatch):
    cancelled = []

    async def handler(request):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled.append(request.url.path)
            raise

    requests, _ = mock_http(monkeypatch, handler=handler)
    started = time.monotonic()
    result = live.fetch_discussion_market(CONTEXT, timeout=0.04)
    elapsed = time.monotonic() - started
    assert elapsed < 0.4
    assert result["status"] == "unavailable"
    assert result["errors"] == {"quote": "TIMEOUT", "candles": "TIMEOUT"}
    assert len(requests) == 2 and len(cancelled) == 2
    for request in requests:
        assert 0 < request.extensions["timeout"]["read"] <= 0.04


def test_hard_deadline_preserves_already_received_quote(monkeypatch):
    async def handler(request):
        if request.url.path == live.QUOTE_PATH:
            return httpx.Response(200, json=ticker())
        await asyncio.sleep(1)

    requests, _ = mock_http(monkeypatch, handler=handler)
    result = live.fetch_discussion_market(CONTEXT, timeout=0.04)
    assert result["status"] == "partial" and result["quote"]["price"] == ticker()["price"]
    assert result["errors"] == {"candles": "TIMEOUT"} and len(requests) == 2


@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf"), True, None, "8"])
def test_invalid_or_exhausted_budget_never_starts_request(monkeypatch, budget):
    def forbidden(**_kwargs):
        pytest.fail("Exhausted observation budget cannot start a request")

    monkeypatch.setattr(live.httpx, "AsyncClient", forbidden)
    result = live.fetch_discussion_market(CONTEXT, timeout=budget)
    assert result["status"] == "unavailable" and result["errors"] == {"quote": "TIMEOUT", "candles": "TIMEOUT"}


def test_timeout_parameter_cannot_expand_the_maximum_total_budget(monkeypatch):
    observed = []

    async def collect(_symbol, _timeframe, deadline, results, errors):
        observed.append(deadline - time.monotonic())
        errors.update(quote="TIMEOUT", candles="TIMEOUT")

    monkeypatch.setattr(live, "_collect", collect)
    assert live.fetch_discussion_market(CONTEXT, timeout=1000)["status"] == "unavailable"
    assert 0 < observed[0] <= 8


@pytest.mark.parametrize("analysis_price", [None, "NaN", "Infinity", "0", "-1", True])
def test_invalid_saved_price_does_not_break_new_observation(monkeypatch, analysis_price):
    mock_http(monkeypatch)
    context = {**CONTEXT, "quote": {"price": analysis_price}}
    result = live.fetch_discussion_market(context)
    assert result["status"] == "available" and result["comparison"] is None


def test_comparison_can_use_original_saved_report_quote_when_context_quote_is_absent(monkeypatch):
    mock_http(monkeypatch)
    context = {"subject": CONTEXT["subject"], "original_report": {"quote": {"price": "100"}}}
    result = live.fetch_discussion_market(context)
    assert result["comparison"]["analysis_price"] == "100"


def test_negative_change_since_analysis_keeps_decimal_precision(monkeypatch):
    mock_http(monkeypatch, quote=ticker(price="99.9000"))
    result = live.fetch_discussion_market(CONTEXT)
    assert Decimal(result["comparison"]["change_since_analysis_pct"]) == Decimal("-0.1")


def test_non_json_and_nonfinite_json_bodies_are_safe(monkeypatch):
    async def handler(request):
        if request.url.path == live.QUOTE_PATH:
            return httpx.Response(200, text="private response api_key=private")
        return httpx.Response(200, text='[{"secret": NaN}]')

    mock_http(monkeypatch, handler=handler)
    result = live.fetch_discussion_market(CONTEXT)
    assert result["status"] == "unavailable"
    assert result["errors"] == {"quote": "INVALID_RESPONSE", "candles": "INVALID_RESPONSE"}
    assert "private" not in json.dumps(result)


def test_current_event_loop_call_fails_safely_without_exposing_runtime_errors(monkeypatch):
    requests, _ = mock_http(monkeypatch)

    async def caller():
        return live.fetch_discussion_market(CONTEXT)

    result = asyncio.run(caller())
    assert result["status"] == "unavailable"
    assert result["errors"] == {"quote": "NETWORK_ERROR", "candles": "NETWORK_ERROR"}
    assert requests == []
