from replay_v4 import outcome

from .test_volume_evidence import candles


def test_replay_detects_main_timeframe_cross_and_censors_incomplete_followup():
    coarse = candles()
    for candle in coarse[40:]:
        candle.update(open="99", high="100", low="96", close="97",
                      quote_volume="200", taker_buy_quote_volume="40")
    zone = {"kind": "support", "low": 98, "high": 100}
    result = outcome(coarse, 40, zone, 2, "1h", 0.1)
    assert result["immediate_persistent_cross"]
    assert not result["censored"]
    partial = outcome(coarse[:41], 40, zone, 2, "1h", 0.1)
    assert partial["censored"]
    assert not partial["immediate_persistent_cross"]


def test_replay_checks_only_next_closed_main_timeframe_candle_for_touch():
    coarse = candles()
    for candle in coarse[41:]:
        candle.update(open="99", high="100", low="96", close="97")
    assert outcome(coarse, 40, {"kind": "support", "low": 98, "high": 100},
                   2, "1h", 0.1) is None
