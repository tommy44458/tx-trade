from backtest_fine_friction_v3 import fine_event


def _bar(index: int, low: str, high: str, close: str) -> dict:
    return {"open_time": f"2026-09-01T00:{index * 5:02d}:00+00:00",
            "low": low, "high": high, "close": close}


def test_fine_event_orders_touch_cross_and_reentry() -> None:
    zone = {"low": "98", "high": "100"}
    bars = [_bar(0, "101", "103", "102"), _bar(1, "99", "102", "101"),
            _bar(2, "97", "100", "97"), _bar(3, "98", "101", "99"),
            _bar(4, "99", "102", "101")]
    result = fine_event(bars, 0, 2, 5, zone, "support")
    assert result is not None
    assert result["minutes_to_first_cross"] == 5
    assert result["cross_within_15m"]
    assert result["reentered_after_cross"]
    assert not result["no_cross_horizon"]
    assert fine_event(bars, 0, 1, 5, zone, "support") is None


def test_fine_event_holds_when_no_close_crosses_far_boundary() -> None:
    bars = [_bar(0, "99", "102", "100"), _bar(1, "97", "101", "99")]
    result = fine_event(bars, 0, 1, 2, {"low": "98", "high": "100"}, "support")
    assert result is not None
    assert result["no_cross_horizon"]
    assert result["minutes_to_first_cross"] is None
