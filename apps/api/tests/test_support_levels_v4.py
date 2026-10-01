from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.support_levels_v4 import advance, new_state, snapshot


def coarse_candles(count=300):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    for i in range(count):
        opened = start + timedelta(hours=i)
        candles.append({"open_time": opened.isoformat(),
                        "close_time": (opened + timedelta(hours=1, milliseconds=-1)).isoformat(),
                        "open": "100", "high": "102", "low": "95" if i in (260, 280) else "98",
                        "close": "100", "volume": "1", "quote_volume": "100", "taker_buy_quote_volume": "50"})
    return candles


def test_incremental_resume_matches_full_replay_and_keeps_formation_atr():
    candles = coarse_candles()
    initial = new_state("BTCUSDT", "1h", 0.1)
    prefix = advance(initial, candles[:275], candles[274]["close_time"])
    resumed = advance(prefix, candles, candles[-1]["close_time"])
    full = advance(initial, candles, candles[-1]["close_time"])
    assert resumed == full
    assert initial["coarse_count"] == 0
    assert full["zones"][0]["formation_atr"] == prefix["zones"][0]["formation_atr"]
    assert full["zones"][0]["revision"] == 2
    assert full["zones"][0]["id"] == prefix["zones"][0]["id"]


def test_in_zone_is_testing_and_two_closes_invalidate():
    candles = coarse_candles()
    state = advance(new_state("BTCUSDT", "1h", 0.1), candles, candles[-1]["close_time"])
    result = snapshot(state, 95., candles[-1]["close_time"])
    assert result["levels"][0]["zone_state"] == "testing"
    extra = coarse_candles(302)[300:]
    for candle in extra:
        candle.update(open="94", high="96", low="93", close="94")
    broken = advance(state, extra, extra[-1]["close_time"])
    assert broken["zones"][0]["state"] == "broken"
    assert not snapshot(broken, 95., extra[-1]["close_time"])["levels"]


def test_pivot_not_published_before_right_confirmation_and_no_warmup_zones():
    candles = coarse_candles()
    initial = new_state("BTCUSDT", "1h", 0.1)
    state = advance(initial, candles[:262], candles[261]["close_time"])
    assert not state["zones"]
    state = advance(state, candles[:263], candles[262]["close_time"])
    assert len(state["zones"]) == 1


def test_future_candles_cannot_change_current_output_and_gap_rejected():
    candles = coarse_candles()
    initial = new_state("BTCUSDT", "1h", 0.1)
    prefix = advance(initial, candles[:275], candles[274]["close_time"])
    modified = deepcopy(candles)
    for candle in modified[275:]:
        candle.update(high="200", low="1")
    assert advance(initial, modified, candles[274]["close_time"]) == prefix
    with pytest.raises(ValueError, match="gap"):
        advance(prefix, candles[276:], candles[-1]["close_time"])


def test_main_timeframe_touch_reaction_resume_is_identical():
    candles = coarse_candles(304)
    for candle in candles[300:]:
        candle.update(open="100", high="101", low="94", close="100",
                      quote_volume="200", taker_buy_quote_volume="80")
    initial = new_state("BTCUSDT", "1h", 0.1)
    state = advance(initial, candles[:300], candles[299]["close_time"])
    before = state["zones"][0]["independent_touch_count"]
    partial = advance(state, candles[300:301], candles[300]["close_time"])
    resumed = advance(partial, candles[301:], candles[-1]["close_time"])
    full = advance(state, candles[300:], candles[-1]["close_time"])
    assert resumed == full
    assert full["zones"][0]["independent_touch_count"] == before + 1
    assert full["zones"][0]["recent_events"][-1]["status"] == "volume_defended"
    assert partial["zones"][0]["episode"]["status"] == "observing"
