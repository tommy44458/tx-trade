"""Replay v3 zones against width-matched extrema on frozen one-year candles.

The close-based outcome is an exploratory friction measure, never a trading P&L.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backtest_level_friction_2w import (
    HORIZON,
    LOOKBACK,
    _eligible,
    _event,
    _width_matched_extreme,
)

from trade_helper.support_levels import wilder_atr
from trade_helper.support_levels_v3 import historical_levels_v3

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data" / "backtests" / "friction_2025-09_2026-08"
START = datetime(2025, 9, 1, tzinfo=UTC)
END = datetime(2026, 9, 1, tzinfo=UTC)
TICKS = {"BTCUSDT": Decimal("0.10"), "ETHUSDT": Decimal("0.01")}


def _blank() -> dict:
    return {"candidates": 0, "touches": 0, "no_cross_4": 0,
            "immediate_clean_pass": 0, "cross_within_4": 0,
            "delayed_cross": 0, "reentered_after_cross": 0}


def evaluate(candles: list[dict], symbol: str, timeframe: str) -> dict:
    first = next(i for i, candle in enumerate(candles)
                 if datetime.fromisoformat(candle["open_time"]) >= START)
    end = next((i for i, candle in enumerate(candles)
                if datetime.fromisoformat(candle["open_time"]) >= END), len(candles))
    if first < LOOKBACK:
        raise ValueError("Insufficient pre-evaluation warmup")
    last = end - HORIZON + 1
    methods = ("v3_independent", "width_matched_20_extreme")
    counts = {f"{method}_{kind}": _blank() for method in methods
              for kind in ("support", "resistance")}
    monthly: dict[str, dict[str, dict]] = {}
    events = []
    next_allowed = {key: first for key in counts}
    for index in range(first, last):
        history = candles[index - LOOKBACK:index]
        reference = Decimal(history[-1]["close"])
        atr = wilder_atr(history)
        v3 = historical_levels_v3(history, timeframe, reference, TICKS[symbol],
                                  f"binance:perp:{symbol}")
        for kind in ("support", "resistance"):
            eligible = [zone for zone in v3["levels"] if zone["kind"] == kind
                        and zone["pivot_count"] >= 2
                        and zone["independent_touch_count"] >= 1
                        and _eligible(zone, reference, atr, kind)]
            if not eligible:
                continue
            zone = eligible[0]
            width = Decimal(zone["high"]) - Decimal(zone["low"])
            baseline = _width_matched_extreme(history, kind, width)
            for method, candidate in (("v3_independent", zone),
                                      ("width_matched_20_extreme", baseline)):
                key = f"{method}_{kind}"
                if index < next_allowed[key] or not _eligible(candidate, reference, atr, kind):
                    continue
                month = candles[index]["open_time"][:7]
                aggregate = counts[key]
                monthly_record = monthly.setdefault(month, {}).setdefault(key, _blank())
                aggregate["candidates"] += 1
                monthly_record["candidates"] += 1
                event = _event(candles, index, candidate, kind)
                if event is None:
                    continue
                aggregate["touches"] += 1
                monthly_record["touches"] += 1
                for name in ("no_cross_4", "immediate_clean_pass", "cross_within_4",
                             "delayed_cross", "reentered_after_cross"):
                    aggregate[name] += event[name]
                    monthly_record[name] += event[name]
                events.append({"method": method, "kind": kind, "market": symbol,
                               "timeframe": timeframe, "signal_close": history[-1]["close_time"],
                               "touch_open": candles[index]["open_time"],
                               "level_id": zone["id"] if method == "v3_independent" else None,
                               "zone_low": candidate["low"], "zone_high": candidate["high"],
                               "distance_atr": float(abs(Decimal(candidate["center"]) - reference) / atr),
                               "width_atr": float(width / atr),
                               "approach_speed_atr": float(abs(
                                   reference - Decimal(history[-4]["close"])) / atr),
                               **{name: event[name] for name in (
                                   "no_cross_4", "immediate_clean_pass", "cross_within_4",
                                   "delayed_cross", "reentered_after_cross")}})
                next_allowed[key] = index + HORIZON
    return {"market": symbol, "timeframe": timeframe,
            "first_evaluated_open": candles[first]["open_time"],
            "last_evaluated_open": candles[last - 1]["open_time"],
            "v3_rule": "pivot_count>=2 and independent_touch_count>=1",
            "tick_size": str(TICKS[symbol]), "tick_size_note": "Current Binance PRICE_FILTER; historical changes unknown",
            "counts": counts, "by_month": monthly, "events": events}


def main() -> None:
    for symbol in TICKS:
        for timeframe in ("1h", "4h"):
            candles = json.loads((DATA_DIR / f"{symbol}_{timeframe}.json").read_text())
            result = evaluate(candles, symbol, timeframe)
            (DATA_DIR / f"v3_{symbol}_{timeframe}_result.json").write_text(
                json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            print(json.dumps({"market": symbol, "timeframe": timeframe,
                              "counts": result["counts"]}), flush=True)


if __name__ == "__main__":
    main()
