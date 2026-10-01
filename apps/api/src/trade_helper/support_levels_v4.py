"""Incremental v4-candle structure and reaction engine; all observations are causal."""

from copy import deepcopy
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from statistics import median

from .market_store import SECONDS, digest, iso, stamp, validate_candle
from .volume_evidence import observe_episode, public_episode, start_episode

VERSION = "support_resistance_v4_candle_2_main_tf"
CONFIG = {"version": VERSION, "warmup_bars": 250, "pivot_width": 2,
          "cluster_atr": 0.35, "padding_atr": 0.15, "max_width_atr": 0.8,
          "rearm_atr": 0.5, "volume_high": 1.5, "volume_low": 0.7,
          "reaction_bars": 2, "episode_horizon_bars": 4,
          "volume_baseline": "previous_20_nonoverlapping_2bar_windows",
          "persistent_cross": "two_main_timeframe_closes_beyond_epsilon",
          "gap_touch": "counter_evidence_only", "late_defense": "last_close_must_remain_approach_side",
          "ttl_days": {"1h": 90, "4h": 365, "1d": 730}}
CONFIG_HASH = digest(CONFIG)


def new_state(market: str, timeframe: str, tick: float) -> dict:
    if timeframe not in CONFIG["ttl_days"] or not 0 < tick < float("inf"):
        raise ValueError("Invalid v4 timeframe/tick")
    return {"market": market, "timeframe": timeframe, "tick": tick,
            "config_hash": CONFIG_HASH, "coarse_count": 0, "coarse_last": None,
            "volume_events_from": None,
            "atr": None, "tr_seed": [], "history": [], "zones": [],
            "completed_events": [], "data_hash": digest([])}


def _round(price: float, tick: float, up: bool) -> float:
    t = Decimal(str(tick))
    return float((Decimal(str(price)) / t).to_integral_value(
        rounding=ROUND_CEILING if up else ROUND_FLOOR) * t)


def _excursion(zone: dict, close: float, atr: float | None = None) -> bool:
    distance = 0.5 * (atr or zone["formation_atr"])
    return (close > zone["high"] + distance if zone["kind"] == "support"
            else close < zone["low"] - distance)


def _make_zone(state: dict, kind: str, price: float, at: int, atr: float) -> dict:
    zone_id = "v4_" + digest([state["market"], state["timeframe"], CONFIG_HASH, kind, at, price])[:20]
    return {"id": zone_id, "kind": kind, "parent_id": None, "source": "confirmed_pivot",
            "low": _round(price - 0.15 * atr, state["tick"], False),
            "high": _round(price + 0.15 * atr, state["tick"], True),
            "formation_atr": atr, "prices": [price], "pivot_count": 1,
            "created_at": at, "confirmed_at": at, "revision": 1, "revisions": [],
            "state": "active", "break_streak": 0, "armed": False, "episode": None,
            "recent_events": [], "independent_touch_count": 0,
            "evidence_class": "structure_candidate", "flip_armed": False,
            "role_episode": None, "child_id": None}


def _archive_event(state: dict, zone: dict, key: str = "episode") -> dict:
    event = public_episode(zone[key])
    state["completed_events"].append(event)
    if key == "episode":
        zone["recent_events"] = (zone["recent_events"] + [event])[-2:]
    zone[key] = None
    return event


def _coarse(state: dict, candle: dict) -> None:
    validate_candle(candle, state["timeframe"])
    at = stamp(candle["close_time"])
    step = SECONDS[state["timeframe"]] * 1000
    if state["coarse_last"] is not None and at != state["coarse_last"] + step:
        raise ValueError("Coarse candle gap; state not advanced")
    if state["timeframe"] != "1d" and state["coarse_count"] >= CONFIG["warmup_bars"]:
        state["volume_events_from"] = state["volume_events_from"] or at
        _observe_zones(state, candle)
    hi, lo, close = (float(candle[k]) for k in ("high", "low", "close"))
    history = state["history"]
    if history:
        previous = float(history[-1]["close"])
        tr = max(hi - lo, abs(hi - previous), abs(lo - previous))
        if state["atr"] is None:
            state["tr_seed"].append(tr)
            if len(state["tr_seed"]) == 14:
                state["atr"] = sum(state["tr_seed"]) / 14
        else:
            state["atr"] = (state["atr"] * 13 + tr) / 14
    history.append(candle)
    del history[:-250]
    state["coarse_last"] = at
    state["coarse_count"] += 1
    state["data_hash"] = digest([state["data_hash"], candle])
    ttl = CONFIG["ttl_days"][state["timeframe"]] * 86400_000
    for zone in state["zones"]:
        if at - zone["confirmed_at"] > ttl:
            zone["state"] = "expired"
            for key in ("episode", "role_episode"):
                if zone[key]:
                    zone[key].update(done=True, status="censored", end_reason="zone_expired",
                                     ended_at=iso(at), available_at=iso(at))
                    _archive_event(state, zone, key)
        if zone["state"] in {"broken", "expired"}:
            continue
        far = close < zone["low"] if zone["kind"] == "support" else close > zone["high"]
        zone["break_streak"] = zone["break_streak"] + 1 if far else 0
        if zone["break_streak"] >= 2:
            zone.update(state="broken", broken_at=at, armed=False)
            if zone["episode"]:
                zone["episode"].update(done=True, end_reason="structure_broken", status="crossed",
                                       ended_at=iso(at), available_at=iso(at))
                _archive_event(state, zone)
    if state["coarse_count"] <= CONFIG["warmup_bars"] or not state["atr"]:
        return
    atr = state["atr"]
    for kind, field in (("support", "low"), ("resistance", "high")):
        prices = [float(c[field]) for c in history[-5:]]
        price = prices[2]
        pivot = (price < min(prices[:2]) and price <= min(prices[3:]) if kind == "support"
                 else price > max(prices[:2]) and price >= max(prices[3:]))
        if not pivot:
            continue
        matches = []
        for zone in state["zones"]:
            if zone["kind"] != kind or zone["state"] != "active":
                continue
            a0 = zone["formation_atr"]
            low = min(zone["low"], _round(price - 0.15 * a0, state["tick"], False))
            high = max(zone["high"], _round(price + 0.15 * a0, state["tick"], True))
            if abs(price - median(zone["prices"])) <= 0.35 * a0 and high - low <= 0.8 * a0:
                matches.append((abs(price - median(zone["prices"])), zone, low, high))
        if matches:
            _, zone, low, high = min(matches, key=lambda item: (item[0], item[1]["id"]))
            zone["revisions"].append({"revision": zone["revision"], "low": zone["low"],
                                      "high": zone["high"], "until": iso(at)})
            zone.update(low=low, high=high, revision=zone["revision"] + 1,
                        pivot_count=zone["pivot_count"] + 1, confirmed_at=at, break_streak=0)
            zone["prices"].append(price)
        else:
            zone = _make_zone(state, kind, price, at, atr)
            state["zones"].append(zone)
        if zone["episode"] is None:
            zone["armed"] = _excursion(zone, close)
        volumes = [float(c["quote_volume"]) for c in history[-23:-3] if c.get("quote_volume") is not None]
        q = history[-3].get("quote_volume")
        zone["formation_volume"] = {
            "status": "available" if q is not None and len(volumes) == 20 and median(volumes) > 0 else "unavailable",
            "rvol": float(q) / median(volumes) if q is not None and len(volumes) == 20 and median(volumes) > 0 else None,
            "source": "pivot whole-candle quote volume / previous 20 median",
            "unit": "ratio", "available_at": iso(at), "used_as_strength_bonus": False}


def _observe_zones(state: dict, candle: dict) -> None:
    high, low, close = (float(candle[k]) for k in ("high", "low", "close"))
    at = stamp(candle["close_time"])
    ttl = CONFIG["ttl_days"][state["timeframe"]] * 86400_000
    children = []
    for zone in state["zones"]:
        if zone["state"] == "expired" or at - zone["confirmed_at"] > ttl:
            continue
        is_role = zone["state"] == "broken"
        if is_role and zone["child_id"]:
            continue
        key, arm = ("role_episode", "flip_armed") if is_role else ("episode", "armed")
        target = {**zone, "kind": "resistance" if zone["kind"] == "support" else "support"} if is_role else zone
        overlap = low <= target["high"] and high >= target["low"]
        jumped = high < target["low"] if target["kind"] == "support" else low > target["high"]
        if zone[key] is None and zone[arm] and (overlap or jumped):
            zone[key] = start_episode(target, candle, state["atr"], state["timeframe"],
                                      state["tick"], state["history"], gap=not overlap)
            zone[arm] = False
            if not is_role:
                zone["independent_touch_count"] += 1
        if zone[key]:
            observe_episode(zone[key], candle)
            if zone[key]["done"]:
                event = _archive_event(state, zone, key)
                if is_role and event["status"] == "volume_defended":
                    at = stamp(event["available_at"])
                    child = _make_zone(state, target["kind"], median(zone["prices"]), at,
                                       zone["formation_atr"])
                    child.update(parent_id=zone["id"], source="confirmed_role_retest",
                                 low=zone["low"], high=zone["high"], pivot_count=0,
                                 recent_events=[event], independent_touch_count=1)
                    zone["child_id"] = child["id"]
                    children.append(child)
        elif _excursion(target, close):
            zone[arm] = True
        if not is_role:
            zone["evidence_class"] = evidence_class(zone)
    state["zones"].extend(children)


def evidence_class(zone: dict) -> str:
    events = zone["recent_events"]
    latest = public_episode(zone["episode"]) if zone.get("episode") else (events[-1] if events else None)
    if latest and latest["status"] in {"crossed", "volume_crossed", "cross_pending", "gap_cross"}:
        return "weakening"
    if (len(events) == 2 and all("penetration_atr" in e for e in events)
            and events[-1]["penetration_atr"] >= events[0]["penetration_atr"] + 0.1
            and events[-1]["recovery_atr"] < events[0]["recovery_atr"]):
        return "weakening"
    if events and events[-1]["status"] == "censored":
        return "volume_unavailable"
    for event in reversed(events):
        if event["status"] in {"crossed", "volume_crossed"}:
            break
        if event["status"] == "volume_defended":
            return "volume_supported"
    return "structure_candidate"


def advance(state: dict, candles: list[dict], as_of: str) -> dict:
    """State is copied so a rejected data batch cannot corrupt a persisted checkpoint."""
    state = deepcopy(state)
    if state["config_hash"] != CONFIG_HASH:
        raise ValueError("Algorithm/config mismatch")
    cutoff = stamp(as_of)
    events = sorted(((stamp(c["close_time"]), c) for c in candles
                     if (state["coarse_last"] is None or stamp(c["close_time"]) > state["coarse_last"])
                     and stamp(c["close_time"]) <= cutoff), key=lambda item: item[0])
    for _, payload in events:
        _coarse(state, payload)
    return state


def snapshot(state: dict, reference: float, as_of: str, parent: dict | None = None,
             rank_volume: bool = True, include_testing: bool = True) -> dict:
    if reference <= 0 or not state["atr"] or state["coarse_count"] <= CONFIG["warmup_bars"]:
        raise ValueError("Insufficient structure data")
    cutoff = stamp(as_of)
    if state["coarse_last"] > cutoff:
        raise ValueError("Cannot snapshot future state")
    levels, background = [], []
    parent_zones = parent["zones"] if parent else []
    for zone in state["zones"]:
        if zone["state"] != "active" or (zone["pivot_count"] < 2 and not zone["parent_id"]):
            continue
        inside = zone["low"] <= reference <= zone["high"]
        if inside and not include_testing:
            continue
        if not inside and (reference < zone["low"] if zone["kind"] == "support" else reference > zone["high"]):
            continue
        related = []
        for other in parent_zones:
            if other["state"] != "active" or other["kind"] != zone["kind"] or other["pivot_count"] < 2:
                continue
            width = min(zone["high"] - zone["low"], other["high"] - other["low"])
            if width > 0 and min(zone["high"], other["high"]) - max(zone["low"], other["low"]) >= 0.5 * width:
                related.append({"zone_id": other["id"], "timeframe": parent["timeframe"],
                                "low": other["low"], "high": other["high"]})
        distance = abs(median(zone["prices"]) - reference) / state["atr"]
        recent = public_episode(zone["episode"]) if zone["episode"] else (zone["recent_events"][-1] if zone["recent_events"] else None)
        category = evidence_class(zone)
        output = {"zone_id": zone["id"], "revision": zone["revision"], "parent_id": zone["parent_id"],
                  "kind": zone["kind"], "low": str(zone["low"]), "high": str(zone["high"]),
                  "center": str(median(zone["prices"])), "timeframe": state["timeframe"],
                  "created_at": iso(zone["created_at"]), "confirmed_at": iso(zone["confirmed_at"]),
                  "zone_state": "testing" if inside else "active", "evidence_class": category,
                  "distance_atr": distance, "formation_atr": zone["formation_atr"],
                  "price_evidence": {"status": "available", "pivot_count": zone["pivot_count"],
                                     "independent_touch_count": zone["independent_touch_count"],
                                     "parent_overlaps": related, "source": zone["source"]},
                  "formation_volume": zone.get("formation_volume"), "latest_episode": recent,
                  "volume_evidence": recent["volume_evidence"] if recent else {
                      "status": "unavailable", "reason": "no_observed_independent_episode"},
                  "executed_trade_evidence": {"status": "unavailable", "reason": "trade_archive_not_collected"},
                  "book_evidence": {"status": "unavailable", "reason": "continuous_depth_not_collected"},
                  "counter_evidence": ["recent_cross_or_deeper_penetration"] if category == "weakening" else [],
                  "eligibility_for_strategy_context": False,
                  "observation_stage": "post_touch_as_of" if zone["episode"] else "pre_touch",
                  "quality_flags": ["shadow_not_validated", "main_timeframe_candle_flow_proxy", "not_a_probability"]}
        (background if distance > 3 and not inside else levels).append(output)
    priority = {"volume_supported": 0, "structure_candidate": 1, "volume_unavailable": 2, "weakening": 3}
    levels.sort(key=lambda x: (x["zone_state"] != "testing",
                              priority[x["evidence_class"]] if rank_volume else 0, x["distance_atr"]))
    selected = []
    for kind in ("support", "resistance"):
        for level in (x for x in levels if x["kind"] == kind):
            if sum(x["kind"] == kind for x in selected) >= 3:
                break
            if any(x["kind"] == kind and float(x["low"]) <= float(level["high"])
                   and float(x["high"]) >= float(level["low"]) for x in selected):
                continue
            selected.append(level)
    return {"status": "ready", "algorithm_version": VERSION, "config_hash": CONFIG_HASH,
            "data_hash": state["data_hash"], "market_id": state["market"], "timeframe": state["timeframe"],
            "as_of": as_of, "source_time": iso(state["coarse_last"]), "reference_price": str(reference),
            "levels": selected, "background_levels": sorted(background, key=lambda x: x["distance_atr"])[:6],
            "coverage": {"structure_bars": state["coarse_count"], "warmup_bars": 250,
                         "volume_events_from": iso(state["volume_events_from"]) if state["volume_events_from"] else None,
                         "volume_latest": iso(state["coarse_last"])},
            "state_counts": {name: sum(z["state"] == name for z in state["zones"])
                             for name in ("active", "broken", "expired")},
            "quality_flags": ["shadow_not_validated", "historical_episodes_reconstructed",
                              "main_timeframe_candle_touch_order_unknown", "price_volume_only"]}
