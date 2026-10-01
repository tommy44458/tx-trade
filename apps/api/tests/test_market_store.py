from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.db import connect
from trade_helper.market import normalize_candle
from trade_helper.market_store import cached_candles, init_market_store, validate_candle

pytestmark = pytest.mark.usefixtures("pg_schema")


def test_volume_fields_are_preserved_and_directional_volume_validated():
    candle = normalize_candle([1767225600000, "100", "102", "99", "101", "1",
                               1767225659999, "100.5", 5, "0.6", "60.3", "0"])
    assert candle["quote_volume"] == "100.5"
    assert candle["taker_buy_quote_volume"] == "60.3"
    assert candle["trade_count"] == 5
    validate_candle(candle, "1m")
    candle["taker_buy_quote_volume"] = "101"
    with pytest.raises(ValueError, match="directional"):
        validate_candle(candle, "1m")


def test_cache_reuses_verified_pages_and_detects_corruption(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    init_market_store()
    calls = []

    def fetch(market, tf, start, end):
        calls.append((start, end))
        ms = int(start.timestamp() * 1000)
        return [normalize_candle([ms, "100", "102", "99", "101", "1", ms + 59999,
                                  "100.5", 5, "0.6", "60.3", "0"])]

    monkeypatch.setattr("trade_helper.market_store.fetch_candles_range", fetch)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    first = cached_candles("BTC", "1m", start, end)
    assert cached_candles("BTC", "1m", start, end) == first
    assert len(calls) == 1
    with connect() as db:
        db.execute("UPDATE market_candles_v4 SET sha256='corrupt'")
        db.commit()
    with pytest.raises(ValueError, match="checksum"):
        cached_candles("BTC", "1m", start, end)
