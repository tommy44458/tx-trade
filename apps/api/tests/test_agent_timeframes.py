"""The selected timeframe and three larger intervals reach the model together."""

import json
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trade_helper.agent import STRATEGY_PROMPT_VERSION, agent_context, analyze_with_tools
from trade_helper.analysis import build_report
from trade_helper.report_contract import validate_report
from trade_helper.technical_snapshot import prepare_analysis_evidence
from trade_helper.timeframes import advance_candle, analysis_timeframes, candle_close, candle_open


def candles(timeframe, cutoff, offset, count=180):
    end = candle_open(cutoff, timeframe)
    result = []
    for index in range(count):
        opened = advance_candle(end, timeframe, index - count)
        price = Decimal(100 + offset + index + (index % 9 - 4) * 2)
        result.append({
            "open_time": opened.isoformat(), "close_time": candle_close(opened, timeframe).isoformat(),
            "open": str(price - 1), "high": str(price + 3), "low": str(price - 3),
            "close": str(price), "volume": str(100 + offset + index),
        })
    return result


@pytest.mark.parametrize("primary,expected,monthly_count", [
    ("1h", ["1h", "4h", "12h", "1d"], 180),
    ("4h", ["4h", "12h", "1d", "3d"], 180),
    ("12h", ["12h", "1d", "3d", "1w"], 180),
    ("1d", ["1d", "3d", "1w", "1M"], 180),
    ("1d", ["1d", "3d", "1w", "1M"], 8),
])
def test_first_model_turn_receives_all_four_numeric_frames_and_selected_primary(monkeypatch, primary, expected, monthly_count):
    cutoff = datetime.now(UTC)
    series = {frame: candles(frame, cutoff, offset * 100,
                            monthly_count if frame == "1M" else 180)
              for offset, frame in enumerate(analysis_timeframes(primary))}
    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": primary,
               "leverage": 5, "risk_tolerance": "high", "trading_style": "left"}
    quote = {"price": series[primary][-1]["close"], "tick_size": "0.1", "snapshot_hash": "a" * 64,
             "observed_at": cutoff.isoformat(), "primary_timeframe": primary,
             "higher_timeframe_candles": {
                 frame: {"candles": series[frame]} for frame in expected[1:]}}
    secondary = series[expected[1]]
    trace = prepare_analysis_evidence(request, series[primary], quote, secondary)
    supplied = agent_context(request, series[primary], quote, secondary, prepared_trace=trace)
    plan = supplied["timeframe_plan"]
    assert plan["analysis_timeframes"] == expected
    assert plan["context_timeframes"] == expected[1:]
    assert plan["background_review_order"] == list(reversed(expected[1:]))
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        payload = json.loads(kwargs["input"][0]["content"])
        evidence = payload["precomputed_evidence"]["technical_snapshot"]
        assert evidence["analysis_timeframes"] == expected
        assert list(evidence["timeframes"]) == expected
        for frame in expected:
            data = evidence["timeframes"][frame]
            assert data["status"] == "available"
            assert data["metrics"]["last_close"] == series[frame][-1]["close"]
            if frame == "1M" and monthly_count < 15:
                assert data["indicators"]["rsi"]["status"] == "unavailable"
                assert data["metrics"]["ma20"] is None and data["metrics"]["ma50"] is None
            else:
                assert data["indicators"]["rsi"]["value"] is not None
            table = data["recent_closed_candles"]
            assert table["interval"] == frame
            close_index = table["columns"].index("close")
            assert table["rows"][-1][close_index] == series[frame][-1]["close"]
            if frame == "1M":
                assert table["interval_hours"] is None and table["calendar_months"] == 1
        assert "largest context timeframe down to the smallest" in kwargs["instructions"]
        assert "Do not vote across four timeframes" in kwargs["instructions"]
        assert "Traditional Chinese" in kwargs["instructions"]
        response = {
            "market": "大週期方向與主週期的位置分開判斷。", "levels": "主週期價帶是重新觀察價格反應的位置。",
            "strategy": "目前觀望，等主週期出現更合適的位置。", "agent_stance": "wait",
            "strategy_decision": "wait", "supporting_evidence": "比較較大方向與目前追價的空間。",
            "counter_evidence": "主週期出現承接反應時重新判斷。", "macro_outlook": None,
            "evidence_tools": [item["tool"] for item in trace],
            "entry_decision": {"action": "stand_aside", "reason": "目前沒有合適位置。",
                               "basis_level_ids": []},
        }
        return SimpleNamespace(output=[], output_text=json.dumps(response, ensure_ascii=False))

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr("trade_helper.agent.OpenAI", lambda **kwargs:
                        SimpleNamespace(responses=SimpleNamespace(create=create)))
    decision = analyze_with_tools(request, series[primary], quote, secondary, prepared_trace=trace)
    assert len(calls) == 1
    assert decision["analysis_execution"]["prompt_version"] == STRATEGY_PROMPT_VERSION
    assert decision["analysis_execution"]["additional_tool_calls"] == 0
    report = build_report(request, series[primary], quote, [], decision, secondary)
    assert report["timeframe"] == primary
    assert report["technical_snapshot"] == trace[0]["result"]
    assert report["timeframe_context"]["context_timeframes"] == expected[1:]
    # Strict audit checks remain available off the inference path; this report
    # has no macro evidence, so test the complete numeric snapshot with rules mode.
    audit = deepcopy(report)
    audit.update(analysis_mode="rules_only", entry_decision=None, entry_risk_reference=None,
                 reasoning={}, agent_stance=None)
    validate_report(audit)
