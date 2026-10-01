"""Two-week v3 friction replay using five-minute bars for outcome ordering."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from backtest_level_friction_2w import HORIZON, LOOKBACK, _eligible, _width_matched_extreme

from trade_helper.market import fetch_candles_range
from trade_helper.support_levels import wilder_atr
from trade_helper.support_levels_v3 import historical_levels_v3

ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT / "data" / "backtests" / "fine_2026-09-14_2026-09-28"
START = datetime(2026, 9, 14, tzinfo=UTC)
END = datetime(2026, 9, 28, tzinfo=UTC)
WARMUPS = {"1h": datetime(2026, 8, 31, tzinfo=UTC),
           "4h": datetime(2026, 7, 15, tzinfo=UTC)}
TICKS = {"BTCUSDT": Decimal("0.10"), "ETHUSDT": Decimal("0.01")}


def _beyond(close: Decimal, low: Decimal, high: Decimal, kind: str) -> bool:
    return close < low if kind == "support" else close > high


def fine_event(fine: list[dict], first: int, entry_bars: int, horizon_bars: int,
               zone: dict, kind: str) -> dict | None:
    low, high = Decimal(zone["low"]), Decimal(zone["high"])
    future = fine[first:first + horizon_bars]
    if len(future) != horizon_bars:
        raise ValueError("Incomplete five-minute outcome window")
    touch_at = next((index for index, candle in enumerate(future[:entry_bars])
                     if Decimal(candle["low"]) <= high and Decimal(candle["high"]) >= low), None)
    if touch_at is None:
        return None
    cross_at = next((index for index, candle in enumerate(future[touch_at:], start=touch_at)
                     if _beyond(Decimal(candle["close"]), low, high, kind)), None)
    reentered = cross_at is not None and any(
        not _beyond(Decimal(candle["close"]), low, high, kind)
        for candle in future[cross_at + 1:]
    )
    minutes_to_cross = 5 * (cross_at - touch_at) if cross_at is not None else None
    return {"touch_open": future[touch_at]["open_time"],
            "first_cross_open": future[cross_at]["open_time"] if cross_at is not None else None,
            "minutes_to_first_cross": minutes_to_cross,
            "no_cross_horizon": cross_at is None,
            "cross_within_15m": minutes_to_cross is not None and minutes_to_cross <= 15,
            "reentered_after_cross": reentered}


def _load_coarse(symbol: str, timeframe: str) -> list[dict]:
    path = DATA_DIR / f"{symbol}_{timeframe}.json"
    if not path.exists():
        candles = fetch_candles_range(f"binance:perp:{symbol}", timeframe,
                                      WARMUPS[timeframe], END)
        path.write_text(json.dumps(candles, separators=(",", ":")))
    return json.loads(path.read_text())


def evaluate(coarse: list[dict], fine: list[dict], symbol: str, timeframe: str) -> dict:
    first = next(index for index, candle in enumerate(coarse)
                 if datetime.fromisoformat(candle["open_time"]) >= START)
    end = len(coarse) - HORIZON + 1
    if first < LOOKBACK or first >= end:
        raise ValueError("Insufficient coarse warmup or outcome data")
    fine_index = {candle["open_time"]: index for index, candle in enumerate(fine)}
    entry_bars = 12 if timeframe == "1h" else 48
    horizon_bars = entry_bars * HORIZON
    counts = {f"{method}_{kind}": {"candidates": 0, "touches": 0,
                                  "no_cross_horizon": 0, "cross_within_15m": 0,
                                  "reentered_after_cross": 0}
              for method in ("v3", "baseline") for kind in ("support", "resistance")}
    next_allowed = {key: first for key in counts}
    events = []
    for index in range(first, end):
        history = coarse[index - LOOKBACK:index]
        reference = Decimal(history[-1]["close"])
        atr = wilder_atr(history)
        levels = historical_levels_v3(history, timeframe, reference, TICKS[symbol],
                                      f"binance:perp:{symbol}")["levels"]
        for kind in ("support", "resistance"):
            selected = [zone for zone in levels if zone["kind"] == kind
                        and zone["pivot_count"] >= 2
                        and zone["independent_touch_count"] >= 1
                        and _eligible(zone, reference, atr, kind)]
            if not selected:
                continue
            zone = selected[0]
            width = Decimal(zone["high"]) - Decimal(zone["low"])
            baseline = _width_matched_extreme(history, kind, width)
            for method, candidate in (("v3", zone), ("baseline", baseline)):
                key = f"{method}_{kind}"
                if index < next_allowed[key] or not _eligible(candidate, reference, atr, kind):
                    continue
                counts[key]["candidates"] += 1
                start_at = fine_index.get(coarse[index]["open_time"])
                if start_at is None:
                    raise ValueError("Missing five-minute candle at coarse decision")
                outcome = fine_event(fine, start_at, entry_bars, horizon_bars,
                                     candidate, kind)
                if outcome is None:
                    continue
                counts[key]["touches"] += 1
                for name in ("no_cross_horizon", "cross_within_15m", "reentered_after_cross"):
                    counts[key][name] += outcome[name]
                events.append({"method": method, "kind": kind,
                               "signal_close": history[-1]["close_time"],
                               "zone_low": candidate["low"], "zone_high": candidate["high"],
                               "distance_atr": float(abs(Decimal(candidate["center"]) - reference) / atr),
                               **outcome})
                next_allowed[key] = index + HORIZON
    return {"market": symbol, "timeframe": timeframe, "counts": counts, "events": events}


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for symbol in TICKS:
        fine_path = DATA_DIR / f"{symbol}_5m.json"
        fine = json.loads(fine_path.read_text())
        fine_hash = hashlib.sha256(fine_path.read_bytes()).hexdigest()
        for timeframe in ("1h", "4h"):
            coarse = _load_coarse(symbol, timeframe)
            result = evaluate(coarse, fine, symbol, timeframe)
            result["five_minute_sha256"] = fine_hash
            (DATA_DIR / f"v3_{symbol}_{timeframe}_result.json").write_text(
                json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            print(json.dumps({"market": symbol, "timeframe": timeframe,
                              "counts": result["counts"]}), flush=True)


if __name__ == "__main__":
    main()
