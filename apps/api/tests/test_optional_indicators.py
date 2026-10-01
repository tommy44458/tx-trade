"""Numerical fixtures and frozen-snapshot behavior; no model or network calls."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from trade_helper.indicators import TOOL_FUNCTIONS, tool_schema
from trade_helper.optional_indicators import (
    FUNCTIONS,
    adx_dmi,
    bollinger,
    donchian,
    fibonacci,
    keltner,
    obv,
    stochastic,
)
from trade_helper.prompts import resolve_prompt
from trade_helper.technical_snapshot import (
    ADDITIONAL_INDICATORS,
    DEFAULT_INDICATORS,
    additional_tool_schemas,
    build_technical_snapshot,
    execute_additional_indicator,
)
from trade_helper.timeframes import analysis_timeframes

from .test_higher_timeframes import higher_rows

EPSILON = Decimal("1e-23")
CUTOFF = datetime(2026, 10, 1, 13, 16, tzinfo=UTC)
PARAMETERS = {"bollinger": {"period": 20, "multiplier": 2},
              "fibonacci": {"lookback": 80, "width": 2, "direction": "auto"},
              "adx_dmi": {"period": 14, "adx_period": 14}, "obv": {"period": 20},
              "donchian": {"period": 20}, "keltner": {"period": 20, "atr_period": 14, "multiplier": 2},
              "stochastic": {"period": 14, "smooth_k": 3, "smooth_d": 3}}


def rows(closes, volumes=None, spread="1"):
    values = [Decimal(str(value)) for value in closes]
    result = higher_rows("1h", CUTOFF, len(values))
    for index, (row, close) in enumerate(zip(result, values)):
        row.update(open=str(close), close=str(close), high=str(close + Decimal(spread)),
                   low=str(close - Decimal(spread)), volume=str(volumes[index] if volumes is not None else 100))
    return result


def mirror(candles, axis=Decimal(300)):
    result = deepcopy(candles)
    for row in result:
        row.update(open=str(axis - Decimal(row["open"])), close=str(axis - Decimal(row["close"])),
                   high=str(axis - Decimal(row["low"])), low=str(axis - Decimal(row["high"])))
    return result


def decimal_fraction(value):
    return Decimal(value.numerator) / Decimal(value.denominator)


def test_bollinger_population_variance_multiplier_and_decimal_precision():
    result = bollinger(rows([1, 2, 3, 4], spread="0.01"), {}, {"period": 4, "multiplier": 2})
    assert Decimal(result["middle"]) == Decimal("2.5")
    assert abs(Decimal(result["standard_deviation"]) ** 2 - Decimal("1.25")) < EPSILON
    assert abs(Decimal(result["upper"]) - Decimal("2.5") - 2 * Decimal("1.25").sqrt()) < EPSILON
    assert Decimal(result["percent_b"]) > Decimal("0.8")
    narrow = bollinger(rows(["0.00000011", "0.00000012", "0.00000013", "0.00000014"], spread="0.000000001"),
                      {}, {"period": 4, "multiplier": 3})
    assert Decimal(narrow["middle"]) == Decimal("0.000000125")
    assert Decimal(narrow["standard_deviation"]) > 0
    constant = bollinger(rows([100] * 20, spread="0"), {}, {"period": 20, "multiplier": 2})
    assert constant["percent_b"] is None and Decimal(constant["bandwidth_pct"]) == 0


def test_adx_wilder_sma_seed_against_independent_fraction_fixture():
    # TR=(3,2,3,5,2), +DM=(2,1,0,4,0), -DM=(0,0,2,0,1).
    # First n=3 seed produces DX=20; later DXs are 700/11 and 1900/53.
    result = adx_dmi(rows([100, 102, 103, 101, 105, 104]), {}, {"period": 3, "adx_period": 3})
    expected = (Fraction(20) + Fraction(700, 11) + Fraction(1900, 53)) / 3
    assert abs(Decimal(result["adx"]) - decimal_fraction(expected)) < EPSILON
    assert abs(Decimal(result["plus_di"]) - 45) < EPSILON
    assert abs(Decimal(result["minus_di"]) - Decimal("21.25")) < EPSILON
    assert result["previous_adx"] is None  # This is the first eligible ADX.
    extended = adx_dmi(rows([100, 102, 103, 101, 105, 104, 106]), {}, {"period": 3, "adx_period": 3})
    assert abs(Decimal(extended["previous_adx"]) - decimal_fraction(expected)) < EPSILON
    assert abs(Decimal(extended["adx"]) - (decimal_fraction(expected) * 2 + Decimal(extended["dx"])) / 3) < EPSILON


def test_adx_strength_does_not_imply_direction_and_zero_range_is_defined():
    increasing = rows(range(100, 130))
    up = adx_dmi(increasing, {}, {"period": 14, "adx_period": 14})
    down = adx_dmi(mirror(increasing), {}, {"period": 14, "adx_period": 14})
    assert Decimal(up["adx"]) == Decimal(down["adx"]) == 100
    assert up["plus_di"] == down["minus_di"] and up["minus_di"] == down["plus_di"]
    flat = adx_dmi(rows([100] * 28, spread="0"), {}, {"period": 14, "adx_period": 14})
    assert all(Decimal(flat[key]) == 0 for key in ("adx", "plus_di", "minus_di"))
    assert flat["quality_flags"] == ["zero_directional_movement"]


def test_obv_unchanged_close_signing_window_fraction_and_zero_volume():
    candles = rows([100, 101, 101, 99, 102], [100, 10, 20, 30, 40])
    result = obv(candles, {}, {"period": 3})
    assert result["initial_obv"] == "0" and Decimal(result["obv"]) == 20
    assert Decimal(result["obv_change"]) == 10 and Decimal(result["window_volume"]) == 90
    assert Decimal(result["signed_volume_fraction"]) == Decimal(1) / 9
    assert Decimal(obv(mirror(candles), {}, {"period": 3})["obv"]) == -20
    empty = obv(rows([100, 101, 102, 103], [0] * 4), {}, {"period": 3})
    assert empty["signed_volume_fraction"] is None and Decimal(empty["obv"]) == 0


def test_donchian_breakout_uses_prior_channel_without_latest_bar():
    candles = rows([100, 100, 100, 110])
    result = donchian(candles, {}, {"period": 3})
    assert result["upper"] == "111" and result["previous_channel"]["upper"] == "101"
    assert result["previous_channel"]["end_at"] == candles[-2]["close_time"]
    assert result["last_close_vs_previous_channel"] == "above"
    assert donchian(mirror(candles), {}, {"period": 3})["last_close_vs_previous_channel"] == "below"
    flat = donchian(rows([100] * 4, spread="0"), {}, {"period": 3})
    assert flat["close_position_pct"] is None


def test_keltner_variant_ema_sma_seed_and_wilder_atr():
    result = keltner(rows([100, 102, 104, 106]), {}, {"period": 3, "atr_period": 2, "multiplier": 2})
    assert Decimal(result["middle"]) == 104 and Decimal(result["atr"]) == 3
    assert Decimal(result["upper"]) == 110 and Decimal(result["lower"]) == 98
    flat = keltner(rows([100] * 20, spread="0"), {}, {"period": 20, "atr_period": 14, "multiplier": 2})
    assert flat["close_position"] is None and Decimal(flat["bandwidth_pct"]) == 0


def test_stochastic_range_and_smoothing_do_not_invent_flat_momentum():
    result = stochastic(rows([100, 101, 102, 103]), {}, {"period": 3, "smooth_k": 1, "smooth_d": 2})
    assert all(Decimal(result[key]) == 75 for key in ("raw_k", "k", "d"))
    inverted = stochastic(mirror(rows([100, 101, 102, 103])), {}, {"period": 3, "smooth_k": 1, "smooth_d": 2})
    assert Decimal(result["k"]) + Decimal(inverted["k"]) == 100
    flat = stochastic(rows([100] * 18, spread="0"), {}, {"period": 14, "smooth_k": 3, "smooth_d": 3})
    assert flat["k"] is None and flat["d"] is None
    middle = stochastic(rows([100] * 18), {}, {"period": 14, "smooth_k": 3, "smooth_d": 3})
    assert Decimal(middle["k"]) == Decimal(middle["d"]) == 50


def fib_rows():
    return rows([110, 108, 100, 106, 112, 118, 120, 117, 115, 110, 114, 116], spread="0.5")


def test_fibonacci_confirmed_chronological_abc_prices_and_tick_rounding():
    candles = fib_rows()
    quote = {"tick_size": "0.1", "observed_at": CUTOFF.isoformat()}
    result = fibonacci(candles, quote, PARAMETERS["fibonacci"])
    a, b, c = (result["anchors"][key] for key in ("a", "b", "c"))
    assert a["price"] == "99.5" and b["price"] == "120.5" and c["price"] == "109.5"
    assert a["open_time"] < b["open_time"] < c["open_time"]
    assert all(point["confirmed_at"] <= candles[-1]["close_time"] for point in (a, b, c))
    assert Decimal(result["retracements"]["0.5"]["raw_price"]) == 110
    assert Decimal(result["retracements"]["0.618"]["raw_price"]) == Decimal("107.522")
    assert Decimal(result["retracements"]["0.618"]["price"]) == Decimal("107.5")
    assert Decimal(result["trend_extensions"]["levels"]["1"]["price"]) == Decimal("130.5")
    assert result["final_anchor_bars_ago"] == 2 and result["direction_scope"] != "current_market_direction"
    result_down = fibonacci(mirror(candles), quote, PARAMETERS["fibonacci"])
    for ratio in result["retracements"]:
        assert Decimal(result["retracements"][ratio]["raw_price"]) + Decimal(result_down["retracements"][ratio]["raw_price"]) == 300


def test_fibonacci_new_impulse_does_not_reuse_old_abc_or_future_extreme():
    quote = {"tick_size": "0.1", "observed_at": CUTOFF.isoformat()}
    candles = fib_rows()
    prior = fibonacci(candles, quote, PARAMETERS["fibonacci"])
    candles[-1]["high"] = "1000"  # Closed, but not yet a confirmed pivot.
    assert fibonacci(candles, quote, PARAMETERS["fibonacci"])["anchors"] == prior["anchors"]
    new = rows([110, 108, 100, 106, 112, 118, 120, 117, 115, 110, 114, 116, 122, 127, 130, 128, 126], spread="0.5")
    result = fibonacci(new, quote, PARAMETERS["fibonacci"])
    assert result["anchors"]["a"]["price"] == "109.5" and result["anchors"]["b"]["price"] == "130.5"
    assert result["anchors"]["c"] is None and result["trend_extensions"]["status"] == "unavailable"
    future = deepcopy(new)
    future[-1]["close_time"] = (CUTOFF + timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match="cutoff"):
        fibonacci(future, quote, PARAMETERS["fibonacci"])
    flat = fibonacci(rows([100] * 20), quote, PARAMETERS["fibonacci"])
    assert flat["status"] == "unavailable" and flat["reason"] == "no_confirmed_directional_pivot_pair"


@pytest.mark.parametrize("invalid_tick", ["0", "-0.1", "NaN", "Infinity", "not-a-tick"])
def test_fibonacci_rejects_invalid_exchange_tick(invalid_tick):
    with pytest.raises(ValueError, match="tick"):
        fibonacci(fib_rows(), {"tick_size": invalid_tick}, PARAMETERS["fibonacci"])


def frozen(primary="1h", count=100, monthly_count=None):
    frames = {frame: higher_rows(frame, CUTOFF, monthly_count if frame == "1M" and monthly_count is not None else count)
              for frame in analysis_timeframes(primary)}
    quote = {"price": "135.1234", "tick_size": "0.01", "observed_at": CUTOFF.isoformat(),
             "snapshot_hash": "a" * 64, "higher_timeframe_candles": {
                 frame: {"candles": candles, "requested_candles": count}
                 for frame, candles in frames.items() if frame != primary}}
    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": primary}
    secondary = analysis_timeframes(primary)[1]
    snapshot = build_technical_snapshot(request, frames[primary], quote, frames[secondary])
    return request, frames[primary], quote, frames[secondary], snapshot


def test_unused_tools_do_no_calculations_and_baseline_is_explicit(monkeypatch):
    for name in FUNCTIONS:
        monkeypatch.setitem(TOOL_FUNCTIONS, name, lambda *args: pytest.fail("Unused optional tool computed"))
    *_, snapshot = frozen()
    for frame in snapshot["timeframes"].values():
        assert set(frame["indicators"]) == set(DEFAULT_INDICATORS)
        assert not set(FUNCTIONS) & set(frame["indicators"])
    assert {"rsi", "macd", "volatility_atr", "volume_signal"} <= set(DEFAULT_INDICATORS)
    assert set(snapshot["optional_indicator_catalog"]["not_precomputed"]) == set(FUNCTIONS)


@pytest.mark.parametrize("primary", ["1h", "4h", "12h", "1d"])
def test_every_optional_tool_dispatches_all_four_frozen_frames_with_honest_metadata(primary):
    request, candles, quote, context, snapshot = frozen(primary)
    original = deepcopy(snapshot)
    for frame in analysis_timeframes(primary):
        for name, parameters in PARAMETERS.items():
            run = execute_additional_indicator(name, {"reason": "Test the unresolved price/volume hypothesis", "timeframe": frame, **parameters},
                                               request, candles, quote, context, snapshot)
            result = run["result"]
            assert result["timeframe"] == frame and result["analysis_as_of"] == quote["observed_at"]
            assert result["as_of"] == snapshot["timeframes"][frame]["last_closed_at"]
            assert result["warmup_status"] == "sufficient" and result["candle_count"] == 100
            expected_coverage = snapshot["timeframes"][frame]["coverage_status"]
            assert result["coverage_status"] == expected_coverage
            assert result["status"] == ("partial" if expected_coverage == "partial" else "available")
            assert run["execution_source"] == "agent_requested"
    assert snapshot == original
    schemas = additional_tool_schemas(snapshot)
    assert {schema["name"] for schema in schemas} == set(ADDITIONAL_INDICATORS)
    assert all(set(schema["parameters"]["properties"]["timeframe"]["enum"]) == set(analysis_timeframes(primary)) for schema in schemas)


def test_monthly_partial_and_warmup_missing_values_are_not_fabricated():
    request, candles, quote, context, snapshot = frozen("1d", monthly_count=10)
    for name in ("bollinger", "adx_dmi", "obv", "stochastic"):
        result = execute_additional_indicator(name, {"reason": "Inspect monthly context", "timeframe": "1M", **PARAMETERS[name]},
                                             request, candles, quote, context, snapshot)["result"]
        assert result["status"] == "unavailable" and result["reason"] == "insufficient_closed_candles"
        assert result["warmup_status"] == "insufficient" and result["candle_count"] == 10
        assert result["coverage_status"] == "partial"
    request, candles, quote, context, snapshot = frozen("1d", monthly_count=30)
    result = execute_additional_indicator("adx_dmi", {"reason": "Inspect monthly context", "timeframe": "1M", **PARAMETERS["adx_dmi"]},
                                         request, candles, quote, context, snapshot)["result"]
    assert result["status"] == "partial" and result["warmup_status"] == "sufficient"
    del snapshot["timeframes"]["1M"]
    with pytest.raises(ValueError, match="timeframe"):
        execute_additional_indicator("obv", {"reason": "test", "timeframe": "1M", **PARAMETERS["obv"]},
                                     request, candles, quote, context, snapshot)


def test_identical_requests_reuse_cache_without_changing_snapshot_or_reason(monkeypatch):
    request, candles, quote, context, snapshot = frozen()
    cache = {}
    args = {"reason": "First hypothesis", "timeframe": "1h", **PARAMETERS["bollinger"]}
    first = execute_additional_indicator("bollinger", args, request, candles, quote, context, snapshot, cache=cache)
    monkeypatch.setitem(TOOL_FUNCTIONS, "bollinger", lambda *args: pytest.fail("Optional indicator recomputed"))
    second = execute_additional_indicator("bollinger", args | {"reason": "Recheck same hypothesis"},
                                         request, candles, quote, context, snapshot, cache=cache)
    assert second["execution_source"] == "agent_requested_cached" and second["result"] == first["result"]
    assert second["reason"] == "Recheck same hypothesis" and "bollinger" not in snapshot["timeframes"]["1h"]["indicators"]


@pytest.mark.parametrize("change", [{"period": True}, {"multiplier": True}, {"multiplier": float("nan")},
                                   {"multiplier": "2"}, {"timeframe": "1m"}, {"_request": {}}, {"reason": ""}])
def test_parameters_are_validated_before_calculation_or_cache(change):
    request, candles, quote, context, snapshot = frozen()
    with pytest.raises(ValueError):
        execute_additional_indicator("bollinger", {"reason": "test", "timeframe": "1h", **PARAMETERS["bollinger"], **change},
                                     request, candles, quote, context, snapshot, cache={})


def test_frozen_cutoff_and_series_identity_prevent_future_data():
    request, candles, quote, context, snapshot = frozen()
    candles[-1]["close_time"] = (CUTOFF + timedelta(hours=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        execute_additional_indicator("obv", {"reason": "test", "timeframe": "1h", **PARAMETERS["obv"]},
                                     request, candles, quote, context, snapshot)
    request, candles, quote, context, snapshot = frozen()
    quote["observed_at"] = (CUTOFF + timedelta(minutes=1)).isoformat()
    with pytest.raises(ValueError, match="identity"):
        execute_additional_indicator("obv", {"reason": "test", "timeframe": "1h", **PARAMETERS["obv"]},
                                     request, candles, quote, context, snapshot)


@pytest.mark.parametrize("locale", ["en-US", "zh-TW"])
def test_prompt_catalog_is_optional_and_bilingual_without_direction_bias(locale):
    instructions = resolve_prompt("strategy_market", prompt_locale=locale,
                                  inputs={"timeframe": "1h", "risk_tolerance": "high", "trading_style": "left"}).instructions
    assert all(name in instructions for name in FUNCTIONS)
    for name in FUNCTIONS:
        assert tool_schema(name)["strict"] is True
    assert "at most four" in instructions.lower() or "最多四次" in instructions
    assert "no extra calls" in instructions or "不追加工具" in instructions
    assert "guaranteed" in instructions or "不保證" in instructions
