"""Replay the production Agent without forcing optional tool choices.

Preparation is offline by default. --live uses the already selected Codex model
and consumes account usage. It reads only non-secret preferences from the user's
SQLite database; all test writes go to a disposable database.
"""

import argparse
import json
import math
import os
import sqlite3
import sys
import tempfile
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from time import monotonic
from unittest.mock import patch

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT / "src"))

from trade_helper.timeframes import candle_close, candle_open

CASE_IDS = ("aligned_trend", "same_market_left_high", "compression", "weak_participation", "btc_0930")
CAPABILITY_PROBES = {
    "probe_envelopes": (
        "This is an explicit tool-capability test, not an autonomous-selection sample. "
        "For the 1h frozen closed series, use the exposed Python tools to obtain Bollinger(20,2) bandwidth and percent_b, "
        "Keltner(20,14,2) bands, Donchian(55) previous-channel comparison, and Stochastic(14,3,3) K/D. "
        "These values are not precomputed. Include the actual values and their limits in the final report; do not estimate or invent them. "
        "Do not treat a band/channel touch or oscillator threshold as proof of reversal."
    ),
    "probe_structure": (
        "This is an explicit tool-capability test, not an autonomous-selection sample. "
        "For the 1h frozen closed series, use the exposed Python tools to obtain ADX/DMI(14,14), OBV(100), "
        "and Fibonacci(lookback=160,width=3,direction=auto). These values are not precomputed. "
        "Explain ADX strength versus DI direction, OBV's price change versus signed volume fraction, and Fibonacci's actual confirmed anchors. "
        "Include actual returned values and their limits in the final report; do not estimate or invent them. "
        "If a required anchor or history is unavailable, disclose that instead of selecting another parameter to manufacture a value."
    ),
    "probe_fibonacci": (
        "This is an explicit tool-capability test, not an autonomous-selection sample. "
        "Use the exposed Python Fibonacci tool on the frozen 1h series with lookback=160,width=3,direction=auto. "
        "Report its actual confirmed anchor prices/times, 0.382 and 0.618 retracements, and whether a valid C anchor permits trend extensions. "
        "These values are not precomputed. Do not estimate them, manufacture an anchor, or label projections as active v3 support/resistance."
    ),
}
CUTOFF = datetime(2026, 9, 30, 13, 16, tzinfo=UTC)


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def request():
    return {"kind": "market", "market_id": "binance:perp:SOLUSDT", "timeframe": "1h",
            "directional_bias": None, "risk_tolerance": "medium", "trading_style": "right",
            "leverage": 10, "position_ids": [], "account_equity_usdt": None, "output_locale": "zh-TW"}


def synthetic_case(name):
    """Aggregate every higher frame from the same synthetic hourly sequence."""
    start = candle_open(CUTOFF, "1d") - timedelta(days=220)
    end = candle_open(CUTOFF, "1h")
    count = int((end - start).total_seconds() // 3600)
    hourly = []
    previous = Decimal(100)
    compression_start = count - 360
    weak_start = count - 120
    for index in range(count):
        close = Decimal(str(100 + index * .006 + .32 * math.sin(index * math.tau / 32)))
        volume = Decimal(1000)
        spread = Decimal("0.14")
        if name == "compression" and index >= compression_start:
            elapsed = index - compression_start
            amplitude = 2.4 * math.exp(-elapsed / 90) + .08
            close = Decimal(str(100 + compression_start * .006 + amplitude * math.sin(elapsed * math.tau / 16)))
            spread = Decimal(str(.04 + amplitude * .12))
        elif name == "weak_participation" and index >= weak_start:
            up = (index - weak_start) % 3 != 2
            close = previous + (Decimal(".035") if up else Decimal("-.050"))
            volume = Decimal(600) if up else Decimal(4000)
        close = close.quantize(Decimal(".01"))
        opened = start + timedelta(hours=index)
        hourly.append({"open_time": opened.isoformat(), "close_time": candle_close(opened, "1h").isoformat(),
                       "open": str(previous), "high": str(max(previous, close) + spread),
                       "low": str(min(previous, close) - spread), "close": str(close), "volume": str(volume)})
        previous = close
    frames = {"1h": hourly[-1000:]}
    for timeframe, limit in (("4h", 1000), ("12h", 180), ("1d", 180)):
        buckets = defaultdict(list)
        for row in hourly:
            opened = candle_open(datetime.fromisoformat(row["open_time"]), timeframe)
            if candle_close(opened, timeframe) < CUTOFF:
                buckets[opened].append(row)
        hours = {"4h": 4, "12h": 12, "1d": 24}[timeframe]
        aggregated = []
        for opened, rows in sorted(buckets.items()):
            if len(rows) != hours:
                continue
            aggregated.append({"open_time": opened.isoformat(), "close_time": candle_close(opened, timeframe).isoformat(),
                               "open": rows[0]["open"], "high": str(max(Decimal(r["high"]) for r in rows)),
                               "low": str(min(Decimal(r["low"]) for r in rows)), "close": rows[-1]["close"],
                               "volume": str(sum(Decimal(r["volume"]) for r in rows))})
        frames[timeframe] = aggregated[-limit:]
    live_price = previous + Decimal(".01")
    opened = candle_open(CUTOFF, "1h")
    quote = {"price": str(live_price), "tick_size": "0.01", "observed_at": CUTOFF.isoformat(),
             "forming_candle": {"open_time": opened.isoformat(), "close_time": candle_close(opened, "1h").isoformat(),
                                "fetched_at": CUTOFF.isoformat(), "open": str(previous), "high": str(live_price + Decimal(".08")),
                                "low": str(previous - Decimal(".08")), "close": str(live_price), "volume": "200"},
             "higher_timeframe_candles": {tf: {"candles": rows, "requested_candles": len(rows)}
                                           for tf, rows in frames.items() if tf != "1h"}}
    return {"origin": "synthetic_consistent_1h_aggregation", "request": request(),
            "candles": frames["1h"], "context_candles": frames["4h"], "quote": quote}


def cases():
    aligned = synthetic_case("aligned_trend")
    preference_case = deepcopy(aligned)
    preference_case["request"].update(directional_bias="bearish", risk_tolerance="high", trading_style="left")
    fixture = json.loads((API_ROOT / "tests/fixtures/btc_20260930_2116.json").read_text())
    observation = fixture["observations"][0]
    btc = {"origin": "frozen_public_btc_20260930_2116_fixture", "request": observation["request"] | {"output_locale": "zh-TW"},
           "candles": fixture["candles"], "context_candles": fixture["context_candles"], "quote": observation["quote"]}
    return {"aligned_trend": aligned, "same_market_left_high": preference_case,
            "compression": synthetic_case("compression"), "weak_participation": synthetic_case("weak_participation"),
            "btc_0930": btc, **{name: deepcopy(btc if name == "probe_fibonacci" else aligned) for name in CAPABILITY_PROBES}}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def dispatch_observer(original, name, info, output, began):
    def observe(tool, arguments, *positional, **keywords):
        call = {"tool": tool, "arguments": deepcopy(arguments), "started_seconds": round(monotonic() - began, 3)}
        info["actual_dispatches"].append(call)
        try:
            execution = original(tool, arguments, *positional, **keywords)
        except Exception as exc:
            call.update(success=False, error_type=type(exc).__name__)
            write_json(output / f"{name}-dispatches.json", info["actual_dispatches"])
            raise
        call.update(success=True, result_available=execution["result"].get("status") in {"available", "partial"},
                    execution_source=execution["execution_source"], result=deepcopy(execution["result"]))
        write_json(output / f"{name}-dispatches.json", info["actual_dispatches"])
        print(json.dumps({"dispatch": name, "tool": tool, "timeframe": arguments.get("timeframe"), "status": execution["result"].get("status")}), flush=True)
        return execution
    return observe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Run real Codex analyses; consumes account usage")
    parser.add_argument("--case", choices=(*CASE_IDS, *CAPABILITY_PROBES), action="append")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--settings-db", type=Path,
                        default=Path.home() / "Library/Application Support/AI Trade Helper/data/trade_helper.sqlite3")
    args = parser.parse_args()
    output = args.output_dir or Path(tempfile.mkdtemp(prefix="ath-indicator-selection-"))
    output.mkdir(parents=True, exist_ok=True)
    output.chmod(0o700)
    suite = cases()
    results = []
    # Never allow imports of provider/settings modules to initialize the user DB.
    with tempfile.TemporaryDirectory(prefix="ath-indicator-db-") as private:
        os.environ.update(APP_DESKTOP="1", APP_DB_PATH=str(Path(private) / "test.sqlite3"), APP_DATA_DIR=private)
        from trade_helper import agent, codex_bridge
        from trade_helper.config import local_user_id
        from trade_helper.local_settings import save_preferences
        from trade_helper.prompts import resolve_prompt
        from trade_helper.technical_snapshot import (
            DEFAULT_INDICATORS,
            additional_tool_schemas,
            prepare_analysis_evidence,
        )

        if args.live:
            with sqlite3.connect(f"file:{args.settings_db.expanduser()}?mode=ro", uri=True) as db:
                row = db.execute("SELECT value_json FROM app_preferences WHERE user_id=?", (local_user_id(),)).fetchone()
                prefs = json.loads(row[0]) if row else {}
            if prefs.get("model_provider") != "codex" or prefs.get("codex_auth_scope") == "application":
                raise SystemExit("Verification requires the existing selected Codex CLI authorization; no provider is changed.")
            save_preferences({key: prefs[key] for key in ("model_provider", "models", "codex_auth_scope", "codex_disconnected") if key in prefs})
            Path(os.environ["APP_DB_PATH"]).chmod(0o600)
        try:
            for name in (args.case or CASE_IDS):
                fixture = deepcopy(suite[name])
                quote = fixture["quote"]
                before = digest({key: fixture[key] for key in ("candles", "context_candles", "quote")})
                quote["snapshot_hash"] = before
                frozen_hash = digest(fixture)
                req = fixture["request"]
                trace = prepare_analysis_evidence(req, fixture["candles"], quote, fixture["context_candles"], [])
                snapshot = trace[0]["result"]
                bundle = resolve_prompt("strategy_market", response_locale=req["output_locale"], inputs=req)
                info = {"case": name, "origin": fixture["origin"], "market_data_sha256": before,
                        "execution_started_at": datetime.now(UTC).isoformat(),
                        "selection_mode": "explicit_capability_probe" if name in CAPABILITY_PROBES else "autonomous_production_analysis",
                        "preferences": {key: req.get(key) for key in ("directional_bias", "risk_tolerance", "trading_style")},
                        "analysis_as_of": quote["observed_at"], "available_frames": [tf for tf, frame in snapshot["timeframes"].items() if frame["status"] == "available"],
                        "unavailable_frames": [tf for tf, frame in snapshot["timeframes"].items() if frame["status"] != "available"],
                        "precomputed_names": list(DEFAULT_INDICATORS), "optional_schema_count": len(additional_tool_schemas(snapshot)),
                        "prompt_version": bundle.prompt_version, "prompt_sha256": bundle.instructions_sha256,
                        "live": args.live, "actual_dispatches": []}
                write_json(output / f"{name}-input.json", fixture)
                write_json(output / f"{name}-prepared.json", trace)
                print(json.dumps({"started": name, "live": args.live, "available_frames": info["available_frames"]}), flush=True)
                if args.live:
                    began = monotonic()
                    observe = dispatch_observer(agent.execute_additional_indicator, name, info, output, began)
                    original_context = agent.agent_context
                    def context_with_probe(*positional, _original=original_context, _name=name, **keywords):
                        context = _original(*positional, **keywords)
                        if _name in CAPABILITY_PROBES:
                            context["explicit_verification_request"] = CAPABILITY_PROBES[_name]
                        return context
                    try:
                        with patch.object(agent, "execute_additional_indicator", observe), patch.object(agent, "agent_context", context_with_probe):
                            decision = agent.analyze_with_tools(req, fixture["candles"], quote, fixture["context_candles"], [], prepared_trace=trace, prompt_bundle=bundle)
                        write_json(output / f"{name}-decision.json", decision)
                        execution = decision["analysis_execution"]
                        info.update({key: execution.get(key) for key in ("provider", "model", "additional_tool_calls", "model_requests", "input_tokens", "output_tokens")})
                        reasoning = decision["reasoning"]
                        info.update(success=True, seconds=round(monotonic() - began, 2), agent_stance=decision["agent_stance"],
                                    entry_decision=reasoning.get("entry_decision"), reasoning=reasoning,
                                    agent_requested_trace_count=sum(run.get("execution_source", "").startswith("agent_requested") for run in decision["tool_trace"]))
                        assert info["additional_tool_calls"] == len(info["actual_dispatches"]), "Dispatch accounting differs"
                        assert info["agent_requested_trace_count"] == sum(call["success"] for call in info["actual_dispatches"]), "Actual executions and returned trace differ"
                    except Exception as exc:  # noqa: BLE001 -- record a failed case without exposing provider payloads
                        info.update(success=False, seconds=round(monotonic() - began, 2), error_type=type(exc).__name__)
                info["frozen_input_unchanged"] = digest(fixture) == frozen_hash
                assert info["frozen_input_unchanged"], "Verification mutated frozen input"
                results.append(info)
                write_json(output / "summary.json", results)
                print(json.dumps({key: info[key] for key in ("case", "live", "success", "seconds", "additional_tool_calls", "agent_stance", "frozen_input_unchanged", "error_type") if key in info}), flush=True)
        finally:
            if args.live:
                codex_bridge.shutdown()
    print(json.dumps({"output_directory": str(output), "cases": len(results), "live": args.live}), flush=True)
    return 1 if any(result.get("success") is False for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
