"""Prepare the selected timeframe and its three higher frames for the model."""

import json
import re
from copy import deepcopy
from datetime import datetime
from decimal import Decimal

from .analysis import calculate
from .higher_timeframes import (
    PARAMETERS,
    RECENT_LIMITS,
    build_higher_timeframe_context,
    calculate_context_indicators,
    calculate_context_metrics,
)
from .indicator_preferences import (
    INITIAL_INDICATOR_DEFAULT_PARAMETERS,
    normalize_initial_indicators,
)
from .indicators import execute_tool, tool_schema, validate_tool_parameters
from .optional_indicators import FUNCTIONS as OPTIONAL_FUNCTIONS
from .optional_indicators import VERSION as OPTIONAL_VERSION
from .optional_indicators import required_candles
from .strategy_engine import compare_timeframes, other_timeframe
from .timeframes import (
    advance_candle,
    analysis_timeframes,
    duration_seconds,
    higher_timeframes,
    interval_hours,
)

VERSION = "technical_snapshot_v3"
PRICE_ACTION_VERSION = "closed_price_action_v2"
RECENT_CANDLE_LIMITS = RECENT_LIMITS
PRICE_ACTION_WINDOWS = {"1h": (("24h", 24), ("72h", 72), ("7d", 168), ("30d", 720)),
                        "4h": (("24h", 6), ("7d", 42), ("10d", 60), ("30d", 180)),
                        "12h": (("7d", 14), ("30d", 60), ("90d", 180)),
                        "1d": (("7d", 7), ("30d", 30), ("90d", 90), ("180d", 180)),
                        "3d": (("30d", 10), ("90d", 30), ("180d", 60)),
                        "1w": (("4w", 4), ("13w", 13), ("26w", 26), ("52w", 52)),
                        "1M": (("3M", 3), ("6M", 6), ("12M", 12), ("24M", 24))}
CANDLE_FIELDS = ("open_time", "close_time", "open", "high", "low", "close", "volume")
DEFAULT_INDICATORS = PARAMETERS
ADDITIONAL_INDICATORS = ("rsi", "bollinger", "volatility_atr", "rolling_vwap", "swing_points",
                         "fibonacci", "adx_dmi", "obv", "donchian", "keltner", "stochastic")


def _coverage(rows: list[dict], timeframe: str, requested: int) -> dict:
    seconds = duration_seconds(rows)
    requested_start = datetime.fromisoformat(rows[-1]['open_time'])
    requested_end = advance_candle(requested_start, timeframe)
    requested_duration = requested_end - advance_candle(requested_end, timeframe, -requested)
    return {"status": "complete" if len(rows) == requested else "partial",
            "requested_candles": requested, "candle_count": len(rows),
            "duration_hours": seconds // 3600,
            "duration_days": str(Decimal(seconds) / 86400),
            "requested_days": str(Decimal(round(requested_duration.total_seconds())) / 86400),
            "interval": timeframe, "interval_hours": interval_hours(timeframe),
            "calendar_months": 1 if timeframe == '1M' else None,
            "start_at": rows[0]["open_time"], "end_at": rows[-1]["close_time"]}


def _price_action_summary(rows: list[dict], timeframe: str, window: str, requested: int) -> dict:
    selected = rows[-requested:]
    opening, closing = Decimal(selected[0]["open"]), Decimal(selected[-1]["close"])
    change = ((closing / opening - 1) * 100).quantize(Decimal("0.01"))
    return {"window": window, **_coverage(selected, timeframe, requested),
            "open": selected[0]["open"], "high": str(max(Decimal(row["high"]) for row in selected)),
            "low": str(min(Decimal(row["low"]) for row in selected)), "last_close": selected[-1]["close"],
            "change_pct": format(change, "f"), "change_magnitude_pct": format(abs(change), "f"),
            "change_direction": "up" if closing > opening else "down" if closing < opening else "flat",
            "volume": str(sum((Decimal(row["volume"]) for row in selected), Decimal(0)))}


def build_technical_snapshot(request: dict, candles: list[dict], quote: dict,
                             context_candles: list[dict] | None) -> dict:
    cutoff = datetime.fromisoformat(quote["observed_at"])
    primary = request["timeframe"]
    frames = {timeframe: quote.get('higher_timeframe_candles', {}).get(timeframe, {}).get('candles')
              for timeframe in higher_timeframes(primary)}
    frames[primary] = candles
    if context_candles is not None:
        frames[other_timeframe(primary)] = context_candles
    higher_context = build_higher_timeframe_context(quote, primary)
    results = {}
    for timeframe in analysis_timeframes(primary):
        rows = frames[timeframe]
        if not rows:
            results[timeframe] = {"status": "unavailable", "reason": "closed_candles_not_provided"}
            continue
        higher_frame = higher_context["timeframes"].get(timeframe, {})
        if (timeframe != primary and quote.get('higher_timeframe_candles', {}).get(timeframe, {}).get('candles')
                and higher_frame.get('status') == 'unavailable'):
            results[timeframe] = deepcopy(higher_frame)
            continue
        metrics = (calculate(rows, timeframe) if timeframe == primary or len(rows) >= 60 else
                   calculate_context_metrics(rows, timeframe))
        if cutoff.tzinfo is None or any(
                datetime.fromisoformat(row["close_time"]).tzinfo is None or
                datetime.fromisoformat(row["close_time"]) > cutoff for row in rows):
            raise ValueError("Technical snapshot candle closes after the analysis cutoff")
        indicators = calculate_context_indicators(rows, quote)
        recent = rows[-RECENT_CANDLE_LIMITS[timeframe]:]
        requested_count = quote.get('higher_timeframe_candles', {}).get(timeframe, {}).get(
            'requested_candles', 1000 if timeframe in {primary, other_timeframe(primary)} else 180)
        results[timeframe] = {
            "status": "available", "last_closed_at": metrics["last_candle_at"],
            "history_quality": quote.get('higher_timeframe_candles', {}).get(timeframe, {}).get(
                'history_quality', getattr(rows, 'history_quality', None)),
            "candle_count": len(rows), "metrics": metrics, "indicators": indicators,
            "requested_candles": requested_count,
            "coverage_status": 'complete' if len(rows) >= requested_count else 'partial',
            "recent_closed_candles": [
                {key: row[key] for key in CANDLE_FIELDS} for row in recent],
            "recent_candle_coverage": _coverage(recent, timeframe, RECENT_CANDLE_LIMITS[timeframe]),
            "price_action_summary": [
                _price_action_summary(rows, timeframe, window, count)
                for window, count in (*PRICE_ACTION_WINDOWS[timeframe], ("available_history", len(rows)))],
        }
    selected = normalize_initial_indicators(request.get("initial_indicators"))
    frozen_parameters = request.get("initial_indicator_parameters")
    selected_parameters = {
        name: deepcopy(frozen_parameters[name] if isinstance(frozen_parameters, dict) and name in frozen_parameters
                       else INITIAL_INDICATOR_DEFAULT_PARAMETERS[name]) for name in selected}
    snapshot = {"version": VERSION, "market_id": request["market_id"],
            "market_snapshot_sha256": quote.get("snapshot_hash"),
            "as_of": quote["observed_at"], "primary_timeframe": primary,
            "analysis_timeframes": list(analysis_timeframes(primary)),
            "context_timeframes": list(higher_timeframes(primary)),
            "data_basis": "closed_candles", "price_action_version": PRICE_ACTION_VERSION,
            "optional_indicator_catalog": {
                "version": OPTIONAL_VERSION, "max_calls": 4,
                "tools": list(ADDITIONAL_INDICATORS),
                "not_precomputed": [name for name in ADDITIONAL_INDICATORS
                                    if name not in DEFAULT_INDICATORS and name not in selected],
                "existing_defaults_reused": True, "data_basis": "frozen_closed_candles_only"},
            "timeframes": results, "higher_timeframe_context": higher_context}
    snapshot["initial_indicator_selection"] = {
        "names": selected,
        "parameters": selected_parameters,
        "catalog_version": request.get("initial_indicator_catalog_version", OPTIONAL_VERSION),
        "scope": "primary_and_three_higher_timeframes",
        "execution": "precomputed_before_first_model_request",
        "source": "frozen_analysis_request",
    }
    for timeframe, frame in results.items():
        if frame["status"] != "available":
            continue
        for name in selected:
            execution = execute_additional_indicator(
                name, {"reason": "User selected this indicator for the initial strategy evidence.",
                       "timeframe": timeframe, **selected_parameters[name]},
                request, candles, quote, context_candles, snapshot)
            frame["indicators"][name] = {
                **execution["result"], "parameters": deepcopy(selected_parameters[name]),
                "execution_source": "precomputed_selected",
            }
    return snapshot


def prepare_analysis_evidence(request: dict, candles: list[dict], quote: dict,
                              context_candles: list[dict] | None = None,
                              positions: list[dict] | None = None) -> list[dict]:
    snapshot = build_technical_snapshot(request, candles, quote, context_candles)
    trace = [{"tool": "technical_snapshot", "reason": "預先計算主週期與向上三個週期的常用指標，供 Agent 一次綜合判斷",
              "parameters": {"timeframes": list(analysis_timeframes(request["timeframe"])),
                             "defaults": DEFAULT_INDICATORS,
                             "initial_indicators": snapshot["initial_indicator_selection"]["parameters"]},
              "execution_source": "precomputed", "result": snapshot}]
    primary = snapshot["timeframes"][request["timeframe"]]["metrics"]
    secondary_frame = snapshot["timeframes"][other_timeframe(request["timeframe"])]
    secondary = secondary_frame.get("metrics") if secondary_frame.get("candle_count", 0) >= 60 else None
    higher_metrics = {frame: data.get("metrics") for frame, data in snapshot["timeframes"].items()
                      if frame != request["timeframe"] and data.get("status") == "available"}
    prepared = {"metrics": primary,
                "timeframe_context": compare_timeframes(primary, secondary, request["timeframe"], higher_metrics)}
    tools = [("funding_context", "取得資金費與標記價背景"),
             ("support_resistance", "取得主週期已確認 v3 支撐壓力")]
    if secondary is not None:
        tools.append(("compare_timeframes", "以相同快照比較主週期與向上三個週期已收盤方向"))
    tools.append(("strategy_candidates", "預先提供數值檢查情境，供 Agent 自主取捨"))
    if positions:
        tools.append(("evaluate_positions", "預先計算所選持倉參考資料"))
    for name, reason in tools:
        execution = execute_tool(name, {"reason": reason}, candles, quote, request,
                                 context_candles, positions, prepared=prepared)
        execution["execution_source"] = "precomputed"
        trace.append(execution)
        if name == "support_resistance":
            prepared["levels"] = execution["result"]
    return trace


def compact_indicator_values(value):
    """Bound decimal-string precision for model input; stored evidence stays exact."""
    if isinstance(value, dict):
        return {key: compact_indicator_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [compact_indicator_values(item) for item in value]
    if isinstance(value, str) and re.fullmatch(r"-?\d+\.\d+", value):
        number = Decimal(value)
        if not number.is_finite():
            raise ValueError("Invalid technical indicator value")
        if len(number.as_tuple().digits) > 12:
            rounded = number.quantize(Decimal(1).scaleb(number.adjusted() - 11))
            return format(rounded, "f")
    return value


def compact_technical_snapshot(snapshot: dict) -> dict:
    """Send more exact OHLCV in a table, without repeated per-candle field names."""
    compact = compact_indicator_values({key: value for key, value in snapshot.items()
                                         if key not in {"timeframes", "higher_timeframe_context"}})
    frames = {}
    for timeframe, frame in snapshot["timeframes"].items():
        data = compact_indicator_values({key: value for key, value in frame.items()
                                         if key not in {"recent_closed_candles", "price_action_summary"}})
        if frame["status"] == "available":
            # The raw evidence retains both timestamps. The compact table keeps
            # every close timestamp and exact price/volume; no bars are aggregated.
            columns = [key for key in CANDLE_FIELDS if key != "open_time"]
            data["recent_closed_candles"] = {
                "format": "ohlcv_table_v1", "columns": columns, "order": "oldest_first",
                "first_open_time": frame["recent_closed_candles"][0]["open_time"],
                "interval": timeframe, "interval_hours": interval_hours(timeframe),
                "calendar_months": 1 if timeframe == "1M" else None,
                "rows": [[row[key] for key in columns] for row in frame["recent_closed_candles"]]}
            data["price_action_summary"] = deepcopy(frame["price_action_summary"])
        frames[timeframe] = data
    higher = snapshot.get("higher_timeframe_context")
    if higher:
        compact_higher = compact_indicator_values({k: v for k, v in higher.items() if k != "timeframes"})
        compact_higher["timeframes"] = {}
        for timeframe, frame in higher["timeframes"].items():
            shared = timeframe in frames and frames[timeframe]['status'] == 'available'
            excluded = {'recent_closed_candles', 'metrics', 'indicators'} if shared else {'recent_closed_candles'}
            data = compact_indicator_values({k: v for k, v in frame.items() if k not in excluded})
            if shared:
                data['technical_frame_ref'] = f'timeframes.{timeframe}'
            elif frame["status"] == "available":
                columns = [key for key in CANDLE_FIELDS if key != "open_time"]
                data["recent_closed_candles"] = {
                    "format": "ohlcv_table_v1", "columns": columns, "order": "oldest_first",
                    "first_open_time": frame["recent_closed_candles"][0]["open_time"],
                    "interval": timeframe, "interval_hours": interval_hours(timeframe),
                    "calendar_months": 1 if timeframe == "1M" else None,
                    "rows": [[row[key] for key in columns] for row in frame["recent_closed_candles"]]}
            compact_higher["timeframes"][timeframe] = data
        compact["higher_timeframe_context"] = compact_higher
    return {**compact, "timeframes": frames}


def additional_tool_schemas(snapshot: dict) -> list[dict]:
    available = [name for name, frame in snapshot["timeframes"].items()
                 if frame["status"] == "available"]
    schemas = []
    for name in ADDITIONAL_INDICATORS:
        schema = tool_schema(name)
        initial = snapshot.get("initial_indicator_selection", {}).get("parameters", {})
        schema["description"] += (
            " User-selected parameters are already in each available timeframe's initial evidence; reuse them unless a different parameter is needed."
            if name in initial else
            " Default parameters are already precomputed; request only a needed parameter change." if name in DEFAULT_INDICATORS else
            " Not precomputed. Request only to check an unresolved hypothesis, not because it matches a preference.")
        schema["description"] += " Uses only this frozen snapshot's closed candles. No network refresh or v3-zone modification."
        schema["parameters"]["properties"]["timeframe"] = {"type": "string", "enum": available}
        schema["parameters"]["required"].append("timeframe")
        schemas.append(schema)
    return schemas


def execute_additional_indicator(name: str, args: dict, request: dict, candles: list[dict],
                                  quote: dict, context_candles: list[dict] | None,
                                  snapshot: dict, *, cache: dict | None = None) -> dict:
    if name not in ADDITIONAL_INDICATORS:
        raise ValueError("Unapproved tool")
    if not isinstance(args, dict) or set(args) != {
            *tool_schema(name)["parameters"]["properties"], "timeframe"}:
        raise ValueError("Invalid tool arguments")
    timeframe = args["timeframe"]
    if (not isinstance(timeframe, str) or timeframe not in snapshot["timeframes"] or
            snapshot["timeframes"][timeframe]["status"] != "available"):
        raise ValueError("Tool timeframe differs from snapshot")
    parameters = {key: value for key, value in args.items() if key not in {"reason", "timeframe"}}
    # Always validate even if the corresponding calculation is already cached.
    validate_tool_parameters(name, args)
    if not isinstance(args["reason"], str) or not args["reason"].strip():
        raise ValueError("Invalid tool arguments")
    frame = snapshot["timeframes"][timeframe]
    rows = (candles if timeframe == request["timeframe"] else context_candles
            if timeframe == other_timeframe(request["timeframe"]) and context_candles is not None else
            quote.get("higher_timeframe_candles", {}).get(timeframe, {}).get("candles"))
    if not rows:
        raise ValueError("Tool timeframe is unavailable in snapshot")
    cutoff = datetime.fromisoformat(snapshot["as_of"])
    if (snapshot["as_of"] != quote["observed_at"] or cutoff.tzinfo is None or
            snapshot["market_id"] != request["market_id"] or
            snapshot.get("market_snapshot_sha256") != quote.get("snapshot_hash")):
        raise ValueError("Optional indicator snapshot identity differs")
    previous = None
    for row in rows:
        opened, closed = (datetime.fromisoformat(row[key]) for key in ("open_time", "close_time"))
        if (opened.tzinfo is None or closed.tzinfo is None or opened >= closed or closed > cutoff or
                previous is not None and opened <= previous):
            raise ValueError("Optional indicator contains future or unordered closed candles")
        previous = closed
    if len(rows) != frame["candle_count"] or rows[-1]["close_time"] != frame["last_closed_at"]:
        raise ValueError("Optional indicator candle series differs from frozen snapshot")
    needed = (required_candles(name, parameters) if name in OPTIONAL_FUNCTIONS else
              parameters.get('period', 0) + 1 if name in {'rsi', 'volatility_atr'} else
              parameters.get('period', parameters.get('width', 0) * 2 + 1))
    key = json.dumps([OPTIONAL_VERSION, snapshot.get("market_snapshot_sha256"), snapshot["as_of"],
                      timeframe, name, parameters], sort_keys=True, separators=(",", ":"))
    if cache is not None and key in cache:
        execution = {"tool": name, "reason": args["reason"][:180], "parameters": parameters,
                     "result": deepcopy(cache[key]), "execution_source": "agent_requested_cached"}
    elif (name in frame["indicators"] and (
            name in DEFAULT_INDICATORS and parameters == DEFAULT_INDICATORS[name] or
            parameters == frame["indicators"][name].get("parameters"))):
        execution = {"tool": name, "reason": args["reason"][:180], "parameters": parameters,
                     "result": deepcopy(frame["indicators"][name]),
                     "execution_source": "agent_requested_cached"}
    else:
        if len(rows) < needed:
            execution = {"tool": name, "reason": args["reason"][:180], "parameters": parameters,
                         "result": {"status": "unavailable", "reason": "insufficient_closed_candles"}}
        elif name == "rolling_vwap" and not any(
                Decimal(row["volume"]) > 0 for row in rows[-parameters["period"]:]):
            execution = {"tool": name, "reason": args["reason"][:180], "parameters": parameters,
                         "result": {"status": "unavailable", "reason": "zero_volume", **parameters}}
        else:
            execution = execute_tool(name, {key: value for key, value in args.items() if key != "timeframe"},
                                     rows, quote, {**request, "timeframe": timeframe})
        execution["execution_source"] = "agent_requested"
    result = execution["result"]
    # Preserve nulls, unavailability, and partial history; never synthesize bars.
    result.setdefault("status", "unavailable" if result.get("quality_flags") and name == "stochastic" and
                      (result.get("k") is None or result.get("d") is None) else
                      "partial" if frame.get("coverage_status") == "partial" or result.get("lookback_coverage") == "partial" else
                      "available")
    result.update({"timeframe": timeframe, "data_basis": "closed_candles", "calculation_version": OPTIONAL_VERSION,
                   "as_of": rows[-1]["close_time"], "analysis_as_of": snapshot["as_of"],
                   "candle_count": len(rows), "required_candles": needed,
                   "warmup_status": "sufficient" if len(rows) >= needed else "insufficient",
                   "coverage_status": frame.get("coverage_status", "unknown"),
                   "history_start_at": rows[0]["open_time"], "history_quality": frame.get("history_quality"),
                   "requested_candles": frame.get("requested_candles")})
    if cache is not None:
        cache[key] = deepcopy(result)
    execution["parameters"]["timeframe"] = timeframe
    return execution
