"""Compare frozen one-year v2/v3/v4 runs on closed main-timeframe candles."""

import json
import random
from collections import Counter, defaultdict
from decimal import Decimal
from statistics import median

from replay_v4 import read_candles

from trade_helper.config import data_dir
from trade_helper.market_store import digest, stamp
from trade_helper.support_levels_v4 import CONFIG_HASH

ROOT = data_dir() / "backtests" / "v4_main_tf_year_2025-09_2026-08"
METHODS = ("v2", "v3", "v4_price_rank", "v4_volume_rank", "width_matched_20_extreme")


def month_block_intervals(monthly: dict) -> dict:
    rng = random.Random(20260928)
    result = {}
    for timeframe, by_month in monthly.items():
        months = sorted(by_month)
        result[timeframe] = {}
        for other in ("v2", "v3", "v4_price_rank"):
            differences = []
            for _ in range(4000):
                sampled = [rng.choice(months) for _ in months]
                a = Counter()
                b = Counter()
                for month in sampled:
                    a.update(by_month[month]["v4_volume_rank"])
                    b.update(by_month[month][other])
                if a["fourbar_evaluable"] and b["fourbar_evaluable"]:
                    differences.append(a["no_cross_4"] / a["fourbar_evaluable"] -
                                       b["no_cross_4"] / b["fourbar_evaluable"])
            differences.sort()
            result[timeframe][other] = {
                "difference": sum(by_month[m]["v4_volume_rank"]["no_cross_4"] for m in months) /
                              sum(by_month[m]["v4_volume_rank"]["fourbar_evaluable"] for m in months) -
                              sum(by_month[m][other]["no_cross_4"] for m in months) /
                              sum(by_month[m][other]["fourbar_evaluable"] for m in months),
                "month_block_95_interval": [differences[int(0.025 * len(differences))],
                                             differences[int(0.975 * len(differences))]],
            }
    return result


def four_bars(event: dict, coarse: list[dict], index: dict[int, int]) -> dict | None:
    decision = stamp(event["decision_at"]) + 1
    start = index[decision]
    future = coarse[start:start + 4]
    if len(future) != 4 or event["gap_cross"]:
        return None
    low, high = Decimal(str(event["low"])), Decimal(str(event["high"]))
    crossed = [Decimal(candle["close"]) < low if event["kind"] == "support"
               else Decimal(candle["close"]) > high for candle in future]
    return {"no_cross_4": not any(crossed),
            "immediate_clean_pass": crossed[0] and crossed[1]}


def run() -> dict:
    rows = {}
    pairs = defaultdict(lambda: defaultdict(Counter))
    touches_by_tf = defaultdict(lambda: defaultdict(list))
    monthly = defaultdict(lambda: defaultdict(lambda: defaultdict(Counter)))
    for symbol in ("BTCUSDT", "ETHUSDT"):
        market = f"binance:perp:{symbol}"
        for timeframe in ("1h", "4h"):
            result = json.loads((ROOT / f"{symbol}_{timeframe}_result.json").read_text())
            if result["config_hash"] != CONFIG_HASH or result["evaluation_version"] != "v4_main_tf_year_1":
                raise ValueError("Version mismatch")
            coarse = read_candles(market, timeframe,
                                  result["manifest"]["coarse_from"], result["manifest"]["end"])
            if len(coarse) < 300 or digest(coarse) != result["source"]["coarse_sha256"]:
                raise ValueError("Coarse source changed")
            index = {stamp(c["open_time"]): i for i, c in enumerate(coarse)}
            counts = {method: Counter(result["counts"].get(method, {})) for method in METHODS}
            event_keys = defaultdict(dict)
            if Counter(event["method"] for event in result["events"]) != Counter({
                method: result["counts"][method]["touch_or_gap_events"] for method in METHODS}):
                raise ValueError("Event totals differ from report counts")
            for event in result["events"]:
                method = event["method"]
                touches_by_tf[timeframe][method].append(event)
                event_keys[(event["decision_at"], event["kind"])][method] = event
                metric = four_bars(event, coarse, index)
                if metric is not None:
                    counts[method]["fourbar_evaluable"] += 1
                    monthly[timeframe][event["decision_at"][:7]][method]["fourbar_evaluable"] += 1
                    for key, value in metric.items():
                        counts[method][key] += int(value)
                        monthly[timeframe][event["decision_at"][:7]][method][key] += int(value)
            for events in event_keys.values():
                for other in ("v2", "v3"):
                    a, b = events.get("v4_volume_rank"), events.get(other)
                    if a and b:
                        ma, mb = four_bars(a, coarse, index), four_bars(b, coarse, index)
                        if ma and mb:
                            pair = pairs[timeframe][other]
                            pair["same_decision_touches"] += 1
                            pair["both_no_cross_4"] += int(ma["no_cross_4"] and mb["no_cross_4"])
                            pair["v4_only_no_cross_4"] += int(ma["no_cross_4"] and not mb["no_cross_4"])
                            pair["other_only_no_cross_4"] += int(mb["no_cross_4"] and not ma["no_cross_4"])
                            pair["both_cross_4"] += int(not ma["no_cross_4"] and not mb["no_cross_4"])
            rows[f"{symbol}_{timeframe}"] = {method: dict(counts[method]) for method in METHODS}
    pooled = {}
    for timeframe in ("1h", "4h"):
        pooled[timeframe] = {}
        for method in METHODS:
            total = Counter()
            for symbol in ("BTCUSDT", "ETHUSDT"):
                total.update(rows[f"{symbol}_{timeframe}"][method])
            pooled[timeframe][method] = dict(total)
    summary = {"window": "2025-09-01T00:00:00Z to 2026-09-01T00:00:00Z exclusive",
               "metric_note": "Four-bar metric is evaluated after the next closed main-timeframe candle touches the frozen zone. Different methods may choose different zones and decision times; no minute data is used.",
               "by_market": rows, "pooled": pooled,
               "touch_geometry": {tf: {method: {
                   "median_distance_atr": median(e["distance_atr"] for e in events),
                   "median_width_atr": median(e["width_atr"] for e in events)}
                   for method, events in by_tf.items()} for tf, by_tf in touches_by_tf.items()},
               "by_month_fourbar": {tf: {month: {method: dict(counts) for method, counts in by_method.items()}
                                         for month, by_method in by_month.items()}
                                    for tf, by_month in monthly.items()},
               "month_block_comparison": month_block_intervals(monthly),
               "same_decision_touch_pairs": {tf: {other: dict(value) for other, value in by_tf.items()}
                                             for tf, by_tf in pairs.items()}}
    path = ROOT / "comparison_summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps({"pooled": pooled, "same_decision_touch_pairs": summary["same_decision_touch_pairs"]},
                     ensure_ascii=False), flush=True)
    return summary


if __name__ == "__main__":
    run()
