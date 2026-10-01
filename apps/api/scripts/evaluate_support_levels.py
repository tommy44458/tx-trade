"""Exploratory walk-forward zone reaction counts; not a trading backtest."""

import json
from decimal import Decimal

from trade_helper.market import fetch_candles
from trade_helper.support_levels import historical_levels, wilder_atr

LOOKBACK = 300
STRIDE = 10
ACTIVATION_BARS = 6
OUTCOME_BARS = 4
REACTION_ATR = Decimal("0.5")


def evaluate(candles: list[dict], timeframe: str) -> dict:
    counts = {"candidates": 0, "touched": 0, "favorable_first": 0,
              "adverse_first": 0, "unresolved": 0}
    for end in range(LOOKBACK, len(candles) - ACTIVATION_BARS - OUTCOME_BARS, STRIDE):
        history = candles[end - LOOKBACK:end]
        reference = Decimal(history[-1]["close"])
        atr = wilder_atr(history)
        levels = historical_levels(history, timeframe, reference)
        for kind in ("support", "resistance"):
            eligible = [level for level in levels if level["kind"] == kind
                        and level["touch_count"] >= 2
                        and abs(Decimal(level["center"]) - reference) <= 3 * atr]
            if not eligible:
                continue
            zone = eligible[0]
            counts["candidates"] += 1
            low, high = Decimal(zone["low"]), Decimal(zone["high"])
            future = candles[end:end + ACTIVATION_BARS + OUTCOME_BARS]
            touch = next((index for index, candle in enumerate(future[:ACTIVATION_BARS])
                          if Decimal(candle["low"]) <= high and Decimal(candle["high"]) >= low), None)
            if touch is None:
                continue
            counts["touched"] += 1
            for candle in future[touch:touch + OUTCOME_BARS]:
                close = Decimal(candle["close"])
                favorable = close > high + REACTION_ATR * atr if kind == "support" else close < low - REACTION_ATR * atr
                adverse = close < low - REACTION_ATR * atr if kind == "support" else close > high + REACTION_ATR * atr
                if favorable:
                    counts["favorable_first"] += 1
                    break
                if adverse:
                    counts["adverse_first"] += 1
                    break
            else:
                counts["unresolved"] += 1
    return counts


def main() -> None:
    for symbol in ("BTCUSDT", "ETHUSDT"):
        for timeframe in ("1h", "4h"):
            candles = fetch_candles(f"binance:perp:{symbol}", timeframe, 1000)
            print(json.dumps({"market": symbol, "timeframe": timeframe,
                              "first_close": candles[0]["close_time"],
                              "last_close": candles[-1]["close_time"],
                              "closed_candles": len(candles), **evaluate(candles, timeframe)}))


if __name__ == "__main__":
    main()
