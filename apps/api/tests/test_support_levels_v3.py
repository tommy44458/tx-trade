from decimal import Decimal

import pytest

from trade_helper.support_levels_v3 import historical_levels_v3

from .test_support_levels import candles_with_swings


def test_v3_waits_for_confirmation_and_keeps_formation_atr() -> None:
    candles = candles_with_swings()
    initial = historical_levels_v3(candles[:60], "1h", Decimal(100))
    after = historical_levels_v3(candles, "1h", Decimal(100))
    support_initial = next(zone for zone in initial["levels"] if zone["kind"] == "support")
    support_after = next(zone for zone in after["levels"] if zone["kind"] == "support")
    assert support_initial["id"] == support_after["id"]
    assert support_initial["formation_atr"] == support_after["formation_atr"]
    assert support_after["pivot_count"] == 2
    assert support_after["independent_touch_count"] == 1
    assert support_after["revision"] == 2

    candles = candles_with_swings(82)
    candles[78]["low"] = "92"
    before_confirmation = historical_levels_v3(candles[:80], "1h", Decimal(100))
    after_confirmation = historical_levels_v3(candles, "1h", Decimal(100))
    assert all(zone["center"] != "92" for zone in before_confirmation["levels"])
    assert any(zone["center"] == "92" for zone in after_confirmation["levels"])


def test_v3_repeated_bars_inside_zone_count_once_until_rearmed() -> None:
    candles = candles_with_swings(100)
    for index in range(50, 56):
        candles[index]["low"] = "95.1"
        candles[index]["close"] = "95.2"
    result = historical_levels_v3(candles, "1h", Decimal(100))
    support = next(zone for zone in result["levels"] if zone["kind"] == "support")
    assert support["independent_touch_count"] == 2


def test_v3_two_closes_beyond_zone_invalidate_it() -> None:
    candles = candles_with_swings()
    for index in (60, 61):
        candles[index].update({"open": "94", "high": "96", "low": "93", "close": "94"})
    result = historical_levels_v3(candles, "1h", Decimal(100))
    assert result["invalidated_zone_count"] >= 1
    assert all(zone["center"] != "95.1" for zone in result["levels"])


@pytest.mark.parametrize("kind", ["support", "resistance"])
def test_quote_inside_or_across_original_zone_does_not_hide_or_rewrite_it(kind) -> None:
    candles = candles_with_swings()
    original = next(z for z in historical_levels_v3(candles, "1h", Decimal(100))["levels"]
                    if z["kind"] == kind)
    for quote in (Decimal(original["center"]), Decimal(original["low"]) - Decimal("0.1"),
                  Decimal(original["high"]) + Decimal("0.1")):
        result = historical_levels_v3(candles, "1h", quote)
        zone = next(z for z in result["levels"] if z["id"] == original["id"])
        assert (zone["low"], zone["high"], zone["revision"], zone["kind"]) == (
            original["low"], original["high"], original["revision"], kind)
        assert zone["consecutive_closes_beyond"] == 0
        assert zone["zone_state"] == "active"
        assert zone["role_reversal_confirmed"] is False
    inside = historical_levels_v3(candles, "1h", Decimal(original["center"]))
    assert next(z for z in inside["levels"] if z["id"] == original["id"])["price_relation"] == "inside"


@pytest.mark.parametrize("kind,close,op,high,low", [
    ("resistance", "106", "100", "107", "99"),
    ("support", "94", "100", "101", "93"),
])
def test_first_close_crossing_remains_visible_and_second_is_only_invalidated_context(
        kind, close, op, high, low) -> None:
    candles = candles_with_swings()
    original = next(z for z in historical_levels_v3(candles[:78], "1h", Decimal(100))["levels"]
                    if z["kind"] == kind)
    for index in (78, 79):
        candles[index].update(open=op, high=high, low=low, close=close)
    once = historical_levels_v3(candles[:79], "1h", Decimal(close))
    zone = next(z for z in once["levels"] if z["id"] == original["id"])
    assert zone["consecutive_closes_beyond"] == 1
    assert zone["zone_state"] == "active"
    assert zone["break_evidence"] == [{"at": candles[78]["close_time"], "close": close,
                                       "low": original["low"], "high": original["high"],
                                       "revision": original["revision"]}]
    twice = historical_levels_v3(candles, "1h", Decimal(close))
    assert all(z["id"] != original["id"] for z in twice["levels"])
    failed = next(z for z in twice["recently_invalidated_levels"] if z["id"] == original["id"])
    assert failed["zone_state"] == "invalidated"
    assert failed["invalidated_at"] == candles[79]["close_time"]
    assert failed["consecutive_closes_beyond"] == len(failed["break_evidence"]) == 2
    assert failed["kind"] == kind and failed["role_reversal_confirmed"] is False


def test_close_returning_inside_resets_break_evidence_without_losing_zone() -> None:
    candles = candles_with_swings()
    candles[-2].update(open="100", high="107", low="99", close="106")
    candles[-1].update(open="106", high="107", low="104", close="105")
    result = historical_levels_v3(candles, "1h", Decimal(105))
    zone = next(z for z in result["levels"] if z["kind"] == "resistance")
    assert zone["price_relation"] == zone["last_closed_relation"] == "inside"
    assert zone["consecutive_closes_beyond"] == 0
    assert zone["break_evidence"] == []


def test_v3_rejects_missing_candles_and_bad_tick() -> None:
    candles = candles_with_swings()
    with pytest.raises(ValueError, match="gap"):
        historical_levels_v3(candles[:30] + candles[31:], "1h", Decimal(100))
    with pytest.raises(ValueError, match="Tick size"):
        historical_levels_v3(candles, "1h", Decimal(100), Decimal(0))
