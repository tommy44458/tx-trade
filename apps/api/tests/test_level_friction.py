from scripts.backtest_level_friction_2w import _event


def _candle(low: int, high: int, close: int) -> dict:
    return {"low": str(low), "high": str(high), "close": str(close)}


def test_delayed_break_is_not_mistaken_for_immediate_pass() -> None:
    support = {"low": "95", "high": "97"}
    candles = [_candle(96, 101, 98), _candle(94, 99, 94),
               _candle(93, 96, 94), _candle(92, 95, 93)]

    result = _event(candles, 0, support, "support")

    assert result is not None
    assert result["immediate_clean_pass"] is False
    assert result["delayed_cross"] is True
    assert result["cross_within_4"] is True
    assert result["no_cross_4"] is False


def test_cross_then_reentry_records_failed_break() -> None:
    resistance = {"low": "103", "high": "105"}
    candles = [_candle(102, 106, 106), _candle(104, 107, 106),
               _candle(101, 106, 104), _candle(100, 104, 102)]

    result = _event(candles, 0, resistance, "resistance")

    assert result is not None
    assert result["immediate_clean_pass"] is True
    assert result["reentered_after_cross"] is True
