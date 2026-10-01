import hashlib
import hmac
import json
import traceback
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from trade_helper import binance
from trade_helper.bingx_sync import EXCHANGE_FIELDS
from trade_helper.market_catalog import MarketCatalogUnavailable

CREDENTIALS = {"api_key": "test-binance-key", "api_secret": "test-binance-secret"}
POSITION = {
    "symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0.0200",
    "entryPrice": "90000.1234", "markPrice": "90100.4321", "unRealizedProfit": "2.006174",
    "liquidationPrice": "81234.0000", "marginAsset": "USDT", "updateTime": 1720736417660,
}
CONFIG = {"symbol": "BTCUSDT", "leverage": 10, "marginType": "ISOLATED"}
ORDINARY_ACCOUNT = {"isFutureEnabled": True, "isPortfolioMarginRetailEnabled": False}
PERMISSIONS = {"enableReading": True, "enableFixReadOnly": True,
               **{name: False for name in binance.WRITE_PERMISSION_FLAGS}}


def mock_requests(monkeypatch, responses):
    """Every HTTP operation is a mock, including Binance's public clock."""
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if not responses:
            pytest.fail("Unexpected network request")
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload, headers = item if len(item) == 3 else (*item, {})
        return httpx.Response(status, json=payload, headers=headers, request=httpx.Request("GET", url))

    monkeypatch.setattr(binance.httpx, "get", fake_get)
    return calls


def mock_snapshot_requests(monkeypatch, responses):
    return mock_requests(monkeypatch, [(200, ORDINARY_ACCOUNT), *responses])


@pytest.fixture(autouse=True)
def never_load_real_credentials(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Adapter tests must explicitly supply mocked credentials")

    monkeypatch.setattr(binance, "integration_credentials", forbidden)


def test_fetch_uses_only_three_signed_gets_and_normalizes_exact_decimals(monkeypatch):
    monkeypatch.setattr(binance.time, "time", lambda: 1700000000)
    calls = mock_snapshot_requests(monkeypatch, [(200, [POSITION]), (200, [CONFIG])])
    snapshot = binance.fetch_snapshot(credentials=CREDENTIALS)
    assert snapshot["closure_allowed"] is True
    assert snapshot["unsupported"] == 0 and snapshot["skipped_symbols"] == []
    assert datetime.fromisoformat(snapshot["started_at"]) <= datetime.fromisoformat(snapshot["completed_at"])
    assert len(calls) == 3
    assert [urlsplit(url).path for url, _ in calls] == [binance.ACCOUNT_ENDPOINT, binance.POSITION_ENDPOINT, binance.CONFIG_ENDPOINT]
    for url, options in calls:
        assert urlsplit(url).netloc == ("api.binance.com" if urlsplit(url).path == binance.ACCOUNT_ENDPOINT else "fapi.binance.com")
        assert options["headers"]["X-MBX-APIKEY"] == CREDENTIALS["api_key"]
        assert options["follow_redirects"] is False
        assert options["timeout"] == 12
        query = urlsplit(url).query
        signed, signature = query.rsplit("&signature=", 1)
        assert signature == hmac.new(CREDENTIALS["api_secret"].encode(), signed.encode(), hashlib.sha256).hexdigest()
        assert parse_qs(signed) == {"recvWindow": ["5000"], "timestamp": ["1700000000000"]}
        assert "symbol" not in parse_qs(signed)
        assert CREDENTIALS["api_secret"] not in url
    position = snapshot["positions"][0]
    assert set(EXCHANGE_FIELDS) <= position.keys()
    assert position["external_position_id"] == "BTCUSDT:BOTH"
    assert position["quantity"] == "0.0200"
    assert position["entry_price"] == "90000.1234"
    assert position["leverage"] == 10 and position["margin_mode"] == "isolated"
    assert position["mark_price"] == "90100.4321"
    assert position["unrealized_profit"] == "2.006174"
    assert position["exchange_update_time"] == "2024-07-11T22:20:17.660000+00:00"
    assert position["entry_time"] is None
    assert position["exchange_liquidation_price"] == "81234.0000"
    assert "stop_loss" not in position and "take_profit" not in position


@pytest.mark.parametrize("position_side,amount,expected", [
    ("BOTH", "-0.02", "short"), ("BOTH", "0.02", "long"),
    ("LONG", "0.02", "long"), ("SHORT", "-0.02", "short"),
])
def test_one_way_and_hedge_sides(position_side, amount, expected):
    row = {**POSITION, "positionSide": position_side, "positionAmt": amount}
    rows, unsupported, skipped = binance.normalize_positions([row], [CONFIG])
    assert rows[0]["side"] == expected and rows[0]["quantity"] == "0.02"
    assert rows[0]["external_position_id"] == f"BTCUSDT:{position_side}"
    assert unsupported == 0 and skipped == []


def test_both_hedge_legs_remain_distinct_and_use_symbol_config():
    rows = [{**POSITION, "positionSide": "LONG"},
            {**POSITION, "positionSide": "SHORT", "positionAmt": "-0.01"}]
    normalized, count, _ = binance.normalize_positions(rows, [{**CONFIG, "marginType": "CROSSED", "leverage": "21"}])
    assert count == 0
    assert [row["external_position_id"] for row in normalized] == ["BTCUSDT:LONG", "BTCUSDT:SHORT"]
    assert all(row["leverage"] == 21 and row["margin_mode"] == "cross" for row in normalized)


def test_zero_open_order_rows_are_not_positions_and_need_no_entry_or_config():
    rows, unsupported, skipped = binance.normalize_positions(
        [{"symbol": "BTCUSDT", "positionSide": "BOTH", "positionAmt": "0"}], [])
    assert rows == [] and unsupported == 0 and skipped == []


def test_complete_empty_snapshot_allows_closure(monkeypatch):
    calls = mock_snapshot_requests(monkeypatch, [(200, []), (200, [])])
    result = binance.fetch_snapshot(credentials=CREDENTIALS)
    assert result["positions"] == [] and result["closure_allowed"] is True
    assert len(calls) == 3


@pytest.mark.parametrize("account", [
    {**ORDINARY_ACCOUNT, "isPortfolioMarginRetailEnabled": True},
    {**ORDINARY_ACCOUNT, "isFutureEnabled": False},
])
def test_unsupported_account_mode_never_looks_like_no_positions(monkeypatch, account):
    calls = mock_requests(monkeypatch, [(200, account)])
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot(credentials=CREDENTIALS)
    assert caught.value.code == "UNSUPPORTED_ACCOUNT_MODE" and len(calls) == 1


@pytest.mark.parametrize("account", [
    {}, [], {"isFutureEnabled": True}, {"isPortfolioMarginRetailEnabled": False},
    {**ORDINARY_ACCOUNT, "isFutureEnabled": "true"},
    {**ORDINARY_ACCOUNT, "isPortfolioMarginRetailEnabled": 0},
])
def test_unverified_account_mode_prevents_reading_or_closing_positions(monkeypatch, account):
    calls = mock_requests(monkeypatch, [(200, account)])
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot(credentials=CREDENTIALS)
    assert caught.value.code == "ACCOUNT_MODE_UNVERIFIED" and len(calls) == 1


def test_unknown_usdt_usdc_and_delivery_positions_are_skipped_and_block_closure(monkeypatch):
    rows = [POSITION, *[{**POSITION, "symbol": name} for name in
                       ("NEWUSDT", "BTCUSDC", "BTCUSDT_250627")]]
    mock_snapshot_requests(monkeypatch, [(200, rows), (200, [CONFIG])])
    result = binance.fetch_snapshot(credentials=CREDENTIALS)
    assert len(result["positions"]) == 1
    assert result["unsupported"] == 3
    assert result["skipped_symbols"] == ["NEWUSDT", "BTCUSDC", "BTCUSDT_250627"]
    assert result["closure_allowed"] is False


def test_unknown_hedge_legs_count_positions_but_deduplicate_skipped_symbols():
    rows = [{**POSITION, "symbol": "NEWUSDT", "positionSide": "LONG"},
            {**POSITION, "symbol": "NEWUSDT", "positionSide": "SHORT", "positionAmt": "-1"}]
    positions, unsupported, skipped = binance.normalize_positions(rows, [])
    assert positions == [] and unsupported == 2 and skipped == ["NEWUSDT"]


@pytest.mark.parametrize("rows,configs,code", [
    ({"data": [POSITION]}, [CONFIG], "INVALID_RESPONSE"),
    ([POSITION], {"data": [CONFIG]}, "INVALID_CONFIG"),
    ([None], [CONFIG], "INVALID_POSITION"),
    ([POSITION], [None], "INVALID_CONFIG"),
    ([POSITION], [], "INVALID_CONFIG"),
    ([POSITION], [CONFIG, CONFIG], "INVALID_CONFIG"),
    ([POSITION, POSITION], [CONFIG], "INVALID_POSITION"),
    ([POSITION, {**POSITION, "positionSide": "LONG"}], [CONFIG], "INVALID_POSITION"),
])
def test_incomplete_or_ambiguous_snapshots_are_rejected(rows, configs, code):
    with pytest.raises(binance.BinanceError) as caught:
        binance.normalize_positions(rows, configs)
    assert caught.value.code == code


@pytest.mark.parametrize("field,value", [
    ("positionAmt", "NaN"), ("positionAmt", "Infinity"), ("positionAmt", True),
    ("positionAmt", None), ("entryPrice", "NaN"), ("entryPrice", "0"),
    ("entryPrice", "-1"), ("markPrice", "Infinity"), ("markPrice", "0"),
    ("unRealizedProfit", "NaN"), ("liquidationPrice", "-1"),
    ("liquidationPrice", "Infinity"), ("updateTime", -1), ("updateTime", True),
    ("updateTime", "1720736417660.5"), ("updateTime", "NaN"),
    ("updateTime", 253402300800000), ("symbol", "BTC-USDT"),
    ("symbol", "https://secret.invalid"), ("symbol", None),
    ("positionSide", "SELL"), ("positionSide", None), ("marginAsset", "USDC"),
])
def test_invalid_position_fields_reject_whole_snapshot(field, value):
    with pytest.raises(binance.BinanceError) as caught:
        binance.normalize_positions([{**POSITION, field: value}], [CONFIG])
    assert caught.value.code == "INVALID_POSITION"


@pytest.mark.parametrize("side,amount", [("LONG", "-1"), ("SHORT", "1")])
def test_hedge_side_and_signed_quantity_must_agree(side, amount):
    with pytest.raises(binance.BinanceError, match="inconsistent position"):
        binance.normalize_positions([{**POSITION, "positionSide": side, "positionAmt": amount}], [CONFIG])


@pytest.mark.parametrize("field,value", [
    ("leverage", None), ("leverage", True), ("leverage", "NaN"),
    ("leverage", "Infinity"), ("leverage", 0), ("leverage", -1),
    ("leverage", "1.5"), ("leverage", 126), ("marginType", None),
    ("marginType", "UNKNOWN"), ("marginType", []), ("symbol", None),
])
def test_invalid_symbol_settings_are_not_inferred_from_position(field, value):
    with pytest.raises(binance.BinanceError) as caught:
        binance.normalize_positions([POSITION], [{**CONFIG, field: value}])
    assert caught.value.code == "INVALID_CONFIG"


def test_optional_unknown_estimates_are_not_invented_and_zero_liquidation_is_none():
    row = deepcopy(POSITION)
    for field in ("markPrice", "unRealizedProfit", "updateTime"):
        row.pop(field)
    row["liquidationPrice"] = "0"
    result, _, _ = binance.normalize_positions([row], [CONFIG])
    assert result[0]["mark_price"] is None
    assert result[0]["unrealized_profit"] is None
    assert result[0]["exchange_update_time"] is None
    assert result[0]["exchange_liquidation_price"] is None
    assert result[0]["entry_time"] is None


def test_negative_pnl_is_a_valid_estimate():
    rows, _, _ = binance.normalize_positions([{**POSITION, "unRealizedProfit": "-12.3456"}], [CONFIG])
    assert rows[0]["unrealized_profit"] == "-12.3456"


@pytest.mark.parametrize("rows,configs", [([], []), ([POSITION], [CONFIG])])
def test_missing_verified_catalog_never_returns_closure_capable_snapshot(monkeypatch, rows, configs):
    def unavailable():
        raise MarketCatalogUnavailable()

    monkeypatch.setattr(binance, "get_catalog", unavailable)
    with pytest.raises(binance.BinanceError) as caught:
        binance.normalize_positions(rows, configs)
    assert caught.value.code == "MARKET_CATALOG_UNAVAILABLE"


@pytest.mark.parametrize("rows,configs", [([], []), ([POSITION], [CONFIG])])
def test_stale_catalog_cannot_make_a_closure_capable_snapshot(monkeypatch, rows, configs):
    stale = replace(binance.get_catalog(), status="stale")
    monkeypatch.setattr(binance, "get_catalog", lambda: stale)
    with pytest.raises(binance.BinanceError) as caught:
        binance.normalize_positions(rows, configs)
    assert caught.value.code == "MARKET_CATALOG_UNAVAILABLE"


def test_failed_second_endpoint_never_returns_partial_positions(monkeypatch):
    calls = mock_snapshot_requests(monkeypatch, [(200, [POSITION]), (503, {"msg": "arbitrary-private-text"})])
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot(credentials=CREDENTIALS)
    assert caught.value.code == "UPSTREAM_ERROR" and len(calls) == 3
    assert "arbitrary-private-text" not in str(caught.value)


@pytest.mark.parametrize("status,exchange_code,expected", [
    (401, -2015, "AUTHENTICATION_FAILED"), (403, None, "AUTHENTICATION_FAILED"),
    (200, -2014, "AUTHENTICATION_FAILED"), (400, -1022, "SIGNATURE_ERROR"),
    (429, -1003, "RATE_LIMITED"), (418, -1003, "IP_BANNED"),
    (408, None, "TIMEOUT"), (500, -9999, "UPSTREAM_ERROR"),
    (302, None, "UPSTREAM_ERROR"),
])
def test_safe_errors_never_echo_signed_urls_headers_or_api_messages(monkeypatch, status, exchange_code, expected):
    payload = {"code": exchange_code, "msg": f"{CREDENTIALS['api_key']} {CREDENTIALS['api_secret']} https://private.invalid?signature=private"}
    calls = mock_requests(monkeypatch, [(status, payload, {"Retry-After": "30"})])
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot(credentials=CREDENTIALS)
    error = caught.value
    assert error.code == expected and error.exchange_code == exchange_code
    assert error.retry_after == 30 and len(calls) == 1
    rendered = json.dumps(error.as_dict()) + "".join(traceback.format_exception(error))
    for private in (CREDENTIALS["api_key"], CREDENTIALS["api_secret"], "signature=", "https://private.invalid"):
        assert private not in rendered


@pytest.mark.parametrize("exception,code", [
    (httpx.ReadTimeout("secret-url?signature=hidden"), "TIMEOUT"),
    (httpx.ConnectError("secret-url?signature=hidden"), "NETWORK_ERROR"),
])
def test_transport_errors_are_sanitized_and_not_retried(monkeypatch, exception, code):
    calls = mock_requests(monkeypatch, [exception])
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot(credentials=CREDENTIALS)
    assert caught.value.code == code and len(calls) == 1
    assert "secret-url" not in "".join(traceback.format_exception(caught.value))


def test_non_json_response_is_safe(monkeypatch):
    def fake_get(url, **kwargs):
        return httpx.Response(200, text="test-binance-secret signature=private", request=httpx.Request("GET", url))

    monkeypatch.setattr(binance.httpx, "get", fake_get)
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot(credentials=CREDENTIALS)
    assert caught.value.code == "INVALID_RESPONSE"
    assert "test-binance-secret" not in "".join(traceback.format_exception(caught.value))


def test_timestamp_error_calibrates_once_and_retries_original_get(monkeypatch):
    monkeypatch.setattr(binance.time, "time", lambda: 1700000000)
    calls = mock_snapshot_requests(monkeypatch, [(400, {"code": -1021}),
                                      (200, {"serverTime": 1700000012345}),
                                      (200, [POSITION]), (200, [CONFIG])])
    result = binance.fetch_snapshot(credentials=CREDENTIALS)
    assert len(result["positions"]) == 1
    assert [urlsplit(url).path for url, _ in calls] == [binance.ACCOUNT_ENDPOINT, binance.POSITION_ENDPOINT, binance.TIME_ENDPOINT,
                                                    binance.POSITION_ENDPOINT, binance.CONFIG_ENDPOINT]
    clock_url, clock_options = calls[2]
    assert urlsplit(clock_url).query == ""
    assert "X-MBX-APIKEY" not in clock_options["headers"]
    for url, _ in calls[3:]:
        assert parse_qs(urlsplit(url).query)["timestamp"] == ["1700000012345"]


@pytest.mark.parametrize("responses", [
    [(400, {"code": -1021}), (200, {"serverTime": 1700000000000}), (400, {"code": -1021})],
    [(400, {"code": -1021}), (200, {"serverTime": 1700000000000}), (200, [POSITION]),
     (400, {"code": -1021})],
])
def test_clock_retry_is_bounded_once_across_snapshot(monkeypatch, responses):
    expected_calls = len(responses) + 1
    calls = mock_snapshot_requests(monkeypatch, responses)
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot(credentials=CREDENTIALS)
    assert caught.value.code == "TIMESTAMP_ERROR"
    assert len(calls) == expected_calls
    assert sum(urlsplit(url).path == binance.TIME_ENDPOINT for url, _ in calls) == 1


def test_account_mode_uses_same_clock_calibration_as_the_position_snapshot(monkeypatch):
    monkeypatch.setattr(binance.time, "time", lambda: 1700000000)
    calls = mock_requests(monkeypatch, [(400, {"code": -1021}),
                                      (200, {"serverTime": 1700000012345}),
                                      (200, ORDINARY_ACCOUNT), (200, [POSITION]), (200, [CONFIG])])
    assert len(binance.fetch_snapshot(credentials=CREDENTIALS)["positions"]) == 1
    assert [urlsplit(url).path for url, _ in calls] == [binance.ACCOUNT_ENDPOINT, binance.TIME_ENDPOINT,
                                                    binance.ACCOUNT_ENDPOINT, binance.POSITION_ENDPOINT,
                                                    binance.CONFIG_ENDPOINT]
    for url, _ in calls[2:]:
        assert parse_qs(urlsplit(url).query)["timestamp"] == ["1700000012345"]


def test_connection_clock_calibration_is_not_repeated_for_permission_check(monkeypatch):
    calls = mock_requests(monkeypatch, [(400, {"code": -1021}),
                                      (200, {"serverTime": 1700000000000}),
                                      (200, ORDINARY_ACCOUNT), (200, []), (200, []),
                                      (400, {"code": -1021})])
    result = binance.test_connection(credentials=CREDENTIALS)
    assert result["readable"] is True
    assert result["permissions"]["error_code"] == "TIMESTAMP_ERROR"
    assert sum(urlsplit(url).path == binance.TIME_ENDPOINT for url, _ in calls) == 1
    assert len(calls) == 6


@pytest.mark.parametrize("clock", [{"serverTime": "1700000000000"}, {"serverTime": True},
                                   {"serverTime": 0}, {"serverTime": -1}, []])
def test_invalid_clock_response_is_not_used_for_signed_retry(monkeypatch, clock):
    calls = mock_snapshot_requests(monkeypatch, [(400, {"code": -1021}), (200, clock)])
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot(credentials=CREDENTIALS)
    assert caught.value.code == "INVALID_RESPONSE" and len(calls) == 3


def test_private_allowlist_rejects_trading_and_unknown_routes_without_http(monkeypatch):
    calls = mock_requests(monkeypatch, [])
    session = binance._ReadOnlySession(CREDENTIALS)
    for endpoint in ("/fapi/v1/order", "/sapi/v1/capital/withdraw/apply", "https://other.invalid", "/fapi/v1/listenKey"):
        with pytest.raises(binance.BinanceError) as caught:
            session.get(endpoint)
        assert caught.value.code == "UNSUPPORTED_ENDPOINT"
    assert calls == []


def test_configured_reads_only_metadata_and_invocation_loads_credentials_once(monkeypatch):
    calls = []
    monkeypatch.setattr(binance, "integration_status", lambda name: {"configured": name == "binance"})
    assert binance.configured() is True

    def get_credentials(name):
        calls.append(name)
        return CREDENTIALS

    monkeypatch.setattr(binance, "integration_credentials", get_credentials)
    mock_snapshot_requests(monkeypatch, [(200, [POSITION]), (200, [CONFIG]), (200, PERMISSIONS)])
    result = binance.test_connection()
    assert result["readable"] is True and calls == ["binance"]


@pytest.mark.parametrize("credentials,expected", [
    ({}, "INVALID_CREDENTIALS"), ({"api_key": "key"}, "INVALID_CREDENTIALS"),
    ({"api_key": "key", "api_secret": " "}, "INVALID_CREDENTIALS"),
    ({"api_key": "key\n", "api_secret": "secret"}, "INVALID_CREDENTIALS"),
    ({"api_key": "key", "api_secret": 123}, "INVALID_CREDENTIALS"),
])
def test_invalid_credentials_make_no_request(monkeypatch, credentials, expected):
    calls = mock_requests(monkeypatch, [])
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot(credentials=credentials)
    assert caught.value.code == expected and calls == []


@pytest.mark.parametrize("field", ["api_key", "api_secret"])
@pytest.mark.parametrize("character", ["\x00", "\x07", "\x1b", "\x7f", "é", "金", "\ud800"])
def test_non_ascii_or_control_credentials_are_rejected_before_http(monkeypatch, field, character):
    calls = mock_requests(monkeypatch, [])
    credentials = {**CREDENTIALS, field: f"prefix{character}suffix"}
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot(credentials=credentials)
    assert caught.value.code == "INVALID_CREDENTIALS" and calls == []


def test_unconfigured_explicit_invocation_makes_no_request(monkeypatch):
    monkeypatch.setattr(binance, "integration_credentials", lambda name: None)
    calls = mock_requests(monkeypatch, [])
    with pytest.raises(binance.BinanceError) as caught:
        binance.fetch_snapshot()
    assert caught.value.code == "NOT_CONFIGURED" and calls == []


def test_permission_query_uses_signed_spot_get_and_only_safe_public_fields(monkeypatch):
    payload = {**PERMISSIONS, "createTime": 1700000000000, "accountId": "private-account",
               "msg": "test-binance-secret", "ipRestrict": True}
    calls = mock_requests(monkeypatch, [(200, payload)])
    result = binance.query_permissions(credentials=CREDENTIALS)
    assert result == {"verified": True, "read_only": True, "reading": True, "write_permissions": []}
    url, _ = calls[0]
    assert urlsplit(url).netloc == "api.binance.com"
    assert urlsplit(url).path == binance.PERMISSIONS_ENDPOINT
    assert "private-account" not in json.dumps(result) and "test-binance-secret" not in json.dumps(result)


@pytest.mark.parametrize("permission", binance.WRITE_PERMISSION_FLAGS)
def test_every_extra_permission_prevents_read_only_verdict(monkeypatch, permission):
    mock_requests(monkeypatch, [(200, {**PERMISSIONS, permission: True})])
    result = binance.query_permissions(credentials=CREDENTIALS)
    assert result == {"verified": True, "read_only": False, "reading": True,
                      "write_permissions": [permission]}


@pytest.mark.parametrize("mutation", [
    {"enableWithdrawals": None}, {"enableFutures": "false"},
    {"enableReading": "true"}, {"enableNewTradingPermission": True},
])
def test_missing_malformed_or_unknown_enabled_permissions_are_unverified(monkeypatch, mutation):
    mock_requests(monkeypatch, [(200, {**PERMISSIONS, **mutation})])
    result = binance.query_permissions(credentials=CREDENTIALS)
    assert result["verified"] is False and result["read_only"] is None
    assert result["write_permissions"] == []


def test_incomplete_permissions_still_disclose_known_write_permission(monkeypatch):
    mock_requests(monkeypatch, [(200, {"enableReading": True, "enableWithdrawals": True})])
    result = binance.query_permissions(credentials=CREDENTIALS)
    assert result == {"verified": False, "read_only": False, "reading": True,
                      "write_permissions": ["enableWithdrawals"]}


def test_reading_disabled_is_not_a_read_only_key(monkeypatch):
    mock_requests(monkeypatch, [(200, {**PERMISSIONS, "enableReading": False})])
    result = binance.query_permissions(credentials=CREDENTIALS)
    assert result["reading"] is False and result["read_only"] is False


def test_empty_permission_object_is_unverified(monkeypatch):
    mock_requests(monkeypatch, [(200, {})])
    assert binance.query_permissions(credentials=CREDENTIALS) == {
        "verified": False, "read_only": None, "reading": None, "write_permissions": []}


def test_connection_separates_readable_account_from_read_only_permission_check(monkeypatch):
    mock_snapshot_requests(monkeypatch, [(200, [POSITION]), (200, [CONFIG]),
                               (200, {**PERMISSIONS, "enableFutures": True})])
    result = binance.test_connection(credentials=CREDENTIALS)
    assert result["readable"] is True and result["active"] == 1
    assert result["active_symbols"] == ["BTCUSDT"]
    assert result["permissions"]["read_only"] is False
    assert "positions" not in result


@pytest.mark.parametrize("permission_response,expected", [
    ((401, {"code": -2015, "msg": "secret-account"}), "AUTHENTICATION_FAILED"),
    ((429, {"code": -1003}), "RATE_LIMITED"),
    ((200, []), "INVALID_RESPONSE"),
])
def test_permission_failure_keeps_successful_position_read_but_never_claims_read_only(monkeypatch, permission_response, expected):
    mock_snapshot_requests(monkeypatch, [(200, []), (200, []), permission_response])
    result = binance.test_connection(credentials=CREDENTIALS)
    assert result["readable"] is True and result["active"] == 0
    assert result["permissions"] == {"verified": False, "read_only": None, "reading": None,
                                     "write_permissions": [], "error_code": expected}
    assert "secret-account" not in json.dumps(result)


def test_connection_position_failure_is_not_swallowed_or_followed_by_permission_query(monkeypatch):
    calls = mock_requests(monkeypatch, [(401, {"code": -2015})])
    with pytest.raises(binance.BinanceError) as caught:
        binance.test_connection(credentials=CREDENTIALS)
    assert caught.value.code == "AUTHENTICATION_FAILED" and len(calls) == 1


def test_connection_account_info_failure_is_not_reported_as_readable(monkeypatch):
    calls = mock_requests(monkeypatch, [(200, {**ORDINARY_ACCOUNT, "isPortfolioMarginRetailEnabled": True})])
    with pytest.raises(binance.BinanceError) as caught:
        binance.test_connection(credentials=CREDENTIALS)
    assert caught.value.code == "UNSUPPORTED_ACCOUNT_MODE" and len(calls) == 1


def test_safe_error_constructor_cannot_echo_untrusted_caller_text():
    error = binance.BinanceError("unknown-secret-code", "raw-secret-msg", exchange_code="secret", retry_after="secret")
    assert error.as_dict() == {"code": "UPSTREAM_ERROR", "message": "Binance account access is temporarily unavailable.",
                               "exchange_code": None, "retry_after": None}


def test_update_time_is_not_entry_time():
    result, _, _ = binance.normalize_positions([{**POSITION, "updateTime": 1700000000000}], [CONFIG])
    assert result[0]["entry_time"] is None
    assert result[0]["exchange_update_time"] == datetime.fromtimestamp(1700000000, UTC).isoformat()
