from collections import Counter

from summarize_v4_year import four_bars, month_block_intervals

from trade_helper.market_store import iso, stamp


def test_common_four_bar_metric_uses_frozen_bounds_after_touch():
    start = stamp("2025-09-01T00:00:00+00:00")
    closes = (101, 99, 101, 100)
    coarse = [{"open_time": iso(start + i * 3_600_000), "close": str(close)}
              for i, close in enumerate(closes)]
    index = {stamp(c["open_time"]): i for i, c in enumerate(coarse)}
    event = {"decision_at": iso(start - 1), "low": 100, "high": 102,
             "kind": "support", "gap_cross": False}
    assert four_bars(event, coarse, index) == {"no_cross_4": False,
                                                "immediate_clean_pass": False}
    event["low"] = 98
    assert four_bars(event, coarse, index) == {"no_cross_4": True,
                                                "immediate_clean_pass": False}
    event["gap_cross"] = True
    assert four_bars(event, coarse, index) is None


def test_month_block_comparison_keeps_months_aligned():
    monthly = {"1h": {f"2025-{month:02d}": {
        "v4_volume_rank": Counter(fourbar_evaluable=10, no_cross_4=8),
        "v2": Counter(fourbar_evaluable=10, no_cross_4=6),
        "v3": Counter(fourbar_evaluable=10, no_cross_4=7),
        "v4_price_rank": Counter(fourbar_evaluable=10, no_cross_4=7),
    } for month in range(1, 13)}}
    result = month_block_intervals(monthly)["1h"]
    assert result["v2"]["difference"] > 0
    assert result["v2"]["month_block_95_interval"][0] > 0
