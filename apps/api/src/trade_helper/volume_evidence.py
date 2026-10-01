"""Causal main-timeframe candle volume evidence for support/resistance episodes."""

from statistics import median

from .market_store import SECONDS, iso, stamp


def _values(candle: dict) -> tuple[float | None, float | None]:
    quote, buy = candle.get("quote_volume"), candle.get("taker_buy_quote_volume")
    if quote is None or buy is None:
        return None, None
    return float(quote), float(buy)


def _baseline(history: list[dict], window: int = 2, windows: int = 20) -> dict:
    """Use only complete, non-overlapping main-timeframe windows before the touch."""
    if len(history) < window * windows:
        return {"status": "unavailable", "reason": "insufficient_prior_candles",
                "sample_count": len(history) // window}
    samples = []
    for offset in range(windows, 0, -1):
        part = history[-offset * window:-(offset - 1) * window or None]
        values = [_values(c)[0] for c in part]
        if len(values) != window or any(q is None for q in values):
            return {"status": "unavailable", "reason": "missing_historical_quote_volume",
                    "sample_count": len(samples)}
        samples.append(sum(values))
    expected = median(samples)
    return {"status": "available" if expected > 0 else "unavailable",
            "reason": None if expected > 0 else "zero_volume_baseline",
            "sample_count": windows, "expected_quote_volume": expected,
            "method": "previous_20_nonoverlapping_2bar_windows"}


def _bar(candle: dict) -> dict:
    q, b = _values(candle)
    return {"t": stamp(candle["open_time"]), "o": float(candle["open"]),
            "h": float(candle["high"]), "l": float(candle["low"]),
            "c": float(candle["close"]), "q": q, "b": b}


def start_episode(zone: dict, candle: dict, atr: float, timeframe: str,
                  tick: float, history: list[dict], gap: bool = False) -> dict:
    if timeframe not in {"1h", "4h", "12h", "1d"}:
        raise ValueError("Reaction volume requires a supported main timeframe")
    opened = stamp(candle["open_time"])
    prior = _baseline(history)
    return {"id": f"{zone['id']}:r{zone['revision']}:{opened}",
            "zone_id": zone["id"], "revision": zone["revision"], "kind": zone["kind"],
            "low": zone["low"], "high": zone["high"], "formation_atr": zone.get("formation_atr", atr),
            "atr_at_touch": atr, "epsilon": max(2 * tick, 0.05 * atr),
            "timeframe": timeframe, "window_bars": 2, "horizon_bars": 4,
            "start_ms": opened, "touch_time_range": [iso(opened), candle["close_time"]],
            "pre_touch": {"baseline": prior, "pivot_count": zone.get("pivot_count", 0),
                          "revision": zone["revision"], "available_at": iso(opened - 1)},
            "baseline": prior, "bars": [], "persistent_cross": None,
            "done": False, "status": "observing", "gap_cross": gap,
            "quality_flags": ["main_timeframe_candle_touch_order_unknown", "whole_candle_flow_proxy"] +
                             (["gap_cross"] if gap else [])}


def observe_episode(event: dict, candle: dict) -> None:
    if event["done"]:
        return
    bar = _bar(candle)
    bars = event["bars"]
    step = SECONDS[event["timeframe"]] * 1000
    if bars and bar["t"] != bars[-1]["t"] + step:
        event.update(done=True, status="censored", end_reason="main_timeframe_gap",
                     ended_at=iso(bar["t"]), available_at=iso(bar["t"]))
        return
    bars.append(bar)
    event["available_at"] = candle["close_time"]
    if len(bars) < 2:
        return
    support = event["kind"] == "support"
    far = event["low"] - event["epsilon"] if support else event["high"] + event["epsilon"]
    beyond = [b["c"] < far if support else b["c"] > far for b in bars]
    crossed = beyond[-2] and beyond[-1]
    if crossed:
        event["persistent_cross"] = {"started_at": iso(bars[-2]["t"]),
                                     "available_at": candle["close_time"]}
    first = bars[:2]
    baseline = event["baseline"]
    valid = all(b["q"] is not None and b["b"] is not None for b in first)
    q = sum(b["q"] for b in first) if valid else None
    b = sum(b["b"] for b in first) if valid else None
    rvol = q / baseline["expected_quote_volume"] if valid and baseline["status"] == "available" else None
    sign = -1 if support else 1
    event["volume_evidence"] = {"status": "available" if rvol is not None else "unavailable",
                                "rvol": rvol, "quote_volume": q, "unit": "USDT",
                                "baseline": baseline, "directional_pressure":
                                sign * (2 * b - q) / q if q and b is not None else None,
                                "window_bars": 2, "timeframe": event["timeframe"],
                                "source": f"Binance {event['timeframe']} taker quote volume",
                                "scope": "whole_main_timeframe_candles_not_volume_at_zone_price",
                                "available_at": iso(bars[1]["t"] + step - 1),
                                "coverage": 1 if valid else 0}
    if crossed:
        # A later crossing cannot borrow the initial touch window's high volume.
        event["status"] = ("volume_crossed" if len(bars) == 2 and rvol is not None and rvol >= 1.5
                           else "crossed")
    elif event["gap_cross"]:
        event["status"] = "gap_cross"
    elif any(beyond):
        event["status"] = "cross_pending"
    elif rvol is None:
        event["status"] = "volume_unavailable"
    else:
        defended = all(b["c"] > event["high"] if support else b["c"] < event["low"] for b in first)
        if len(bars) > 2 and not (bars[-1]["c"] > event["high"] if support
                                 else bars[-1]["c"] < event["low"]):
            defended = False
        event["status"] = ("volume_defended" if rvol >= 1.5 and defended else
                           "volume_balanced" if rvol >= 1.5 else "no_volume_confirmation")
    if crossed or len(bars) >= event["horizon_bars"]:
        event.update(done=True, end_reason="persistent_cross" if crossed else "horizon",
                     ended_at=candle["close_time"])


def public_episode(event: dict) -> dict:
    result = {k: v for k, v in event.items() if k not in {"bars", "baseline"}}
    bars = event["bars"]
    if bars:
        support = event["kind"] == "support"
        near = event["high"] if support else event["low"]
        extreme = min(b["l"] for b in bars) if support else max(b["h"] for b in bars)
        sign = -1 if support else 1
        result["penetration_atr"] = max(0, sign * (extreme - near)) / event["atr_at_touch"]
        result["recovery_atr"] = max(0, -sign * (bars[-1]["c"] - near)) / event["atr_at_touch"]
        result["observed_bars"] = len(bars)
        result["inside_close_bars"] = sum(event["low"] <= b["c"] <= event["high"] for b in bars)
    result.setdefault("volume_evidence", {"status": "pending" if not event["done"] else "unavailable",
                                           "reason": "two_bar_reaction_incomplete", "unit": "USDT",
                                           "timeframe": event["timeframe"]})
    return result
