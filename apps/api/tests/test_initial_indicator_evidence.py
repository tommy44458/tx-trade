import json
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from trade_helper.agent import analyze_with_tools
from trade_helper.analysis import build_report
from trade_helper.indicator_preferences import INITIAL_INDICATOR_DEFAULT_PARAMETERS
from trade_helper.indicators import TOOL_FUNCTIONS
from trade_helper.technical_snapshot import (
    ADDITIONAL_INDICATORS,
    additional_tool_schemas,
    build_technical_snapshot,
    execute_additional_indicator,
    prepare_analysis_evidence,
)
from trade_helper.timeframes import analysis_timeframes, higher_timeframes

from .test_higher_timeframes import higher_rows
from .test_technical_snapshot import final_response, fixture


def all_frame_fixture(primary="1h", monthly_count=180):
    request, candles, _, quote = fixture(primary=primary, count=180)
    cutoff = datetime.fromisoformat(quote["observed_at"])
    quote["higher_timeframe_candles"] = {
        frame: {"candles": higher_rows(frame, cutoff, monthly_count if frame == "1M" else 180)}
        for frame in higher_timeframes(primary)}
    return request, candles, quote["higher_timeframe_candles"][higher_timeframes(primary)[0]]["candles"], quote


@pytest.mark.parametrize("primary", ["1h", "4h", "12h", "1d"])
def test_selected_indicators_run_once_per_frame_without_modifying_market_data(monkeypatch, primary):
    request, candles, context, quote = all_frame_fixture(primary)
    request["initial_indicators"] = list(INITIAL_INDICATOR_DEFAULT_PARAMETERS)
    original_data = deepcopy((request, candles, context, quote))
    called = []
    for name in INITIAL_INDICATOR_DEFAULT_PARAMETERS:
        original = TOOL_FUNCTIONS[name]
        def count(rows, value, args, *, _name=name, _original=original):
            called.append((_name, rows[0]["open_time"], rows[-1]["close_time"]))
            return _original(rows, value, args)
        monkeypatch.setitem(TOOL_FUNCTIONS, name, count)
    trace = prepare_analysis_evidence(request, candles, quote, context)
    snapshot = trace[0]["result"]
    assert len(called) == 28
    assert len(set(called)) == 28
    assert (request, candles, context, quote) == original_data
    assert not snapshot["optional_indicator_catalog"]["not_precomputed"]
    assert snapshot["initial_indicator_selection"]["names"] == request["initial_indicators"]
    for frame in analysis_timeframes(primary):
        for name, params in INITIAL_INDICATOR_DEFAULT_PARAMETERS.items():
            value = snapshot["timeframes"][frame]["indicators"][name]
            assert value["parameters"] == params
            assert value["execution_source"] == "precomputed_selected"
            assert value["timeframe"] == frame
            assert value["analysis_as_of"] == quote["observed_at"]
    schemas = additional_tool_schemas(snapshot)
    assert {schema["name"] for schema in schemas} == set(ADDITIONAL_INDICATORS)
    assert all("already in each available timeframe" in schema["description"]
               for schema in schemas if schema["name"] in request["initial_indicators"])


def test_empty_selection_never_executes_advanced_tools(monkeypatch):
    request, candles, context, quote = all_frame_fixture()
    request["initial_indicators"] = []
    for name in INITIAL_INDICATOR_DEFAULT_PARAMETERS:
        monkeypatch.setitem(TOOL_FUNCTIONS, name, lambda *_args: pytest.fail("Unchecked tool ran"))
    snapshot = build_technical_snapshot(request, candles, quote, context)
    assert snapshot["initial_indicator_selection"]["names"] == []
    assert set(snapshot["optional_indicator_catalog"]["not_precomputed"]) == set(INITIAL_INDICATOR_DEFAULT_PARAMETERS)


def test_selected_result_is_reused_and_an_unselected_tool_remains_callable(monkeypatch):
    request, candles, context, quote = all_frame_fixture()
    request["initial_indicators"] = ["bollinger"]
    snapshot = build_technical_snapshot(request, candles, quote, context)
    saved = deepcopy(snapshot)
    monkeypatch.setitem(TOOL_FUNCTIONS, "bollinger", lambda *_args: pytest.fail("Selected calculation repeated"))
    result = execute_additional_indicator("bollinger", {"reason": "Reuse initial measurement", "timeframe": "4h", "period": 20, "multiplier": 2}, request, candles, quote, context, snapshot)
    assert result["execution_source"] == "agent_requested_cached"
    assert result["result"] == snapshot["timeframes"]["4h"]["indicators"]["bollinger"]
    extra = execute_additional_indicator("obv", {"reason": "Check volume participation", "timeframe": "1h", "period": 20}, request, candles, quote, context, snapshot)
    assert extra["execution_source"] == "agent_requested"
    assert snapshot == saved
    assert "obv" not in snapshot["timeframes"]["1h"]["indicators"]


def test_frozen_parameters_are_used_instead_of_current_defaults():
    request, candles, context, quote = all_frame_fixture()
    request.update(initial_indicators=["obv"], initial_indicator_parameters={"obv": {"period": 50}}, initial_indicator_catalog_version="saved-catalog")
    snapshot = build_technical_snapshot(request, candles, quote, context)
    assert snapshot["initial_indicator_selection"]["parameters"] == {"obv": {"period": 50}}
    assert snapshot["initial_indicator_selection"]["catalog_version"] == "saved-catalog"
    assert snapshot["timeframes"]["1h"]["indicators"]["obv"]["period"] == 50


def test_short_monthly_history_keeps_selected_tools_with_honest_unavailability():
    request, candles, context, quote = all_frame_fixture("1d", monthly_count=8)
    request["initial_indicators"] = ["adx_dmi", "obv", "fibonacci"]
    snapshot = build_technical_snapshot(request, candles, quote, context)
    monthly = snapshot["timeframes"]["1M"]["indicators"]
    for name in ("adx_dmi", "obv"):
        assert monthly[name]["status"] == "unavailable"
        assert monthly[name]["warmup_status"] == "insufficient"
    assert monthly["fibonacci"]["status"] == "unavailable"
    assert not monthly["fibonacci"].get("anchors")
    assert snapshot["initial_indicator_selection"]["names"] == ["fibonacci", "adx_dmi", "obv"]


def test_first_model_request_and_saved_report_contain_selected_values_without_extra_calls(monkeypatch):
    request, candles, context, quote = all_frame_fixture()
    request["initial_indicators"] = ["adx_dmi", "obv"]
    trace = prepare_analysis_evidence(request, candles, quote, context)
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        payload = json.loads(kwargs["input"][0]["content"])
        snapshot = payload["precomputed_evidence"]["technical_snapshot"]
        assert snapshot["initial_indicator_selection"]["names"] == ["adx_dmi", "obv"]
        for frame in analysis_timeframes("1h"):
            for name in request["initial_indicators"]:
                assert snapshot["timeframes"][frame]["indicators"][name]["execution_source"] == "precomputed_selected"
        assert "Review all selected measurements" in kwargs["instructions"]
        return SimpleNamespace(output=[], output_text=final_response(trace))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setattr("trade_helper.agent.OpenAI", lambda **_kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)))
    decision = analyze_with_tools(request, candles, quote, context, prepared_trace=trace)
    assert len(requests) == 1
    assert decision["analysis_execution"]["additional_tool_calls"] == 0
    assert decision["analysis_execution"]["initial_indicator_calculations"] == 8
    report = build_report(request, candles, quote, [], decision, context)
    assert report["technical_snapshot"] == trace[0]["result"]
    assert report["technical_snapshot"]["timeframes"]["1h"]["indicators"]["obv"]["obv"]
