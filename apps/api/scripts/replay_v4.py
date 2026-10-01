"""Causal support/resistance replay using only closed 1H/4H candles."""

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from trade_helper.config import data_dir
from trade_helper.db import connect
from trade_helper.market_store import SECONDS, digest, iso, stamp
from trade_helper.support_levels import historical_levels
from trade_helper.support_levels_v3 import historical_levels_v3
from trade_helper.support_levels_v4 import CONFIG_HASH, advance, new_state, snapshot
from trade_helper.volume_evidence import observe_episode, public_episode, start_episode


def read_candles(market: str, timeframe: str, start: int = 0, end: int = 2**62) -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT payload,sha256 FROM market_candles_v4 WHERE market=? AND timeframe=? AND open_ms>=? AND open_ms<? ORDER BY open_ms",
                          (market, timeframe, start, end)).fetchall()
    result = []
    for row in rows:
        candle = json.loads(row["payload"])
        if digest(candle) != row["sha256"]:
            raise ValueError("Cache checksum mismatch")
        result.append(candle)
    return result


def outcome(coarse: list[dict], index: int, zone: dict, atr: float,
            timeframe: str, tick: float) -> dict | None:
    """Observe the next four main-timeframe bars after a frozen decision."""
    if index >= len(coarse):
        return None
    first = coarse[index]
    low, high = float(zone["low"]), float(zone["high"])
    touch = float(first["low"]) <= high and float(first["high"]) >= low
    gap = (float(first["high"]) < low if zone["kind"] == "support"
           else float(first["low"]) > high)
    if not touch and not gap:
        return None
    frozen = {"id": zone.get("zone_id", zone.get("id", "baseline")),
              "kind": zone["kind"], "revision": zone.get("revision", 1),
              "low": low, "high": high, "formation_atr": float(zone.get("formation_atr", atr)),
              "pivot_count": zone.get("pivot_count", 2)}
    event = start_episode(frozen, first, atr, timeframe, tick, coarse[:index], gap=gap)
    for candle in coarse[index:index + 4]:
        observe_episode(event, candle)
        if event["done"]:
            break
    result = public_episode(event)
    result["censored"] = not event["done"] or event["status"] == "censored"
    result["gap_cross"] = gap
    result["immediate_persistent_cross"] = bool(event["persistent_cross"] and
                                              stamp(event["persistent_cross"]["started_at"]) ==
                                              stamp(first["open_time"]))
    return result


def evaluate(market: str, timeframe: str, days: int = 7, manifest: dict | None = None,
             coarse_input: list[dict] | None = None,
             evaluation_start: int | None = None, evaluation_end: int | None = None) -> dict:
    bounds = manifest or {}
    coarse = (coarse_input if coarse_input is not None else
              read_candles(market, timeframe, bounds.get("coarse_from", 0), bounds.get("end", 2**62)))
    if len(coarse) < 300:
        raise ValueError("Collect main-timeframe history first")
    step = SECONDS[timeframe] * 1000
    end = stamp(coarse[-1]["close_time"]) + 1
    if evaluation_end is not None:
        if end < evaluation_end:
            raise ValueError("Historical data ends before fixed evaluation end")
        end = evaluation_end
    coarse = [c for c in coarse if stamp(c["close_time"]) < end]
    source_hash = digest(coarse)
    if manifest and (source_hash != manifest["source_hash"] or CONFIG_HASH != manifest["config_hash"]):
        raise ValueError("Frozen data/config no longer matches replay manifest")
    start = evaluation_start if evaluation_start is not None else (end - days * 86400_000) // step * step
    if start % step or start >= end or stamp(coarse[0]["open_time"]) >= start:
        raise ValueError("Invalid evaluation start or insufficient warmup")
    tick = 0.1 if market.endswith("BTCUSDT") else 0.01
    state = new_state(market, timeframe, tick)
    before = [c for c in coarse if stamp(c["close_time"]) < start]
    state = advance(state, before, iso(start - 1))
    if state["coarse_count"] < 300:
        raise ValueError("Not enough pre-evaluation structure warmup")
    methods = ("v2", "v3", "v4_price_rank", "v4_volume_rank", "width_matched_20_extreme")
    counts = {method: Counter() for method in methods}
    monthly = {}
    allowed = {(method, kind): start for method in methods for kind in ("support", "resistance")}
    events = []
    history = list(before)
    first = len(before)
    for index in range(first, len(coarse)):
        candle = coarse[index]
        decision = stamp(candle["open_time"])
        reference = float(history[-1]["close"])
        atr = state["atr"]
        options = {
            "v2": [z for z in historical_levels(history[-300:], timeframe, Decimal(str(reference)))
                   if z["touch_count"] >= 2],
            "v3": [z for z in historical_levels_v3(history[-300:], timeframe, Decimal(str(reference)),
                                                    Decimal(str(tick)), market)["levels"]
                   if z["pivot_count"] >= 2 and z["independent_touch_count"] >= 1],
            "v4_price_rank": snapshot(state, reference, iso(decision - 1),
                                      rank_volume=False, include_testing=False)["levels"],
            "v4_volume_rank": snapshot(state, reference, iso(decision - 1),
                                       include_testing=False)["levels"],
        }
        for kind in ("support", "resistance"):
            picked = {}
            for method, zones in options.items():
                eligible = [z for z in zones if z["kind"] == kind and
                            (float(z["high"]) < reference if kind == "support" else
                             float(z["low"]) > reference) and
                            abs(float(z["center"]) - reference) <= 3 * atr]
                if eligible:
                    picked[method] = eligible[0]
            if "v4_price_rank" in picked:
                width = (float(picked["v4_price_rank"]["high"]) -
                         float(picked["v4_price_rank"]["low"]))
                center = (min(float(c["low"]) for c in history[-20:]) if kind == "support"
                          else max(float(c["high"]) for c in history[-20:]))
                low, high = center - width / 2, center + width / 2
                if (high < reference if kind == "support" else low > reference) and abs(center - reference) <= 3 * atr:
                    picked["width_matched_20_extreme"] = {"kind": kind, "low": low,
                                                          "high": high, "center": center}
            for method, zone in picked.items():
                if decision < allowed[method, kind]:
                    continue
                counts[method]["candidates"] += 1
                month_counts = monthly.setdefault(iso(decision)[:7], {}).setdefault(method, Counter())
                month_counts["candidates"] += 1
                result = outcome(coarse, index, zone, atr, timeframe, tick)
                if result is None:
                    counts[method]["untouched"] += 1
                    month_counts["untouched"] += 1
                    continue
                counts[method]["touch_or_gap_events"] += 1
                month_counts["touch_or_gap_events"] += 1
                counts[method]["censored"] += int(result["censored"])
                month_counts["censored"] += int(result["censored"])
                counts[method]["gap_cross"] += int(result["gap_cross"])
                if not result["censored"]:
                    crossed = bool(result["persistent_cross"])
                    for record in (counts[method], month_counts):
                        record["resolved"] += 1
                        record["persistent_cross"] += int(crossed)
                        record["no_persistent_cross"] += int(not crossed)
                        record["immediate_persistent_cross"] += int(result["immediate_persistent_cross"])
                events.append({"method": method, "decision_at": iso(decision - 1),
                               "distance_atr": abs(float(zone["center"]) - reference) / atr,
                               "width_atr": (float(zone["high"]) - float(zone["low"])) / atr,
                               "candidate_class": zone.get("evidence_class"), **result})
                allowed[method, kind] = result["start_ms"] + 4 * step
        state = advance(state, [candle], candle["close_time"])
        history.append(candle)
    frozen = {"market": market, "timeframe": timeframe, "days": days,
              "coarse_from": stamp(coarse[0]["open_time"]), "end": end,
              "source_hash": source_hash, "config_hash": CONFIG_HASH}
    return {"market": market, "timeframe": timeframe, "start": iso(start),
            "end_exclusive": iso(end), "config_hash": CONFIG_HASH,
            "source_hash": source_hash, "manifest": frozen,
            "evaluation_version": "main_tf_1", "structure_bars_at_first_decision": len(before),
            "tick_size": tick, "tick_note": "Current rule; not historical tick reconstruction",
            "note": "Closed 1H/4H volume only. Descriptive obstruction replay; candidate selection differs.",
            "counts": {method: dict(value) for method, value in counts.items()},
            "by_month": {month: {method: dict(value) for method, value in values.items()}
                         for month, values in monthly.items()}, "events": events}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", default="binance:perp:BTCUSDT")
    parser.add_argument("--timeframe", choices=("1h", "4h"), default="1h")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--manifest", help="Frozen JSON manifest from an earlier run")
    args = parser.parse_args()
    if not 1 <= args.days <= 365:
        parser.error("Replay days must be between 1 and 365")
    manifest = json.loads(Path(args.manifest).read_text()) if args.manifest else None
    if manifest:
        args.market, args.timeframe, args.days = manifest["market"], manifest["timeframe"], manifest["days"]
    result = evaluate(args.market, args.timeframe, args.days, manifest)
    directory = data_dir() / "backtests" / "v4_main_tf_replay"
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{args.market.rsplit(':', 1)[-1]}_{args.timeframe}_{result['manifest']['end']}_{CONFIG_HASH[:8]}"
    path = directory / f"{stem}_result.json"
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if path.exists() and path.read_text() != encoded:
        raise ValueError("Refusing to overwrite a different frozen replay result")
    path.write_text(encoded)
    (directory / f"{stem}_manifest.json").write_text(json.dumps(result["manifest"], indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "events"}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
