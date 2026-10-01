from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.volume_evidence import observe_episode, public_episode, start_episode


def candles(count=46, hours=1):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    result = []
    for i in range(count):
        opened = start + timedelta(hours=i * hours)
        result.append({"open_time": opened.isoformat(),
                       "close_time": (opened + timedelta(hours=hours, milliseconds=-1)).isoformat(),
                       "open": "103", "high": "104", "low": "102", "close": "103",
                       "volume": "1", "quote_volume": "100", "taker_buy_quote_volume": "50"})
    return result


def episode(kind="defended", cutoff=44):
    bars = candles()
    for candle in bars[40:]:
        close = "97" if kind == "crossed" else "101.5"
        candle.update(open=close, high="102", low="96", close=close,
                      quote_volume="200", taker_buy_quote_volume="40")
    zone = {"id": "zone", "kind": "support", "revision": 1,
            "low": 98., "high": 100., "pivot_count": 2, "formation_atr": 2.}
    event = start_episode(zone, bars[40], 2., "1h", 0.1, bars[:40])
    for candle in bars[40:cutoff]:
        observe_episode(event, candle)
    return event


def test_equal_main_timeframe_volume_has_opposite_meaning_when_price_crosses():
    defended = episode()
    crossed = episode("crossed")
    assert defended["status"] == "volume_defended"
    assert crossed["status"] == "volume_crossed"
    assert defended["volume_evidence"]["rvol"] == crossed["volume_evidence"]["rvol"] == 2
    assert crossed["persistent_cross"]["available_at"] > crossed["persistent_cross"]["started_at"]
    assert crossed["volume_evidence"]["directional_pressure"] == pytest.approx(0.6)
    assert public_episode(defended)["observed_bars"] == 4


def test_one_main_timeframe_candle_cannot_confirm_volume_reaction():
    event = episode(cutoff=41)
    assert event["status"] == "observing"
    assert "volume_evidence" not in event


def test_missing_and_zero_baselines_are_unavailable():
    history = candles(40)
    zone = {"id": "z", "kind": "support", "revision": 1, "low": 98., "high": 100.}
    for candle in history:
        candle.pop("quote_volume")
        candle.pop("taker_buy_quote_volume")
    event = start_episode(zone, candles()[40], 2., "1h", 0.1, history)
    assert event["baseline"]["reason"] == "missing_historical_quote_volume"
    for candle in history:
        candle.update(quote_volume="0", taker_buy_quote_volume="0")
    event = start_episode(zone, candles()[40], 2., "1h", 0.1, history)
    assert event["baseline"]["reason"] == "zero_volume_baseline"


def test_future_candles_cannot_change_frozen_baseline():
    bars = candles()
    zone = {"id": "z", "kind": "support", "revision": 1, "low": 98., "high": 100.}
    before = start_episode(zone, bars[40], 2., "1h", 0.1, bars[:40])
    changed = deepcopy(bars)
    for candle in changed[40:]:
        candle.update(quote_volume="10000000", taker_buy_quote_volume="5000000")
    after = start_episode(zone, changed[40], 2., "1h", 0.1, changed[:40])
    assert before["baseline"] == after["baseline"]


def test_main_timeframe_gap_censors_episode():
    bars = candles()
    zone = {"id": "z", "kind": "support", "revision": 1, "low": 98., "high": 100.}
    event = start_episode(zone, bars[40], 2., "1h", 0.1, bars[:40])
    observe_episode(event, bars[40])
    observe_episode(event, bars[42])
    assert event["status"] == "censored"
    assert event["end_reason"] == "main_timeframe_gap"


def test_single_far_close_is_pending_not_defended():
    bars = candles()
    bars[40].update(close="101", high="102", low="96", quote_volume="200")
    bars[41].update(close="97", high="102", low="96", quote_volume="200")
    zone = {"id": "z", "kind": "support", "revision": 1, "low": 98., "high": 100.}
    event = start_episode(zone, bars[40], 2., "1h", 0.1, bars[:40])
    for candle in bars[40:42]:
        observe_episode(event, candle)
    assert event["status"] == "cross_pending"
    assert not event["done"]


@pytest.mark.parametrize('timeframe,hours', [('12h', 12), ('1d', 24)])
def test_new_main_timeframe_volume_window_uses_its_own_candle_duration(timeframe, hours):
    bars = candles(hours=hours)
    zone = {"id": "z", "kind": "support", "revision": 1, "low": 98., "high": 100.}
    event = start_episode(zone, bars[40], 2., timeframe, 0.1, bars[:40])
    for candle in bars[40:42]:
        observe_episode(event, candle)
    assert event['volume_evidence']['timeframe'] == timeframe
    assert event['volume_evidence']['available_at'] == bars[41]['close_time']
    assert event['status'] == 'no_volume_confirmation'
