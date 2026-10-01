from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from trade_helper.market import MAIN_HISTORY_LIMIT, fetch_candles

MARKET = "binance:perp:BTCUSDT"


def binance_row(opened):
    opened_ms = int(opened.timestamp() * 1000)
    return [opened_ms, "100", "101", "99", "100", "10", opened_ms + 3_600_000 - 1]


class Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_1000_closed_lookback_fills_exchange_cap_including_current_candle(monkeypatch):
    opened = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    newest = [binance_row(opened - timedelta(hours=MAIN_HISTORY_LIMIT - 1 - index))
              for index in range(MAIN_HISTORY_LIMIT)]
    older = binance_row(opened - timedelta(hours=MAIN_HISTORY_LIMIT))
    calls = []

    def get(_url, *, params, timeout):
        calls.append(params)
        assert timeout == 12
        assert params["symbol"] == "BTCUSDT" and params["interval"] == "1h"
        if "endTime" in params:
            assert params["limit"] == 1
            assert params["endTime"] == newest[0][0] - 1
            return Response([older])
        assert params["limit"] == 1000
        return Response(newest)

    monkeypatch.setattr("trade_helper.market.httpx.get", get)
    result = fetch_candles(MARKET, "1h", MAIN_HISTORY_LIMIT)
    assert len(result) == MAIN_HISTORY_LIMIT
    assert len(calls) == 2
    assert result[0]["open_time"] == (opened - timedelta(hours=1000)).isoformat()
    assert datetime.fromisoformat(result[-1]["close_time"]) < opened
    for previous, current in pairwise(result):
        assert datetime.fromisoformat(current["open_time"]) - datetime.fromisoformat(
            previous["open_time"]) == timedelta(hours=1)


def test_1000_already_closed_rows_do_not_need_an_older_request(monkeypatch):
    opened = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    data = [binance_row(opened - timedelta(hours=MAIN_HISTORY_LIMIT - index))
            for index in range(MAIN_HISTORY_LIMIT)]
    calls = []

    def get(_url, *, params, timeout):
        calls.append(params)
        assert "endTime" not in params
        return Response(data)

    monkeypatch.setattr("trade_helper.market.httpx.get", get)
    result = fetch_candles(MARKET, "1h", MAIN_HISTORY_LIMIT)
    assert len(result) == MAIN_HISTORY_LIMIT
    assert len(calls) == 1


def test_new_market_partial_history_remains_partial_without_invented_rows(monkeypatch):
    opened = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    data = [binance_row(opened - timedelta(hours=70 - index)) for index in range(71)]
    calls = []

    def get(_url, *, params, timeout):
        calls.append(params)
        assert "endTime" not in params
        return Response(data)

    monkeypatch.setattr("trade_helper.market.httpx.get", get)
    result = fetch_candles(MARKET, "1h", MAIN_HISTORY_LIMIT)
    assert len(result) == 70
    assert len(calls) == 1


@pytest.mark.parametrize("limit", [0, 1001, -1, True])
def test_invalid_lookback_is_rejected_without_network(monkeypatch, limit):
    monkeypatch.setattr("trade_helper.market.httpx.get", lambda *_args, **_kwargs: pytest.fail("unexpected network"))
    with pytest.raises(ValueError, match="limit"):
        fetch_candles(MARKET, "1h", limit)
