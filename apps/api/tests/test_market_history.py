from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.market import fetch_candles_range


def _row(open_ms: int, step_ms: int) -> list:
    return [open_ms, "100", "102", "98", "101", "10", open_ms + step_ms - 1]


def test_historical_fetch_requires_contiguous_closed_candles(monkeypatch) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=10)
    start_ms = int(start.timestamp() * 1000)

    class Response:
        def __init__(self, rows):
            self.rows = rows

        def raise_for_status(self):
            pass

        def json(self):
            return self.rows

    class Client:
        def __init__(self, rows, **_kwargs):
            self.rows = rows

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, _url, params):
            assert params["interval"] == "5m"
            return Response(self.rows)

    rows = [_row(start_ms, 300_000), _row(start_ms + 300_000, 300_000)]
    monkeypatch.setattr("trade_helper.market.httpx.Client", lambda **kwargs: Client(rows, **kwargs))
    result = fetch_candles_range("binance:perp:BTCUSDT", "5m", start, end)
    assert len(result) == 2
    assert result[1]["close_time"].endswith("00:09:59.999000+00:00")

    rows[1][0] += 300_000
    with pytest.raises(ValueError, match="gap"):
        fetch_candles_range("binance:perp:BTCUSDT", "5m", start, end)
