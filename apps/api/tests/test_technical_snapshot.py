import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trade_helper.agent import agent_context, analyze_with_tools
from trade_helper.analysis import build_report
from trade_helper.indicators import TOOL_FUNCTIONS, rsi
from trade_helper.reasoning_evidence import validate_reason_numbers
from trade_helper.report_contract import validate_report
from trade_helper.technical_snapshot import (
    ADDITIONAL_INDICATORS,
    DEFAULT_INDICATORS,
    RECENT_CANDLE_LIMITS,
    build_technical_snapshot,
    compact_indicator_values,
    compact_technical_snapshot,
    execute_additional_indicator,
    prepare_analysis_evidence,
)
from trade_helper.timeframes import analysis_timeframes, higher_timeframes

from .test_analysis import detailed_explanation, sample_candles


def fixture(primary="1h", count=80):
    one = sample_candles(recent=True, timeframe=primary, count=count)
    four = sample_candles(recent=True, timeframe=higher_timeframes(primary)[0], count=count)
    for index, candle in enumerate(four):
        for key in ("open", "high", "low", "close"):
            candle[key] = str(Decimal(candle[key]) * 2 + Decimal(150 - index * 4))
        candle["volume"] = str(Decimal(candle["volume"]) * 2)
    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": primary, "leverage": 5}
    candles, context = one, four
    quote = {"price": candles[-1]["close"], "tick_size": "0.1", "snapshot_hash": "a" * 64,
             "observed_at": datetime.now(UTC).isoformat()}
    return request, candles, context, quote


def final_response(trace):
    candidate = next(run["result"] for run in trace if run["tool"] == "strategy_candidates")
    evidence = [run["tool"] for run in trace] + ["rsi", "volume_signal"]
    return json.dumps({"market": "以兩個週期的方向與動能綜合判斷。", "levels": "參考主週期已確認的區間。",
                       "strategy": "目前先觀望，等待更好的價格位置。", "strategy_decision": "wait",
                       "evidence_tools": evidence, **detailed_explanation(candidate)}, ensure_ascii=False)


@pytest.mark.parametrize("primary", ["1h", "4h", "12h", "1d"])
def test_both_timeframes_use_their_own_closed_candles(primary):
    request, candles, context, quote = fixture(primary)
    snapshot = build_technical_snapshot(request, candles, quote, context)
    assert snapshot["primary_timeframe"] == primary
    assert snapshot["as_of"] == quote["observed_at"]
    frames = {primary: candles, higher_timeframes(primary)[0]: context}
    for timeframe, rows in frames.items():
        frame = snapshot["timeframes"][timeframe]
        assert frame["last_closed_at"] == rows[-1]["close_time"]
        assert frame["metrics"]["last_close"] == rows[-1]["close"]
        assert frame["recent_closed_candles"] == rows[-RECENT_CANDLE_LIMITS[timeframe]:]
        for name, params in DEFAULT_INDICATORS.items():
            assert frame["indicators"][name] == TOOL_FUNCTIONS[name](rows, quote, params)
    assert snapshot["timeframes"][primary]["indicators"]["volatility_atr"]["atr"] != snapshot["timeframes"][higher_timeframes(primary)[0]]["indicators"]["volatility_atr"]["atr"]
    assert "open_time" not in snapshot["timeframes"][primary]["metrics"]


def test_support_is_computed_once_and_reference_results_match_legacy_tools(monkeypatch):
    request, candles, context, quote = fixture()
    legacy = {name: TOOL_FUNCTIONS[name](candles, quote, {
        "timeframe": "1h", "_request": request, "_context_candles": context})
        for name in ("support_resistance", "compare_timeframes", "strategy_candidates")}
    original = TOOL_FUNCTIONS["support_resistance"]
    calls = []
    def counted(*args):
        calls.append(1)
        return original(*args)
    monkeypatch.setitem(TOOL_FUNCTIONS, "support_resistance", counted)
    monkeypatch.setattr("trade_helper.indicators.support_resistance", counted)
    trace = prepare_analysis_evidence(request, candles, quote, context)
    assert len(calls) == 1
    for run in trace:
        if run["tool"] in legacy:
            assert run["result"] == legacy[run["tool"]]


@pytest.mark.parametrize("primary", ["1h", "4h", "12h", "1d"])
def test_first_model_request_has_both_frames_and_can_finish_without_tools(monkeypatch, primary):
    request, candles, context, quote = fixture(primary)
    trace = prepare_analysis_evidence(request, candles, quote, context)
    calls = []
    def create(**kwargs):
        calls.append(deepcopy(kwargs))
        payload = json.loads(kwargs["input"][0]["content"])
        assert set(payload["precomputed_evidence"]["technical_snapshot"]["timeframes"]) == set(analysis_timeframes(primary))
        assert payload["current_candle"]["quote_price"] == quote["price"]
        frames = payload["precomputed_evidence"]["technical_snapshot"]["timeframes"]
        table = frames[primary]["recent_closed_candles"]
        assert table["columns"] == ["close_time", "open", "high", "low", "close", "volume"]
        assert table["rows"][-1][0] == candles[-1]["close_time"]
        assert len(table["rows"]) == min(len(candles), RECENT_CANDLE_LIMITS[primary])
        assert frames[primary]["price_action_summary"]
        sr = payload["precomputed_evidence"]["support_resistance"]
        secondary = sr["secondary_timeframe_context"]
        assert secondary["timeframe"] != primary
        assert secondary["data"]["as_of"] == context[-1]["close_time"]
        assert all(z["timeframe"] == secondary["timeframe"] for z in secondary["data"]["levels"])
        assert kwargs["tool_choice"] == "auto"
        assert {tool["name"] for tool in kwargs["tools"]} == set(ADDITIONAL_INDICATORS)
        return SimpleNamespace(output=[], output_text=final_response(trace),
                               usage=SimpleNamespace(input_tokens=2300, output_tokens=450))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr("trade_helper.agent.OpenAI", lambda **kwargs:
                        SimpleNamespace(responses=SimpleNamespace(create=create)))
    decision = analyze_with_tools(request, candles, quote, context)
    assert len(calls) == 1
    assert decision["analysis_execution"]["additional_tool_calls"] == 0
    assert decision["analysis_execution"]["input_tokens"] == 2300
    report = build_report(request, candles, quote, [], decision, context)
    assert report["technical_snapshot"] == trace[0]["result"]
    validate_report(report)
    for mutation in ("timeframe", "cutoff", "snapshot_hash"):
        bad = deepcopy(report)
        changed = bad["technical_snapshot"]
        if mutation == "timeframe":
            secondary = higher_timeframes(primary)[0]
            changed["timeframes"][primary], changed["timeframes"][secondary] = changed["timeframes"][secondary], changed["timeframes"][primary]
        elif mutation == "cutoff":
            changed["as_of"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        else:
            changed["market_snapshot_sha256"] = "b" * 64
        bad["tool_trace"][0]["result"] = changed
        with pytest.raises(ValueError, match="Technical indicators"):
            validate_report(bad)


def test_additional_model_tool_uses_selected_timeframe_and_preserves_precomputed_values(monkeypatch):
    request, candles, context, quote = fixture()
    trace = prepare_analysis_evidence(request, candles, quote, context)
    calls = []
    def create(**kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) == 1:
            return SimpleNamespace(output=[SimpleNamespace(type="function_call", name="rsi",
                arguments=json.dumps({"reason": "檢查另一週期的較長動能", "period": 21, "timeframe": "4h"}), call_id="rsi-extra")])
        output = json.loads(kwargs["input"][-1]["output"])
        assert output["timeframe"] == "4h" and output["period"] == 21
        assert output["value"] == rsi(context, quote, {"period": 21})["value"]
        assert output["value"] != rsi(candles, quote, {"period": 21})["value"]
        return SimpleNamespace(output=[], output_text=final_response(trace))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr("trade_helper.agent.OpenAI", lambda **kwargs:
                        SimpleNamespace(responses=SimpleNamespace(create=create)))
    decision = analyze_with_tools(request, candles, quote, context)
    assert decision["analysis_execution"]["model_requests"] == 2
    assert decision["analysis_execution"]["additional_tool_calls"] == 1
    assert decision["tool_trace"][-1]["parameters"] == {"period": 21, "timeframe": "4h"}
    assert decision["tool_trace"][0]["result"] == trace[0]["result"]
    build_report(request, candles, quote, [], decision, context)


def test_missing_frame_zero_volume_and_short_fibonacci_window_are_explicit():
    request, candles, _, quote = fixture(count=60)
    for candle in candles:
        candle["volume"] = "0"
    snapshot = build_technical_snapshot(request, candles, quote, None)
    assert snapshot["timeframes"]["4h"]["status"] == "unavailable"
    indicators = snapshot["timeframes"]["1h"]["indicators"]
    assert indicators["rolling_vwap"]["reason"] == "zero_volume"
    assert indicators["volume_signal"]["relative_volume"] is None
    assert "fibonacci" not in indicators and "bollinger" not in indicators
    assert "fibonacci" in snapshot["optional_indicator_catalog"]["not_precomputed"]
    for period in (20, 30):
        run = execute_additional_indicator(
            "rolling_vwap", {"reason": "test", "period": period, "timeframe": "1h"},
            request, candles, quote, None, snapshot)
        assert run["result"]["status"] == "unavailable"
    with pytest.raises(ValueError, match="timeframe"):
        execute_additional_indicator("rsi", {"reason": "test", "period": 14, "timeframe": "4h"},
                                     request, candles, quote, None, snapshot)


def test_defaults_are_reused_and_internal_arguments_cannot_be_injected(monkeypatch):
    request, candles, context, quote = fixture()
    snapshot = build_technical_snapshot(request, candles, quote, context)
    monkeypatch.setitem(TOOL_FUNCTIONS, "rsi", lambda *args: pytest.fail("Default RSI recomputed"))
    args = {"reason": "再查既有動能", "period": 14, "timeframe": "4h"}
    result = execute_additional_indicator("rsi", args, request, candles, quote, context, snapshot)
    assert result["execution_source"] == "agent_requested_cached"
    assert "timeframe" not in snapshot["timeframes"]["4h"]["indicators"]["rsi"]
    for changed in (args | {"_request": {}}, args | {"period": 99}, args | {"timeframe": "1m"}):
        with pytest.raises(ValueError):
            execute_additional_indicator("rsi", changed, request, candles, quote, context, snapshot)


def test_snapshot_rejects_unclosed_candle_in_closed_series():
    request, candles, context, quote = fixture()
    context[-1]["close_time"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match="cutoff"):
        build_technical_snapshot(request, candles, quote, context)


def test_expanded_windows_include_older_reactions_and_report_partial_month():
    request, _, _, quote = fixture()
    one = sample_candles(count=300, recent=True, timeframe="1h")
    four = sample_candles(count=300, recent=True, timeframe="4h")
    quote["observed_at"] = datetime.now(UTC).isoformat()
    # An older excursion outside the previous six-bar input must reach the model.
    one[-40]["high"] = "1000.123456789123456"
    snapshot = build_technical_snapshot(request, one, quote, four)
    raw = deepcopy(snapshot)
    model = compact_technical_snapshot(snapshot)
    assert snapshot == raw
    for timeframe, count in (("1h", 72), ("4h", 60)):
        frame = snapshot["timeframes"][timeframe]
        assert len(frame["recent_closed_candles"]) == count
        assert frame["recent_candle_coverage"]["status"] == "complete"
        table = model["timeframes"][timeframe]["recent_closed_candles"]
        assert len(table["rows"]) == count
        reconstructed = [dict(zip(table["columns"], row, strict=True)) for row in table["rows"]]
        assert reconstructed == [{k: v for k, v in row.items() if k != "open_time"}
                                  for row in frame["recent_closed_candles"]]
    summaries = {x["window"]: x for x in snapshot["timeframes"]["1h"]["price_action_summary"]}
    assert summaries["72h"]["high"] == "1000.123456789123456"
    assert summaries["24h"]["high"] != summaries["72h"]["high"]
    assert summaries["72h"]["volume"] == "7200"
    assert summaries["30d"]["status"] == "partial"
    assert summaries["30d"]["candle_count"] == 300
    assert summaries["30d"]["duration_hours"] == 300
    assert summaries["30d"]["requested_candles"] == 720
    month = next(x for x in snapshot["timeframes"]["4h"]["price_action_summary"] if x["window"] == "30d")
    assert month["status"] == "complete"
    assert month["candle_count"] == 180 and month["duration_hours"] == 720
    assert month["start_at"] == four[-180]["open_time"]
    assert Decimal(month["change_pct"]) == round((Decimal(four[-1]["close"]) / Decimal(four[-180]["open"]) - 1) * 100, 2)
    assert "1000.123456789123456" in json.dumps(model)


def test_short_history_is_not_padded_and_forming_candle_is_excluded():
    request, candles, _, quote = fixture(count=60)
    baseline = build_technical_snapshot(request, candles, quote, None)
    quote["forming_candle"] = {"high": "999999", "volume": "999999", "close": "999999"}
    snapshot = build_technical_snapshot(request, candles, quote, None)
    assert snapshot == baseline
    frame = snapshot["timeframes"]["1h"]
    assert len(frame["recent_closed_candles"]) == 60
    assert frame["recent_candle_coverage"]["status"] == "partial"
    assert frame["recent_candle_coverage"]["duration_hours"] == 60
    model = compact_technical_snapshot(snapshot)
    assert model["timeframes"]["4h"]["status"] == "unavailable"
    assert "recent_closed_candles" not in model["timeframes"]["4h"]
    assert "999999" not in json.dumps(model)


def test_price_change_magnitude_can_be_explained_as_a_decline_without_inventing_values():
    request, candles, context, quote = fixture()
    for row in candles:
        # Mirror the increasing price fixture to get a known, valid decline.
        opening, high, low, close = (Decimal(row[k]) for k in ("open", "high", "low", "close"))
        row.update(open=str(300 - opening), high=str(300 - low), low=str(300 - high), close=str(300 - close))
    snapshot = build_technical_snapshot(request, candles, quote, context)
    day = snapshot["timeframes"]["1h"]["price_action_summary"][0]
    assert day["change_direction"] == "down"
    assert Decimal(day["change_pct"]) < 0
    assert Decimal(day["change_magnitude_pct"]) == -Decimal(day["change_pct"])
    trace = [{"tool": "technical_snapshot", "result": snapshot}]
    validate_reason_numbers(f"1H 最近一天回落 {day['change_magnitude_pct']}%。", trace)
    with pytest.raises(ValueError, match="validated numeric"):
        validate_reason_numbers("1H 最近一天回落 987654.321%。", trace)


def test_support_context_cannot_include_a_future_secondary_candle():
    request, candles, context, quote = fixture()
    context[-1]["close_time"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match="cutoff"):
        TOOL_FUNCTIONS["support_resistance"](candles, quote, {
            "timeframe": request["timeframe"], "_request": request, "_context_candles": context})


@pytest.mark.parametrize("side", ["long", "short"])
def test_high_risk_left_preference_does_not_impose_a_python_direction_veto(monkeypatch, side):
    request, candles, context, quote = fixture()
    request.update(risk_tolerance="high", trading_style="left", directional_bias="bearish")
    trace = prepare_analysis_evidence(request, candles, quote, context)
    payload = json.loads(final_response(trace))
    entry = Decimal(quote["price"])
    direction = Decimal(1 if side == "long" else -1)
    payload.update(agent_stance=side, strategy="現在依價格位置評估進場，承認相反方向仍有風險。",
        supporting_evidence="比較提前交易與順勢參與的位置，依價格反應選擇目前方向。",
        entry_decision={"action": "open_now", "side": side, "entry_price": str(entry),
            "stop_loss": str(entry - direction), "take_profit": str(entry + 2 * direction),
            "trigger": "依本次現價與價格反應評估提前參與。",
            "invalidation": "觸及建議止損，原計畫失效。", "reason": "先依價格位置判斷方向。",
            "basis_level_ids": []})
    def create(**kwargs):
        sent = json.loads(kwargs["input"][0]["content"])
        assert (sent["risk_tolerance"], sent["trading_style"], sent["directional_bias"]) == (
            "high", "left", "bearish")
        return SimpleNamespace(output=[], output_text=json.dumps(payload, ensure_ascii=False))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr("trade_helper.agent.OpenAI", lambda **kwargs:
                        SimpleNamespace(responses=SimpleNamespace(create=create)))
    decision = analyze_with_tools(request, candles, quote, context)
    report = build_report(request, candles, quote, [], decision, context)
    validate_report(report)
    assert report["entry_decision"]["side"] == side
    assert report["preference_assessment"]["trading_style"] == "left"


def test_model_decimal_compaction_preserves_raw_evidence_and_reference_prices():
    request, candles, context, quote = fixture()
    trace = prepare_analysis_evidence(request, candles, quote, context)
    raw = deepcopy(trace)
    payload = agent_context(request, candles, quote, context, prepared_trace=trace)
    assert trace == raw
    evidence = payload["precomputed_evidence"]
    assert len(json.dumps(evidence["technical_snapshot"])) < len(json.dumps(raw[0]["result"]))
    levels = next(run["result"] for run in trace if run["tool"] == "support_resistance")
    assert evidence["support_resistance"] == levels
    assert compact_indicator_values({"price": "0.000001234567890123456789"})["price"] == "0.00000123456789012"


def test_flat_price_rsi_is_neutral():
    candles = [{"close": "100"}] * 80
    assert rsi(candles, {}, {"period": 14})["value"] == "50.00"


@pytest.mark.parametrize("over_budget", [False, True])
def test_precomputed_evidence_does_not_consume_extra_call_budget(monkeypatch, over_budget):
    request, candles, context, quote = fixture()
    trace = prepare_analysis_evidence(request, candles, quote, context)
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) <= 4 or over_budget:
            if len(calls) == 5:
                assert kwargs["tool_choice"] == "none"
            return SimpleNamespace(output=[SimpleNamespace(type="function_call", name="rsi",
                arguments=json.dumps({"reason": "再次核對既有數值", "period": 14, "timeframe": "1h"}),
                call_id="extra-" + str(len(calls)))],
                usage=SimpleNamespace(input_tokens=100, output_tokens=20))
        return SimpleNamespace(output=[], output_text=final_response(trace),
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr("trade_helper.agent.OpenAI", lambda **kwargs:
                        SimpleNamespace(responses=SimpleNamespace(create=create)))
    if over_budget:
        with pytest.raises(ValueError, match="AI analysis exceeded the tool-call limit"):
            analyze_with_tools(request, candles, quote, context)
    else:
        decision = analyze_with_tools(request, candles, quote, context)
        assert decision["analysis_execution"]["model_requests"] == 5
        assert decision["analysis_execution"]["additional_tool_calls"] == 4
        assert decision["analysis_execution"]["input_tokens"] == 500
        assert decision["analysis_execution"]["output_tokens"] == 100
        assert len(decision["tool_trace"]) == 9


def test_macro_compaction_sends_actuals_once_and_preserves_sources():
    request, candles, context, quote = fixture()
    now = datetime.fromisoformat(quote["observed_at"])
    def actual(identifier, age):
        return {"id": identifier, "kind": "cpi", "metric": "cpi_yoy", "label": "CPI 年增率",
                "period": "2026-08", "value": "3.4", "previous_value": "3.1", "unit": "percent",
                "source": "bls", "source_url": "https://www.bls.gov/developers/api_signature.htm",
                "published_at": (now - timedelta(days=age)).isoformat(),
                "ingested_at": (now - timedelta(days=age)).isoformat(),
                "method": "published_series", "version": 1, "content_hash": "a" * 64}
    events = {"official_actuals": [actual("recent", 3), actual("older", 34)],
              "status": "partial", "events": []}
    quote["event_context"] = events
    trace = prepare_analysis_evidence(request, candles, quote, context)
    original = deepcopy(events)
    payload = agent_context(request, candles, quote, context, prepared_trace=trace)
    assert events == original
    assert "official_actuals" not in payload["official_event_context"]
    macro = payload["monthly_macro_context"]
    assert [item["id"] for item in macro["directional_evidence"]] == ["actual:recent"]
    assert [item["id"] for item in macro["latest_actuals"]] == ["actual:older"]
    evidence = macro["directional_evidence"][0]
    assert evidence["value"] == "3.4" and evidence["previous_value"] == "3.1"
    assert evidence["source_url"] == original["official_actuals"][0]["source_url"]
