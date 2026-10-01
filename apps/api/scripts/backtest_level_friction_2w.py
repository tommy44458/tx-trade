"""Forward-only two-week study of whether zones slow a clean price crossing.

This measures close-based friction, not reversals or executable trade returns.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal

from trade_helper.market import fetch_candles
from trade_helper.support_levels import historical_levels, wilder_atr

LOOKBACK = 300
HORIZON = 4


def _beyond(close: Decimal, low: Decimal, high: Decimal, kind: str) -> bool:
    return close < low if kind == "support" else close > high


def _eligible(zone: dict, reference: Decimal, atr: Decimal, kind: str) -> bool:
    low, high = Decimal(zone["low"]), Decimal(zone["high"])
    on_approach_side = reference > high if kind == "support" else reference < low
    return on_approach_side and abs(Decimal(zone["center"]) - reference) <= 3 * atr


def _width_matched_extreme(history: list[dict], kind: str, width: Decimal) -> dict:
    field = "low" if kind == "support" else "high"
    center = (min if kind == "support" else max)(
        Decimal(candle[field]) for candle in history[-20:]
    )
    return {"kind": kind, "low": str(center - width / 2),
            "high": str(center + width / 2), "center": str(center)}


def _event(candles: list[dict], end: int, zone: dict, kind: str) -> dict | None:
    low, high = Decimal(zone["low"]), Decimal(zone["high"])
    touch = candles[end]
    if Decimal(touch["low"]) > high or Decimal(touch["high"]) < low:
        return None
    future = candles[end:end + HORIZON]
    crossed = [_beyond(Decimal(candle["close"]), low, high, kind) for candle in future]
    first_cross = next((index for index, value in enumerate(crossed) if value), None)
    reentered = first_cross is not None and any(
        not value for value in crossed[first_cross + 1:]
    )
    later_overlap = sum(Decimal(candle["low"]) <= high and
                        Decimal(candle["high"]) >= low for candle in future[1:])
    return {"immediate_clean_pass": crossed[0] and crossed[1],
            "cross_within_4": first_cross is not None,
            "no_cross_4": first_cross is None,
            "delayed_cross": first_cross is not None and first_cross > 0,
            "reentered_after_cross": reentered,
            "later_overlap_bars": later_overlap}


def _blank_counts() -> dict:
    return {"candidates": 0, "touches": 0, "immediate_clean_pass": 0,
            "cross_within_4": 0, "no_cross_4": 0,
            "delayed_cross": 0, "reentered_after_cross": 0,
            "later_overlap_bars": 0, "touch_width_atr_sum": 0.0}


def evaluate(candles: list[dict], timeframe: str, *, start: datetime | None = None,
             end_exclusive: datetime | None = None) -> dict:
    last_close = datetime.fromisoformat(candles[-1]["close_time"])
    cutoff = start or last_close - timedelta(days=14)
    first = max(LOOKBACK, next(index for index, candle in enumerate(candles)
                              if datetime.fromisoformat(candle["open_time"]) >= cutoff))
    end_index = (next((index for index, candle in enumerate(candles)
                       if datetime.fromisoformat(candle["open_time"]) >= end_exclusive),
                      len(candles)) if end_exclusive else len(candles))
    last = min(end_index, len(candles)) - HORIZON + 1
    if first >= last:
        raise ValueError("Insufficient forward candles")
    counts = {}
    by_month = {}
    next_allowed = {}
    for method in ("pivot_cluster", "width_matched_20_extreme"):
        for kind in ("support", "resistance"):
            key = f"{method}_{kind}"
            counts[key] = _blank_counts()
            next_allowed[key] = first
    for end in range(first, last):
        history = candles[end - LOOKBACK:end]
        reference = Decimal(history[-1]["close"])
        atr = wilder_atr(history)
        levels = historical_levels(history, timeframe, reference)
        for kind in ("support", "resistance"):
            eligible = [zone for zone in levels if zone["kind"] == kind
                        and zone["touch_count"] >= 2
                        and _eligible(zone, reference, atr, kind)]
            if not eligible:
                continue
            pivot_zone = eligible[0]
            width = Decimal(pivot_zone["high"]) - Decimal(pivot_zone["low"])
            baseline_zone = _width_matched_extreme(history, kind, width)
            for method, zone in (("pivot_cluster", pivot_zone),
                                 ("width_matched_20_extreme", baseline_zone)):
                key = f"{method}_{kind}"
                if end < next_allowed[key] or not _eligible(zone, reference, atr, kind):
                    continue
                record = counts[key]
                month = candles[end]["open_time"][:7]
                monthly = by_month.setdefault(month, {}).setdefault(key, _blank_counts())
                record["candidates"] += 1
                monthly["candidates"] += 1
                event = _event(candles, end, zone, kind)
                if event is None:
                    continue
                record["touches"] += 1
                monthly["touches"] += 1
                for name, value in event.items():
                    record[name] += value
                    monthly[name] += value
                record["touch_width_atr_sum"] += float(width / atr)
                monthly["touch_width_atr_sum"] += float(width / atr)
                next_allowed[key] = end + HORIZON
    return {"first_evaluated_open": candles[first]["open_time"],
            "last_evaluated_open": candles[last - 1]["open_time"],
            "by_month": by_month, **counts}


def main() -> None:
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for timeframe in ("1h", "4h"):
            candles = fetch_candles(f"binance:perp:{symbol}", timeframe, 1000)
            print(json.dumps({"symbol": symbol, "timeframe": timeframe,
                              **evaluate(candles, timeframe)}))


if __name__ == "__main__":
    main()
