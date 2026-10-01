import json
from copy import deepcopy
from datetime import UTC, datetime

import pytest

from trade_helper.agent import _final_reasoning, analyze_with_tools, fallback_analysis
from trade_helper.analysis import build_report
from trade_helper.follow_up import build_follow_up_plan
from trade_helper.report_contract import validate_report

from .test_analysis import detailed_explanation, sample_candles


def test_follow_up_uses_nearest_exact_zones_and_next_closed_bar():
    levels = [
        {"id": "far", "kind": "support", "low": "80.0", "high": "82.0"},
        {"id": "near", "kind": "support", "low": "98.1", "high": "100.2"},
        {"id": "upper", "kind": "resistance", "low": "110.3", "high": "112.4"},
    ]
    plan = build_follow_up_plan(levels, {"price": "104"}, "4h", "2026-09-29T00:00:00+00:00")
    assert [(p["low"], p["high"]) for p in plan if p["kind"] == "price_zone"] == [
        ("98.1", "100.2"), ("110.3", "112.4")]
    assert plan[0]["level_id"] == "near"
    assert plan[-1]["time"] == "2026-09-29T04:00:00+00:00"
    assert plan[0]["snapshot_inside_zone"] is False
    inside = build_follow_up_plan(levels, {"price": "100.2"}, "1h", "2026-09-29T00:00:00+00:00")
    assert inside[0]["snapshot_inside_zone"] is True
    assert "分析時價格已在" in inside[0]["condition"]


def test_no_zone_follow_up_uses_only_time_and_does_not_invent_price():
    plan = build_follow_up_plan([], {"price": "104"}, "1h", "2026-09-29T00:00:00+00:00")
    assert len(plan) == 1 and plan[0]["id"] == "next_close"
    assert "low" not in plan[0]


@pytest.mark.parametrize("mutation", ["missing_detail", "missing_stance", "numeric_reason"])
def test_detailed_agent_explanation_rejects_missing_or_invented_reasons(mutation):
    result = {"candidates": [], "follow_up_plan": build_follow_up_plan(
        [], {"price": "104"}, "1h", "2026-09-29T00:00:00+00:00")}
    trace = [{"tool": "support_resistance"}, {"tool": "strategy_candidates", "result": result}]
    value = {"market": "趨勢待確認。", "levels": "區間不足。",
             "strategy": "等待新收盤再評估。", "strategy_decision": "wait",
             "evidence_tools": ["support_resistance", "strategy_candidates"],
             **detailed_explanation(result)}
    assert _final_reasoning(json.dumps(value), trace, require_detail=True)["supporting_evidence"]
    if mutation == "missing_detail":
        del value["counter_evidence"]
    elif mutation == "missing_stance":
        del value["agent_stance"]
    else:
        value["counter_evidence"] = "等到 99999 元再進場。"
    with pytest.raises(ValueError):
        _final_reasoning(json.dumps(value), trace, require_detail=True)


def test_report_recomputes_follow_up_even_when_report_and_trace_are_both_tampered():
    candles = sample_candles(recent=True)
    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": "1h", "leverage": 5}
    quote = {"price": candles[-1]["close"], "tick_size": "0.1", "snapshot_hash": "a" * 64,
             "observed_at": datetime.now(UTC).isoformat()}
    decision = fallback_analysis(request, candles, quote)
    report = build_report(request, candles, quote, [], decision)
    assert report["follow_up_plan"][-1]["id"] == "next_close"
    assert report["reasoning"]["supporting_evidence"]
    tampered = deepcopy(report)
    tampered["follow_up_plan"][-1]["time"] = "2099-01-01T00:00:00+00:00"
    run = next(run for run in tampered["tool_trace"] if run["tool"] == "strategy_candidates")
    run["result"]["follow_up_plan"] = tampered["follow_up_plan"]
    with pytest.raises(ValueError, match="Follow-up plan"):
        validate_report(tampered)


def test_agent_deadline_stops_before_another_model_call(monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("APP_ANALYSIS_TIMEOUT_SECONDS", "240")
    clock = iter([0, 241])
    monkeypatch.setattr("trade_helper.agent.monotonic", lambda: next(clock))
    monkeypatch.setattr("trade_helper.agent.OpenAI", lambda **kwargs: SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kwargs: calls.append(kwargs))))
    candles = sample_candles(recent=True)
    with pytest.raises(TimeoutError, match="time budget"):
        analyze_with_tools({"market_id": "binance:perp:BTCUSDT", "timeframe": "1h"},
                           candles, {"price": candles[-1]["close"], "tick_size": "0.1",
                                     "observed_at": datetime.now(UTC).isoformat()})
    assert calls == []

def test_agent_keeps_report_without_numeric_validation_or_repair(monkeypatch):
    from types import SimpleNamespace

    candles = sample_candles(recent=True)
    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": "1h", "leverage": 5}
    quote = {"price": candles[-1]["close"], "tick_size": "0.1", "observed_at": datetime.now(UTC).isoformat()}
    raw = {"market": "MA20 為99999。", "strategy": "依現價評估空方。", "evidence_tools": ["custom_tool"],
           "agent_stance": "short", "entry_decision": {"action": "open_now", "side": "short",
           "entry_price": "120.123", "stop_loss": "121.123", "take_profit": "110.123"}}
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output=[], output_text=json.dumps(raw))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr("trade_helper.agent.OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)))
    result = analyze_with_tools(request, candles, quote)
    assert len(calls) == 1
    assert result["reasoning"]["market"] == raw["market"]
    assert result["reasoning"]["entry_decision"]["entry_price"] == "120.123"
    assert result["reasoning"]["entry_decision"]["trigger"] is None


def test_failure_reason_only_exposes_known_validation_messages():
    from trade_helper.agent import model_failure_reason
    assert "AI explanation is missing fields" in model_failure_reason(
        ValueError("AI explanation is missing fields"))
    assert model_failure_reason(ValueError("sensitive provider payload")) == "模型工具流程失敗：ValueError"
