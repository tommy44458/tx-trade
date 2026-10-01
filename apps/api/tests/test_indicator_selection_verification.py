"""Keep the live-verification corpus truthful without calling a model in tests."""

import importlib.util
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trade_helper.optional_indicators import bollinger, obv
from trade_helper.timeframes import candle_open

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify_indicator_selection.py"
SPEC = importlib.util.spec_from_file_location("indicator_selection_verification", SCRIPT)
verification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verification)


@pytest.fixture(scope="module")
def corpus():
    return verification.cases()


def test_preference_comparison_uses_identical_market_facts(corpus):
    first = corpus["aligned_trend"]
    second = corpus["same_market_left_high"]
    for key in ("candles", "context_candles", "quote", "origin"):
        assert first[key] == second[key]
    assert second["request"]["directional_bias"] == "bearish"
    assert second["request"]["risk_tolerance"] == "high"
    assert second["request"]["trading_style"] == "left"
    assert first["request"]["directional_bias"] is None


@pytest.mark.parametrize("case", ["aligned_trend", "compression", "weak_participation"])
def test_synthetic_higher_frames_aggregate_the_same_hourly_data(corpus, case):
    fixture = corpus[case]
    hourly = fixture["candles"]
    for timeframe, count in (("4h", 4), ("12h", 12), ("1d", 24)):
        higher = fixture["quote"]["higher_timeframe_candles"][timeframe]["candles"]
        for row in higher[-20:]:
            opened = datetime.fromisoformat(row["open_time"])
            components = [item for item in hourly if candle_open(datetime.fromisoformat(item["open_time"]), timeframe) == opened]
            assert len(components) == count
            assert row["open"] == components[0]["open"]
            assert row["close"] == components[-1]["close"]
            assert Decimal(row["high"]) == max(Decimal(item["high"]) for item in components)
            assert Decimal(row["low"]) == min(Decimal(item["low"]) for item in components)
            assert Decimal(row["volume"]) == sum(Decimal(item["volume"]) for item in components)
        assert all(datetime.fromisoformat(row["close_time"]) < verification.CUTOFF for row in higher)


def test_challenging_cases_have_the_intended_measurable_information(corpus):
    weak = obv(corpus["weak_participation"]["candles"], {}, {"period": 100})
    assert Decimal(weak["price_change_pct"]) > 0
    assert Decimal(weak["signed_volume_fraction"]) < 0
    rows = corpus["compression"]["candles"]
    recent = bollinger(rows, {}, {"period": 20, "multiplier": 2})
    earlier = bollinger(rows[:-150], {}, {"period": 20, "multiplier": 2})
    assert Decimal(recent["bandwidth_pct"]) < Decimal(earlier["bandwidth_pct"])


def test_historical_case_keeps_only_the_two_real_frames(corpus):
    btc = corpus["btc_0930"]
    assert btc["origin"] == "frozen_public_btc_20260930_2116_fixture"
    assert len(btc["candles"]) == len(btc["context_candles"]) == 300
    assert "higher_timeframe_candles" not in btc["quote"]
    cutoff = datetime.fromisoformat(btc["quote"]["observed_at"])
    assert all(datetime.fromisoformat(row["close_time"]) <= cutoff for row in btc["candles"])
