from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_helper.analysis import strategy_for
from trade_helper.strategy_engine import compare_timeframes
from trade_helper.support_levels_v3 import VERSION as LEVEL_VERSION


def fixture(trend="bullish", background="bullish", price="104"):
    now = datetime.now(UTC).isoformat()
    support = {"id": "s1", "kind": "support", "low": "98", "high": "100",
               "pivot_count": 2, "independent_touch_count": 1,
               "zone_state": "active", "algorithm_version": LEVEL_VERSION}
    resistance = {"id": "r1", "kind": "resistance", "low": "110", "high": "112",
                  "pivot_count": 2, "independent_touch_count": 1,
                  "zone_state": "active", "algorithm_version": LEVEL_VERSION}
    metrics = {"trend": trend, "atr14": "2", "last_candle_at": now,
               "levels": [support, resistance], "level_algorithm_version": LEVEL_VERSION,
               "ma20": "101", "ma50": "99"}
    secondary = {"trend": background, "last_candle_at": now,
                 "ma20": "102", "ma50": "100"}
    context = compare_timeframes(metrics, secondary, "1h")
    return metrics, context, {"price": price, "tick_size": "0.1"}


@pytest.mark.parametrize(("primary", "background", "relation", "state"), [
    ("bullish", "bullish", "aligned", "bullish"),
    ("bullish", "bearish", "conflict", "conflict"),
    ("mixed", "mixed", "range", "range"),
    ("bearish", "mixed", "uncertain", "insufficient"),
])
def test_closed_timeframe_relation(primary, background, relation, state):
    _, context, _ = fixture(primary, background)
    assert context["relation"] == relation
    assert context["market_state"] == state
    assert context["primary_timeframe"] == "1h"
    assert context["context_timeframe"] == "4h"


def test_opposite_timeframes_wait_before_any_price_candidate():
    metrics, context, quote = fixture("bullish", "bearish")
    result = strategy_for(metrics, "bullish", "high", quote, context=context)
    assert result[0]["type"] == "wait"
    assert "1H 與 4H" in result[0]["reason"]
    assert result[0]["confirmation_state"] == "not_applicable"


def test_breakout_stays_pending_after_intrabar_cross_and_targets_next_v3_zone():
    metrics, context, quote = fixture(price="113")
    metrics["levels"].append({**metrics["levels"][1], "id": "r2",
                              "low": "125", "high": "127"})
    result = strategy_for(metrics, "bullish", "medium", quote, context=context)
    breakout = next(item for item in result if item["type"] == "breakout")
    assert breakout["entry"] == "112.1"
    assert breakout["take_profit"] == "125"
    assert breakout["evidence_level_ids"] == ["r1", "r2"]
    assert breakout["confirmation_state"] == "awaiting_close"
    assert breakout["trigger_met"] is False
    assert "盤中穿越不算確認" in breakout["trigger"]
    assert Decimal(breakout["stop_loss"]) < Decimal(breakout["entry"])


def test_mixed_both_timeframes_produces_two_conditional_range_sides():
    metrics, context, quote = fixture("mixed", "mixed")
    result = strategy_for(metrics, None, None, quote, context=context)
    assert {(item["type"], item["side"]) for item in result} == {
        ("range", "long"), ("range", "short")}
    assert all(item["expires_at"] == (
        datetime.fromisoformat(metrics["last_candle_at"]) + timedelta(hours=1)).isoformat()
        for item in result)


def test_known_event_window_waits_but_missing_event_feed_is_labeled_unknown():
    metrics, context, quote = fixture()
    gated = strategy_for(metrics, None, None, quote, context=context,
                         event_risk="high_impact_window")
    assert gated[0]["type"] == "wait"
    normal = strategy_for(metrics, None, None, quote, context=context)
    assert normal[0]["type"] == "pullback"
    assert normal[0]["event_exposure"] == "unknown"
    assert any("尚未接入" in item for item in normal[0]["counter_evidence"])


def test_crossed_target_or_range_boundary_does_not_leave_candidate_active():
    metrics, context, quote = fixture(price="111")
    metrics["atr14"] = "5"
    assert strategy_for(metrics, None, None, quote, context=context)[0]["type"] == "wait"
    metrics, context, quote = fixture("mixed", "mixed", price="113")
    assert strategy_for(metrics, None, None, quote, context=context)[0]["type"] == "wait"


def test_low_risk_requires_more_touch_confirmation_than_medium():
    metrics, context, quote = fixture()
    assert strategy_for(metrics, None, "low", quote, context=context)[0]["type"] == "wait"
    medium = strategy_for(metrics, None, "medium", quote, context=context)[0]
    assert medium["type"] == "pullback"
    assert medium["risk_fit"] == "balanced"
    metrics["levels"][0]["independent_touch_count"] = 2
    low = strategy_for(metrics, None, "low", quote, context=context)[0]
    assert low["type"] == "pullback"
    assert low["risk_fit"] == "higher_confirmation"


def test_recent_official_fomc_statement_pauses_new_entries():
    metrics, context, quote = fixture()
    quote["news_risk"] = "recent_fomc_release"
    result = strategy_for(metrics, None, None, quote, context=context)
    assert result[0]["type"] == "wait"
    assert "FOMC" in result[0]["reason"]


def test_left_and_right_style_change_entry_conditions_without_changing_levels():
    metrics, context, quote = fixture()
    original_levels = [level.copy() for level in metrics["levels"]]
    left = strategy_for(metrics, "bullish", "medium", quote, context=context,
                        trading_style="left")[0]
    right = strategy_for(metrics, "bullish", "medium", quote, context=context,
                         trading_style="right")[0]
    assert left["type"] == right["type"] == "pullback"
    assert (left["entry"], left["stop_loss"], left["take_profit"]) == (
        right["entry"], right["stop_loss"], right["take_profit"])
    assert left["confirmation_state"] == "awaiting_zone_test"
    assert left["entry_style"] == "left"
    assert "無收盤反轉確認" in left["trigger"]
    assert any("直接穿透" in item for item in left["counter_evidence"])
    assert right["confirmation_state"] == "awaiting_close"
    assert right["entry_style"] == "right"
    assert "已收盤" in right["trigger"]
    assert metrics["levels"] == original_levels


def test_left_style_does_not_bypass_low_risk_or_event_gate():
    metrics, context, quote = fixture()
    low = strategy_for(metrics, None, "low", quote, context=context,
                       trading_style="left")
    assert low[0]["type"] == "wait"
    assert "低風險" in low[0]["reason"]
    event = strategy_for(metrics, None, "medium", quote, context=context,
                         event_risk="high_impact_window", trading_style="left")
    assert event[0]["type"] == "wait"
    assert "重大事件" in event[0]["reason"]


def test_right_style_prioritizes_confirmed_breakout_left_excludes_it():
    metrics, context, quote = fixture(price="113")
    metrics["levels"].append({**metrics["levels"][1], "id": "r2",
                              "low": "125", "high": "127"})
    right = strategy_for(metrics, "bullish", "medium", quote, context=context,
                         trading_style="right")
    left = strategy_for(metrics, "bullish", "medium", quote, context=context,
                        trading_style="left")
    assert right[0]["type"] == "breakout"
    assert right[0]["confirmation_state"] == "awaiting_close"
    assert all(item["type"] != "breakout" for item in left)
