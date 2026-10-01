import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trade_helper.agent import (
    _final_reasoning,
    agent_context,
    analyze_with_tools,
    fallback_analysis,
)
from trade_helper.analysis import build_report
from trade_helper.indicators import execute_tool
from trade_helper.position_advice import VERSION, build_position_options
from trade_helper.report_contract import validate_report
from trade_helper.support_levels_v3 import VERSION as LEVEL_VERSION

from .test_analysis import detailed_explanation, sample_candles

MARKET = "binance:perp:BTCUSDT"
QUOTE = {"price": "100", "mark_price": "100", "tick_size": "0.1",
         "observed_at": "2026-09-28T18:00:00+00:00"}
LEVELS = [
    {"id": "support-a", "kind": "support", "low": "96", "high": "98",
     "algorithm_version": LEVEL_VERSION, "zone_state": "active", "pivot_count": 2,
     "independent_touch_count": 1},
    {"id": "resistance-a", "kind": "resistance", "low": "102", "high": "104",
     "algorithm_version": LEVEL_VERSION, "zone_state": "active", "pivot_count": 2,
     "independent_touch_count": 1},
]


def position(position_id="p1", side="long", stop="90", target="120"):
    return {"id": position_id, "version": 1, "market_id": MARKET, "side": side,
            "entry_price": "95" if side == "long" else "105", "quantity": "2",
            "leverage": 5, "margin_mode": "isolated", "stop_loss": stop,
            "take_profit": target, "previous_stop_loss": None}


def test_qualified_v3_levels_only_offer_tighter_stops_in_both_directions():
    options = build_position_options([position(), position("p2", "short", "110", "80")],
                                     QUOTE, LEVELS, "range", Decimal(2))
    assert options["version"] == VERSION
    long, short = options["positions"]
    long_tighten = long["candidates"][1]
    short_tighten = short["candidates"][1]
    assert (long_tighten["proposed_stop"], long_tighten["risk_change_usdt"],
            long_tighten["level_id"]) == ("95.8", "-11.6", "support-a")
    assert (short_tighten["proposed_stop"], short_tighten["risk_change_usdt"],
            short_tighten["level_id"]) == ("104.2", "-11.6", "resistance-a")
    unqualified = [{**LEVELS[0], "pivot_count": 1}, {**LEVELS[1], "zone_state": "invalidated"}]
    result = build_position_options([position()], QUOTE, unqualified, "range", Decimal(2))
    assert [item["kind"] for item in result["positions"][0]["candidates"]] == ["maintain"]
    near_price = [{**LEVELS[0], "low": "99.8", "high": "99.9"}]
    result = build_position_options([position()], QUOTE, near_price, "range", Decimal(2))
    assert [item["kind"] for item in result["positions"][0]["candidates"]] == ["maintain"]


@pytest.mark.parametrize(("item", "expected"), [
    (position(stop="105"), "verify_execution"),
    (position("p2", "short", "95", "80"), "verify_execution"),
    (position(target="99"), "verify_execution"),
    (position(stop=None), "review_protection"),
    (position(target=None), "review_protection"),
])
def test_reached_orders_and_missing_protection_force_review(item, expected):
    result = build_position_options([item], QUOTE, LEVELS, "range", Decimal(2))
    candidates = result["positions"][0]["candidates"]
    assert [candidate["kind"] for candidate in candidates] == [expected]
    assert candidates[0]["proposed_stop"] is None


def test_opposing_closed_timeframes_offer_exposure_review_without_size_claim():
    result = build_position_options([position()], QUOTE, LEVELS, "bearish", Decimal(2))
    candidates = result["positions"][0]["candidates"]
    assert "reduce_exposure_review" in [candidate["kind"] for candidate in candidates]
    assert result["positions"][0]["default_action_id"] == "p1:reduce_exposure_review"
    assert all(candidate["proposed_stop"] is None for candidate in candidates
               if candidate["kind"] == "reduce_exposure_review")



def test_current_bias_and_low_risk_only_add_review_candidates():
    for bias, risk, state in (("bearish", None, "bullish"),
                              (None, "low", "conflict")):
        result = build_position_options([position()], QUOTE, LEVELS, state, Decimal(2),
                                        bias, risk)
        candidates = result["positions"][0]["candidates"]
        assert "reduce_exposure_review" in [candidate["kind"] for candidate in candidates]
        assert all(candidate["proposed_stop"] is None for candidate in candidates
                   if candidate["kind"] == "reduce_exposure_review")
    result = build_position_options([position()], QUOTE, LEVELS, "conflict", Decimal(2),
                                    None, "high")
    assert "reduce_exposure_review" not in [candidate["kind"]
                                            for candidate in result["positions"][0]["candidates"]]


def test_position_agent_receives_only_selected_snapshot_and_validated_tool(monkeypatch):
    candles = sample_candles(recent=True)
    quote = {"price": candles[-1]["close"], "tick_size": "0.1",
             "snapshot_hash": "a" * 64, "observed_at": datetime.now(UTC).isoformat()}
    request = {"kind": "positions", "market_id": MARKET, "timeframe": "1h", "leverage": 5}
    selected = [position() | {"user_id": "private-user", "email": "private@example.com"}]
    context = agent_context(request, candles, quote, None, selected)
    assert context["selected_positions"][0]["id"] == "p1"
    assert "private-user" not in json.dumps(context)
    assert "private@example.com" not in json.dumps(context)
    tool = execute_tool("evaluate_positions", {"reason": "Review selected position"},
                        candles, quote, request, positions=selected)
    choice = tool["result"]["positions"][0]["default_action_id"]
    candidate_result = execute_tool("strategy_candidates", {"reason": "Fixture"},
                                    candles, quote, request, positions=selected)["result"]
    outputs = iter([SimpleNamespace(output=[], output_text=json.dumps({
        "market": "依已收盤資料判讀。", "levels": "採信有效區間。",
        "strategy": "先確認部位風險。", "evidence_tools": [
            "support_resistance", "strategy_candidates", "evaluate_positions"],
        "strategy_decision": "wait", "position_choices": {"p1": choice},
        **detailed_explanation(candidate_result),
        "position_decisions": {"p1": {"decision": "close_now", "reason": "現價已接近風險區，現在平倉較合適。"}}}, ensure_ascii=False))])

    class FakeResponses:
        def create(self, **kwargs):
            evidence = json.loads(kwargs["input"][0]["content"])["precomputed_evidence"]
            assert evidence["evaluate_positions"]["positions"] == [{"position_id": "p1", "version": 1}]
            assert "candidates" not in evidence["evaluate_positions"]["positions"][0]
            return next(outputs)

    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr("trade_helper.agent.OpenAI",
                        lambda **_kwargs: SimpleNamespace(responses=FakeResponses()))
    decision = analyze_with_tools(request, candles, quote, positions=selected)
    assert decision["position_choices"] == {"p1": choice}
    report = build_report(request, candles, quote, selected, decision)
    assert report["position_reviews"][0]["agent_decision"]["decision"] == "close_now"
    assert report["position_reviews"][0]["advice"]["selection_source"] == "python_reference"
    validate_report(report)
    tampered = deepcopy(report)
    tampered["position_reviews"][0]["advice"]["proposed_stop"] = "1"
    with pytest.raises(ValueError, match="unvalidated action"):
        validate_report(tampered)
    tampered = deepcopy(report)
    tampered["position_snapshot"][0]["stop_loss"] = "1"
    with pytest.raises(ValueError, match="Python tool evidence"):
        validate_report(tampered)


def test_invalid_agent_position_choice_is_rejected_and_rules_fallback_is_traceable():
    candles = sample_candles(recent=True)
    quote = {"price": candles[-1]["close"], "tick_size": "0.1",
             "snapshot_hash": "a" * 64, "observed_at": datetime.now(UTC).isoformat()}
    request = {"kind": "positions", "market_id": MARKET, "timeframe": "1h", "leverage": 5}
    selected = [position(stop=None, target="250")]
    decision = fallback_analysis(request, candles, quote, positions=selected)
    assert decision["position_choices"] == {"p1": "p1:review_protection"}
    report = build_report(request, candles, quote, selected, decision)
    assert report["position_reviews"][0]["advice"]["selection_source"] == "python_reference"
    assert report["position_reviews"][0]["advice"]["proposed_stop"] is None
    validate_report(report)
    decision["position_choices"] = {"p1": "p1:tighten_stop_review"}
    report = build_report(request, candles, quote, selected, decision)
    assert report["position_reviews"][0]["advice"]["selection_source"] == "python_reference"


def test_model_cannot_choose_unlisted_position_action():
    candles = sample_candles(recent=True)
    quote = {"price": candles[-1]["close"], "tick_size": "0.1",
             "observed_at": datetime.now(UTC).isoformat()}
    request = {"kind": "positions", "market_id": MARKET, "timeframe": "1h", "leverage": 5}
    trace = fallback_analysis(request, candles, quote,
                              positions=[position(stop=None, target="250")])["tool_trace"]
    response = {"market": "依已收盤資料判讀。", "levels": "採信有效區間。",
                "strategy": "先檢查保護條件。", "strategy_decision": "wait",
                "evidence_tools": [item["tool"] for item in trace],
                "position_choices": {"p1": "p1:tighten_stop_review"}}
    with pytest.raises(ValueError, match="not a validated action"):
        _final_reasoning(json.dumps(response, ensure_ascii=False), trace)
