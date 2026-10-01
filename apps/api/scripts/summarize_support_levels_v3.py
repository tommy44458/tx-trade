"""Distance/speed-stratified descriptive comparison with month-block uncertainty."""

import json
import random
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[3] / "data" / "backtests" / "friction_2025-09_2026-08"
DISTANCE_EDGES = (0, 0.5, 1, 1.5, 3.01)
SPEED_EDGES = (0, 0.5, 1, 2, 100)
METHODS = ("v3_independent", "width_matched_20_extreme")


def _bin(value: float, edges: tuple[float, ...]) -> int:
    for index in range(len(edges) - 1):
        if edges[index] <= value < edges[index + 1]:
            return index
    raise ValueError(f"Value {value} outside predeclared strata")


def _stratified(events: list[dict], timeframe: str, months: list[str]) -> dict:
    multiplier = {month: months.count(month) for month in set(months)}
    groups: dict[tuple, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    raw = {method: [0, 0, 0] for method in METHODS}
    for event in events:
        if event["timeframe"] != timeframe:
            continue
        weight = multiplier.get(event["touch_open"][:7], 0)
        if not weight:
            continue
        method = event["method"]
        raw[method][0] += weight
        raw[method][1] += weight * int(event["no_cross_4"])
        raw[method][2] += weight * int(event["immediate_clean_pass"])
        key = (event["market"], event["kind"],
               _bin(event["distance_atr"], DISTANCE_EDGES),
               _bin(event["approach_speed_atr"], SPEED_EDGES))
        groups[key][method][0] += weight
        groups[key][method][1] += weight * int(event["no_cross_4"])
    weighted_delta = matched_weight = matched_cells = 0
    for methods in groups.values():
        v3, baseline = methods[METHODS[0]], methods[METHODS[1]]
        if v3[0] < 5 or baseline[0] < 5:
            continue
        weight = min(v3[0], baseline[0])
        weighted_delta += weight * (v3[1] / v3[0] - baseline[1] / baseline[0])
        matched_weight += weight
        matched_cells += 1
    return {"raw": raw, "matched_cells": matched_cells,
            "matched_weight": matched_weight,
            "adjusted_no_cross_delta_pp": 100 * weighted_delta / matched_weight
            if matched_weight else None}


def main() -> None:
    results = [json.loads(path.read_text()) for path in DATA_DIR.glob("v3_*_result.json")]
    if len(results) != 4:
        raise ValueError("Four market/timeframe v3 replay files are required")
    events = [event for result in results for event in result["events"]]
    months = sorted({event["touch_open"][:7] for event in events})
    if len(months) != 12:
        raise ValueError("Expected twelve evaluation months")
    rng = random.Random(20260928)
    output = {}
    for timeframe in ("1h", "4h"):
        point = _stratified(events, timeframe, months)
        samples = []
        for _ in range(2000):
            resampled = [rng.choice(months) for _ in months]
            value = _stratified(events, timeframe, resampled)["adjusted_no_cross_delta_pp"]
            if value is not None:
                samples.append(value)
        samples.sort()
        point["month_block_interval_95_pp"] = [samples[int(0.025 * len(samples))],
                                                 samples[int(0.975 * len(samples))]]
        point["note"] = "Exploratory; strata have different event times and are not paired trades"
        output[timeframe] = point
    (DATA_DIR / "v3_stratified_summary.json").write_text(json.dumps(output, indent=2))
    print(json.dumps(output))


if __name__ == "__main__":
    main()
