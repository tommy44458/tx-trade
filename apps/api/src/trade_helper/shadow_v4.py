"""Separate durable shadow queue: long downloads cannot delay the active v3 report."""

import fcntl
import json
import sys
import time
from datetime import UTC, datetime, timedelta

from .config import assert_local_mode, data_dir
from .db import connect, init_db, utc_now
from .market import fetch_quote, fetch_tick_size
from .market_store import (
    SECONDS,
    cached_candles,
    digest,
    floor_time,
    history_start,
    init_market_store,
    stamp,
)
from .support_levels_v4 import CONFIG_HASH, VERSION, advance, new_state, snapshot


def build_v4_shadow(market: str, timeframe: str, progress=None) -> dict:
    init_market_store()
    parent_tf = "4h" if timeframe == "1h" else "1d"
    tick = float(fetch_tick_size(market))
    states = {}
    now = datetime.now(UTC)
    for tf in (timeframe, parent_tf):
        with connect(readonly=True) as db:
            row = db.execute("SELECT payload FROM zone_states_v4 WHERE market=? AND timeframe=? AND config_hash=?",
                             (market, tf, CONFIG_HASH)).fetchone()
        state = json.loads(row["payload"]) if row else None
        if state and state["tick"] != tick:
            raise ValueError("Tick rule changed; explicit state rebuild required")
        states[tf] = state or new_state(market, tf, tick)
    coarse = {}
    for tf, state in states.items():
        start = (datetime.fromtimestamp((state["coarse_last"] + 1) / 1000, UTC)
                 if state["coarse_last"] is not None else history_start(now, tf))
        coarse[tf] = cached_candles(market, tf, start, floor_time(now, tf), progress)
    # Refresh all tails to the same cutoff after the potentially long initial backfill.
    cutoff = datetime.now(UTC)
    for tf in states:
        coarse[tf] += cached_candles(market, tf, floor_time(now, tf), floor_time(cutoff, tf), progress)
    quote = fetch_quote(market)
    as_of = quote["observed_at"]
    for tf, state in states.items():
        if progress:
            progress()
        states[tf] = advance(state, coarse[tf], as_of)
    if stamp(as_of) - states[timeframe]["coarse_last"] > SECONDS[timeframe] * 1000 + 180_000:
        raise ValueError("Main-timeframe candle data stale")
    result = snapshot(states[timeframe], float(quote["price"]), as_of, states[parent_tf])
    result["data_hash"] = digest([result["data_hash"], states[parent_tf]["data_hash"]])
    result["quote"] = quote
    result["observation_note"] = "Background snapshot captured after data collection; separate from original v2 report time"
    result["data_received_before"] = as_of
    with connect() as db:
        for tf, state in states.items():
            for event in state["completed_events"]:
                db.execute("INSERT INTO zone_events_v4 VALUES (?,?,?,?,?) ON CONFLICT (id) DO UPDATE SET payload=EXCLUDED.payload,updated_at=EXCLUDED.updated_at",
                           (event["id"], market, tf, json.dumps(event), utc_now()))
            state["completed_events"] = []
            db.execute("INSERT INTO zone_states_v4 VALUES (?,?,?,?,?) ON CONFLICT (market,timeframe,config_hash) DO UPDATE SET payload=EXCLUDED.payload,updated_at=EXCLUDED.updated_at",
                       (market, tf, CONFIG_HASH, json.dumps(state), utc_now()))
        db.commit()
    return result


def run_once() -> bool:
    with connect() as db:
        db.execute("UPDATE analyses SET v4_status='queued' WHERE v4_status='running' AND v4_lease_until<?", (utc_now(),))
        row = db.execute("SELECT id,request_json FROM analyses WHERE status='completed' AND v4_status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            db.commit()
            return False
        until = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        db.execute("UPDATE analyses SET v4_status='running',v4_lease_until=? WHERE id=?", (until, row["id"]))
        db.commit()

    def progress():
        with connect() as db:
            db.execute("UPDATE analyses SET v4_lease_until=? WHERE id=? AND v4_status='running'",
                       ((datetime.now(UTC) + timedelta(minutes=10)).isoformat(), row["id"]))
            db.commit()

    request = json.loads(row["request_json"])
    try:
        result = build_v4_shadow(request["market_id"], request["timeframe"], progress)
        status = "completed"
    except Exception as exc:  # noqa: BLE001 - persist observable failure without altering v2
        result = {"status": "unavailable", "algorithm_version": VERSION,
                  "error_code": type(exc).__name__, "reason": str(exc)[:250], "as_of": utc_now()}
        status = "failed"
    with connect() as db:
        db.execute("UPDATE analyses SET v4_status=?,v4_json=?,v4_lease_until=NULL WHERE id=? AND v4_status='running'",
                   (status, json.dumps(result), row["id"]))
        db.commit()
    return True


def main() -> None:
    assert_local_mode()
    init_db()
    init_market_store()
    with (data_dir() / "v4_worker.lock").open("w") as lock:
        # A previous app instance may still be shutting down; wait for its lock
        # instead of failing, which would stop the whole local backend.
        fcntl.flock(lock, fcntl.LOCK_EX)
        while True:
            worked = run_once()
            if "--once" in sys.argv:
                return
            if not worked:
                time.sleep(1)


if __name__ == "__main__":
    main()
