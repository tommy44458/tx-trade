from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trade_helper.support_levels import historical_levels, order_book_evidence, wilder_atr


def candles_with_swings(count=80):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    for index in range(count):
        low = "95" if index == 20 else "95.2" if index == 40 else "99"
        high = "105" if index == 25 else "105.2" if index == 45 else "101"
        candles.append({"open_time": (start + timedelta(hours=index)).isoformat(),
                        "close_time": (start + timedelta(hours=index + 1)).isoformat(),
                        "open": "100", "high": high, "low": low, "close": "100",
                        "volume": "200" if index in {20, 40, 25, 45} else "100"})
    return candles


def test_cluster_requires_confirmed_swings_and_records_volume():
    levels = historical_levels(candles_with_swings(), "1h", Decimal(100))
    support = next(level for level in levels if level["kind"] == "support")
    resistance = next(level for level in levels if level["kind"] == "resistance")
    assert support["touch_count"] == resistance["touch_count"] == 2
    assert Decimal(support["relative_pivot_volume"]) == 2
    assert support["confirmed_at"] == candles_with_swings()[42]["close_time"]
    assert support["evidence_label"] == "heuristic_not_probability"


def test_latest_swing_waits_for_two_following_closes():
    candles = candles_with_swings(82)
    candles[78]["low"] = "92"
    before = historical_levels(candles[:80], "1h", Decimal(100))
    after = historical_levels(candles, "1h", Decimal(100))
    assert all(level["center"] != "92" for level in before)
    assert any(level["center"] == "92" for level in after)


def test_two_close_break_resets_old_touch_count():
    candles = candles_with_swings()
    for index in (30, 31):
        candles[index]["close"] = "94"
        candles[index]["low"] = "93.5"
    levels = historical_levels(candles, "1h", Decimal(100))
    renewed = next(level for level in levels if level["center"] == "95.2")
    assert renewed["touch_count"] == 1


def test_relative_volume_changes_zone_priority_without_changing_price():
    candles = candles_with_swings(100)
    candles[70]["low"] = "97"
    before = [level for level in historical_levels(candles, "1h", Decimal(100))
              if level["kind"] == "support"]
    candles[70]["volume"] = "300"
    after = [level for level in historical_levels(candles, "1h", Decimal(100))
             if level["kind"] == "support"]
    assert before[0]["center"] == "95.1"
    assert after[0]["center"] == "97"


def test_wilder_atr_smooths_history_instead_of_last_fourteen_only():
    candles = candles_with_swings()
    first = wilder_atr(candles[:30])
    later = wilder_atr(candles[:31])
    true_range = Decimal(candles[30]["high"]) - Decimal(candles[30]["low"])
    assert later == (first * 13 + true_range) / 14


def test_order_book_wall_is_snapshot_evidence_not_positions():
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    book = {"T": now_ms,
            "bids": [[str(Decimal(100) - Decimal(index) / 100),
                      "12" if index == 4 else "1"] for index in range(1, 21)],
            "asks": [[str(Decimal(100) + Decimal(index) / 100),
                      "12" if index == 4 else "1"] for index in range(1, 21)]}
    evidence = order_book_evidence(book, Decimal(100))
    assert evidence["status"] == "snapshot"
    assert {zone["side"] for zone in evidence["zones"]} == {"bid", "ask"}
    assert all("position" not in zone for zone in evidence["zones"])
    assert order_book_evidence(book, Decimal(110))["status"] == "misaligned"
    book["T"] -= 60_000
    assert order_book_evidence(book, Decimal(100))["status"] == "stale"
