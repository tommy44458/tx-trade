"""Two-week, forward-only reaction study for historical support/resistance zones.

This is a zone-reaction study, not an executable futures P&L simulation.
"""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal

from trade_helper.market import fetch_candles
from trade_helper.support_levels import historical_levels, wilder_atr

LOOKBACK = 300
OUTCOME_BARS = 4
REACTION_ATR = Decimal("0.5")
MARKETS = ("BTCUSDT", "ETHUSDT")
TIMEFRAMES = ("1h", "4h")


def _rolling_extreme(history: list[dict], kind: str, atr: Decimal) -> dict:
    """Simple predeclared baseline, known at the decision close."""
    field = "low" if kind == "support" else "high"
    price = (min if kind == "support" else max)(
        Decimal(candle[field]) for candle in history[-20:]
    )
    padding = atr * Decimal("0.15")
    return {"kind": kind, "low": str(price - padding), "high": str(price + padding),
            "center": str(price)}


def _outcome(future: list[dict], zone: dict, kind: str, atr: Decimal) -> str:
    low, high = Decimal(zone["low"]), Decimal(zone["high"])
    for candle in future:
        close = Decimal(candle["close"])
        if kind == "support":
            if close > high + REACTION_ATR * atr:
                return "favorable"
            if close < low - REACTION_ATR * atr:
                return "adverse"
        else:
            if close < low - REACTION_ATR * atr:
                return "favorable"
            if close > high + REACTION_ATR * atr:
                return "adverse"
    return "unresolved"


def evaluate(candles: list[dict], timeframe: str) -> dict:
    if len(candles) < LOOKBACK + OUTCOME_BARS + 1:
        raise ValueError("Insufficient candles for warmup and forward evaluation")
    last_close = datetime.fromisoformat(candles[-1]["close_time"])
    cutoff = last_close - timedelta(days=14)
    first = next(index for index, candle in enumerate(candles)
                 if datetime.fromisoformat(candle["open_time"]) >= cutoff)
    first = max(first, LOOKBACK)
    last = len(candles) - OUTCOME_BARS
    result = {}
    for method in ("pivot_cluster", "rolling_20_extreme"):
        for kind in ("support", "resistance"):
            key = f"{method}_{kind}"
            counts = {"candidates": 0, "touched": 0, "favorable": 0,
                      "adverse": 0, "unresolved": 0}
            events = []
            next_allowed = first
            for end in range(first, last):
                if end < next_allowed:
                    continue
                history = candles[end - LOOKBACK:end]
                reference = Decimal(history[-1]["close"])
                atr = wilder_atr(history)
                if method == "pivot_cluster":
                    levels = historical_levels(history, timeframe, reference)
                    eligible = [level for level in levels if level["kind"] == kind
                                and level["touch_count"] >= 2
                                and abs(Decimal(level["center"]) - reference) <= 3 * atr]
                    if not eligible:
                        continue
                    zone = eligible[0]
                else:
                    zone = _rolling_extreme(history, kind, atr)
                    if abs(Decimal(zone["center"]) - reference) > 3 * atr:
                        continue
                counts["candidates"] += 1
                touch_candle = candles[end]
                low, high = Decimal(zone["low"]), Decimal(zone["high"])
                if Decimal(touch_candle["low"]) > high or Decimal(touch_candle["high"]) < low:
                    continue
                counts["touched"] += 1
                outcome = _outcome(candles[end:end + OUTCOME_BARS], zone, kind, atr)
                counts[outcome] += 1
                events.append({"signal_close": history[-1]["close_time"],
                               "touch_open": touch_candle["open_time"],
                               "zone_low": zone["low"], "zone_high": zone["high"],
                               "outcome": outcome})
                # A physical touch may otherwise be counted in several consecutive snapshots.
                next_allowed = end + OUTCOME_BARS
            result[key] = {**counts, "events": events}
    return {"first_evaluated_open": candles[first]["open_time"],
            "last_evaluated_open": candles[last - 1]["open_time"],
            "last_available_close": candles[-1]["close_time"], **result}


def main() -> None:
    for symbol in MARKETS:
        for timeframe in TIMEFRAMES:
            candles = fetch_candles(f"binance:perp:{symbol}", timeframe, 1000)
            digest = hashlib.sha256(json.dumps(candles, sort_keys=True).encode()).hexdigest()
            report = evaluate(candles, timeframe)
            summary = {key: {name: value for name, value in item.items() if name != "events"}
                       for key, item in report.items() if isinstance(item, dict)}
            print(json.dumps({"market": symbol, "timeframe": timeframe,
                              "candles": len(candles), "candles_sha256": digest,
                              **{key: value for key, value in report.items()
                                 if not isinstance(value, dict)}, **summary}))


if __name__ == "__main__":
    main()
