import math
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from trade_helper import market_reference
from trade_helper.market_reference import fetch_market_reference, linkage

BTC, ETH, SOL = "binance:perp:BTCUSDT", "binance:perp:ETHUSDT", "binance:perp:SOLUSDT"
END = datetime(2026, 10, 1, 12, tzinfo=UTC)
CUTOFF = END + timedelta(minutes=5)


def rows(returns, *, timeframe="1h", start_price=100.0, end=END):
    """Closed candles ending at `end` whose closes follow the given log returns."""
    step = {"1h": timedelta(hours=1), "4h": timedelta(hours=4)}[timeframe]
    prices = [start_price]
    for value in returns:
        prices.append(prices[-1] * math.exp(value))
    first_open = end - step * len(prices)
    result = []
    for index, price in enumerate(prices):
        opened = first_open + step * index
        result.append({"open_time": opened.isoformat(), "close_time": (opened + step).isoformat(),
                       "open": f"{price:.6f}", "high": f"{price * 1.001:.6f}",
                       "low": f"{price * 0.999:.6f}", "close": f"{price:.6f}", "volume": "10"})
    return result


def wave(count, scale=0.01, phase=0.0):
    return [scale * math.sin(index * 0.7 + phase) for index in range(count)]


def test_linkage_measures_correlation_and_beta_on_aligned_returns():
    base = wave(150)
    same = linkage(rows(base), rows(base), "1h")
    assert (same["correlation"], same["beta"], same["linkage"]) == ("1.00", "1.00", "strong")
    assert same["aligned_returns"] == market_reference.CORRELATION_WINDOW
    doubled = linkage(rows([value * 2 for value in base]), rows(base), "1h")
    assert (doubled["correlation"], doubled["beta"]) == ("1.00", "2.00")
    opposite = linkage(rows([-value for value in base]), rows(base), "1h")
    assert (opposite["correlation"], opposite["linkage"]) == ("-1.00", "strong")
    unrelated = linkage(rows(wave(150, phase=1.6)), rows(base), "1h")
    assert unrelated["linkage"] in {"weak", "moderate"}


def test_linkage_needs_enough_shared_closed_candles():
    short = linkage(rows(wave(20)), rows(wave(20)), "1h")
    assert short == {"correlation": None, "beta": None, "aligned_returns": 20,
                     "linkage": "unknown", "reason": "insufficient_overlap"}
    # Candles from a different period share no timestamps, so nothing is compared.
    shifted = linkage(rows(wave(150)), rows(wave(150), end=END - timedelta(days=30)), "1h")
    assert shifted["reason"] == "insufficient_overlap"


def fake_fetch(series):
    calls = []

    def fetch(market, timeframe, *, limit):
        calls.append((market, timeframe, limit))
        if market not in series:
            raise httpx.ConnectError("unavailable")
        return series[market](timeframe)
    return fetch, calls


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("TRADE_MARKET_REFERENCE_ENABLED", "1")


def test_pair_gets_btc_and_eth_trend_structure_and_linkage(monkeypatch, enabled):
    base = wave(299)
    fetch, calls = fake_fetch({BTC: lambda tf: rows(base, timeframe=tf, start_price=60000),
                               ETH: lambda tf: rows(base, timeframe=tf, start_price=3000)})
    monkeypatch.setattr(market_reference, "fetch_candles", fetch)
    pair = [value * 1.5 for value in base]
    context = fetch_market_reference(SOL, "1h", rows(pair), rows(pair, timeframe="4h"), CUTOFF)
    assert context["status"] == "available"
    assert context["timeframes"] == ["1h", "4h"]
    assert sorted(calls) == sorted((market, tf, market_reference.REFERENCE_CANDLES)
                                   for market in (BTC, ETH) for tf in ("1h", "4h"))
    btc = context["references"][BTC]
    assert btc["status"] == "available"
    frame = btc["timeframes"]["1h"]
    assert frame["status"] == "available"
    assert frame["trend"] in {"bullish", "bearish", "mixed"}
    assert frame["structure"] in {"rising", "falling", "mixed", "insufficient"}
    assert (frame["correlation"], frame["beta"], frame["linkage"]) == ("1.00", "1.50", "strong")
    assert frame["change_candles"] == market_reference.CORRELATION_WINDOW


def test_reference_market_skips_itself_and_reports_the_other(monkeypatch, enabled):
    fetch, calls = fake_fetch({ETH: lambda tf: rows(wave(299), timeframe=tf)})
    monkeypatch.setattr(market_reference, "fetch_candles", fetch)
    context = fetch_market_reference(BTC, "1h", rows(wave(299)), rows(wave(299), timeframe="4h"), CUTOFF)
    assert context["references"][BTC] == {"status": "self", "reason": "analyzed_market"}
    assert {market for market, _, _ in calls} == {ETH}
    assert context["status"] == "available"


def test_unavailable_and_stale_sources_are_labelled_not_invented(monkeypatch, enabled):
    stale = END - timedelta(hours=6)
    fetch, _ = fake_fetch({ETH: lambda tf: rows(wave(299), timeframe=tf, end=stale if tf == "1h" else END)})
    monkeypatch.setattr(market_reference, "fetch_candles", fetch)
    context = fetch_market_reference(SOL, "1h", rows(wave(299)), rows(wave(299), timeframe="4h"), CUTOFF)
    assert context["references"][BTC]["status"] == "unavailable"
    assert context["references"][BTC]["timeframes"]["1h"]["reason"] == "source_unavailable_or_invalid"
    assert context["references"][ETH]["timeframes"]["1h"]["reason"] == "stale_closed_candles"
    assert context["references"][ETH]["timeframes"]["4h"]["status"] == "available"
    assert context["references"][ETH]["status"] == "partial"
    assert context["status"] == "partial"
    assert "correlation" not in context["references"][BTC]["timeframes"]["1h"]


def test_candles_after_the_quote_cutoff_are_ignored(monkeypatch, enabled):
    future = END + timedelta(hours=3)
    fetch, _ = fake_fetch({BTC: lambda tf: rows(wave(299), timeframe=tf, end=future),
                           ETH: lambda tf: rows(wave(299), timeframe=tf, end=future)})
    monkeypatch.setattr(market_reference, "fetch_candles", fetch)
    context = fetch_market_reference(SOL, "1h", rows(wave(299)), rows(wave(299), timeframe="4h"), CUTOFF)
    frame = context["references"][BTC]["timeframes"]["1h"]
    assert datetime.fromisoformat(frame["last_candle_at"]) <= CUTOFF


def test_disabled_reference_makes_no_requests(monkeypatch):
    monkeypatch.setenv("TRADE_MARKET_REFERENCE_ENABLED", "0")
    fetch, calls = fake_fetch({})
    monkeypatch.setattr(market_reference, "fetch_candles", fetch)
    context = fetch_market_reference(SOL, "1h", [], [], CUTOFF)
    assert context["status"] == "unavailable" and context["reason"] == "disabled"
    assert calls == []


def test_worker_sends_the_reference_to_the_model_and_saves_it_in_the_report(monkeypatch, tmp_path):
    import json
    from decimal import Decimal
    from types import SimpleNamespace

    from fastapi.testclient import TestClient

    from trade_helper.api import app
    from trade_helper.worker import run_once

    from .test_analysis import sample_candles

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    reference = {"version": market_reference.VERSION, "market_id": SOL, "status": "available",
                 "references": {BTC: {"status": "available", "timeframes": {
                     "1h": {"status": "available", "trend": "bearish", "correlation": "0.83",
                            "beta": "1.21", "linkage": "strong"}}}}}
    received = []
    monkeypatch.setattr("trade_helper.worker.fetch_market_reference",
                        lambda market, timeframe, candles, context_candles, cutoff:
                        received.append((market, timeframe, len(candles), len(context_candles))) or reference)
    inputs = []
    raw = {"market": "Pair structure is weak.", "strategy": "Wait.", "agent_stance": "neutral",
           "evidence_tools": [], "entry_decision": {"action": "wait", "reason": "No confirmation yet."},
           "macro_outlook": {"stance": "neutral", "reason": "No macro edge.", "evidence_ids": []}}

    def create(**kwargs):
        inputs.append(kwargs["input"])
        return SimpleNamespace(output=[], output_text=json.dumps(raw))
    monkeypatch.setattr("trade_helper.agent.OpenAI",
                        lambda **_: SimpleNamespace(responses=SimpleNamespace(create=create)))
    monkeypatch.setattr("trade_helper.worker.fetch_candles",
                        lambda _, tf, *, limit: sample_candles(recent=True, timeframe=tf))
    monkeypatch.setattr("trade_helper.worker.fetch_quote",
                        lambda *_: {"price": "120", "observed_at": datetime.now(UTC).isoformat()})
    monkeypatch.setattr("trade_helper.worker.fetch_forming_candle", lambda *_: None)
    monkeypatch.setattr("trade_helper.worker.fetch_order_book", lambda *_: None)
    monkeypatch.setattr("trade_helper.worker.fetch_tick_size", lambda *_: Decimal("0.1"))
    with TestClient(app) as client:
        created = client.post("/api/v1/analyses", json={"kind": "market", "market_id": SOL, "timeframe": "1h"},
                              headers={"Idempotency-Key": "market-reference-flow"}).json()
        assert run_once()
        result = client.get(f"/api/v1/analyses/{created['id']}").json()
    assert result["status"] == "completed", result.get("error")
    assert received and received[0][:2] == (SOL, "1h") and received[0][2] > 0 and received[0][3] > 0
    assert result["report"]["market_reference"] == reference
    model_context = json.loads(inputs[0][0]["content"])
    assert model_context["market_reference"] == reference
