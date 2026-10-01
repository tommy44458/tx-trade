from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trade_helper.agent import agent_context, fallback_analysis
from trade_helper.analysis import build_report
from trade_helper.current_candle import current_candle_context
from trade_helper.indicators import execute_tool
from trade_helper.market import fetch_forming_candle
from trade_helper.report_contract import validate_report

from .test_analysis import sample_candles


def _snapshot():
    candles = sample_candles(recent=True)
    previous = Decimal(candles[-1]["close"])
    opened = datetime.fromisoformat(candles[-1]["close_time"]) + timedelta(milliseconds=1)
    observed = datetime.now(UTC)
    forming = {
        "open_time": opened.isoformat(),
        "close_time": (opened + timedelta(hours=1) - timedelta(milliseconds=1)).isoformat(),
        "fetched_at": (observed - timedelta(seconds=2)).isoformat(),
        "open": str(previous), "high": str(previous + 5), "low": str(previous - 5),
        "close": str(previous + 1), "volume": "15",
    }
    quote = {"price": str(previous + 2), "tick_size": "0.1",
             "snapshot_hash": "a" * 64, "observed_at": observed.isoformat(),
             "forming_candle": forming}
    return candles, quote


def test_forming_candle_context_uses_quote_and_marks_unconfirmed():
    candles, quote = _snapshot()
    context = current_candle_context(candles, quote, "1h")
    assert context["status"] == "available"
    assert context["confirmation"] == "unclosed"
    assert context["price_vs_open"] == "above"
    assert context["quote_inside_observed_range"] is True
    assert context["quote_price"] == quote["price"]
    assert context["candle"]["volume"] == "15"
    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": "1h", "leverage": 5}
    assert agent_context(request, candles, quote, None)["current_candle"] == context
    selected = execute_tool("strategy_candidates", {"reason": "Check current price"},
                            candles, quote, request)
    assert selected["result"]["current_candle"] == context


@pytest.mark.parametrize("mutation", ["wrong_period", "future_fetch", "no_candle", "wrong_duration", "at_close"])
def test_unusable_partial_candle_falls_back_to_quote_only(mutation):
    candles, quote = _snapshot()
    if mutation == "wrong_period":
        quote["forming_candle"]["open_time"] = (
            datetime.fromisoformat(quote["forming_candle"]["open_time"]) +
            timedelta(hours=1)).isoformat()
    elif mutation == "future_fetch":
        quote["forming_candle"]["fetched_at"] = (
            datetime.fromisoformat(quote["observed_at"]) + timedelta(seconds=1)).isoformat()
    elif mutation == "wrong_duration":
        quote["forming_candle"]["close_time"] = (
            datetime.fromisoformat(quote["forming_candle"]["close_time"]) + timedelta(hours=3)).isoformat()
    elif mutation == "at_close":
        quote["observed_at"] = quote["forming_candle"]["close_time"]
    else:
        quote["forming_candle"] = None
    context = current_candle_context(candles, quote, "1h")
    assert context["status"] == "quote_only"
    assert context["candle"] is None
    assert context["price_vs_last_close"] == "above"


def test_report_rejects_changed_current_candle_result():
    candles, quote = _snapshot()
    request = {"market_id": "binance:perp:BTCUSDT", "timeframe": "1h", "leverage": 5}
    decision = fallback_analysis(request, candles, quote)
    report = build_report(request, candles, quote, [], decision)
    assert report["current_candle"]["status"] == "available"
    validate_report(report)
    tampered = deepcopy(report)
    tampered["current_candle"]["price_vs_open"] = "below"
    with pytest.raises(ValueError, match="Current candle context"):
        validate_report(tampered)


def test_fetch_forming_candle_reads_unclosed_row(monkeypatch):
    opened = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    close_ms = int((opened + timedelta(hours=1)).timestamp() * 1000) - 1
    row = [int(opened.timestamp() * 1000), "100", "106", "98", "104", "15",
           close_ms, "1500", 5, "8", "800", "0"]

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return [row]

    monkeypatch.setattr("trade_helper.market.httpx.get", lambda *args, **kwargs: Response())
    candle = fetch_forming_candle("binance:perp:BTCUSDT", "1h")
    assert candle["open"] == "100"
    assert candle["volume"] == "15"
    assert candle["fetched_at"]
