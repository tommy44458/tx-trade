"""On-demand closed-candle calculations; never trading decisions or v3 zones.

All arithmetic uses Decimal. Each algorithm documents its seed, range basis,
and degenerate-case behavior so another platform's settings can be compared.
"""

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import pairwise

VERSION = "optional_indicators_v1"
ZERO = Decimal(0)
HUNDRED = Decimal(100)


def _integer(parameters: dict, name: str, default: int, *, maximum: int = 500) -> int:
    value = parameters.get(name, default)
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"Invalid {name}")
    return value


def _multiplier(parameters: dict) -> Decimal:
    value = parameters.get("multiplier", 2)
    if isinstance(value, bool):
        raise TypeError("Invalid multiplier")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid multiplier") from exc
    if not number.is_finite() or not ZERO < number <= 10:
        raise ValueError("Invalid multiplier")
    return number


def _rows(candles: list[dict], needed: int) -> list[dict[str, Decimal]]:
    if len(candles) < needed:
        raise ValueError(f"Indicator needs {needed} closed candles")
    rows = []
    for candle in candles:
        try:
            row = {key: Decimal(str(candle[key])) for key in ("open", "high", "low", "close", "volume")}
        except (InvalidOperation, KeyError, TypeError) as exc:
            raise ValueError("Invalid indicator OHLCV") from exc
        if (not all(number.is_finite() for number in row.values()) or row["low"] <= 0
                or row["volume"] < 0 or row["high"] < max(row["open"], row["close"], row["low"])
                or row["low"] > min(row["open"], row["close"])):
            raise ValueError("Invalid indicator OHLCV")
        rows.append(row)
    return rows


def required_candles(name: str, parameters: dict) -> int:
    period = _integer(parameters, "period", 14 if name in {"adx_dmi", "stochastic"} else 20)
    if name == "fibonacci":
        return 2 * _integer(parameters, "width", 3, maximum=10) + 2
    if name == "adx_dmi":
        return period + _integer(parameters, "adx_period", 14)
    if name == "stochastic":
        return period + _integer(parameters, "smooth_k", 3) + _integer(parameters, "smooth_d", 3) - 2
    if name == "keltner":
        return max(period, _integer(parameters, "atr_period", 14) + 1)
    return period + 1 if name in {"obv", "donchian"} else period


def bollinger(candles: list[dict], _quote: dict, parameters: dict) -> dict:
    period = _integer(parameters, "period", 20)
    multiplier = _multiplier(parameters)
    rows = _rows(candles, period)[-period:]
    values = [row["close"] for row in rows]
    middle = sum(values) / period
    variance = sum((value - middle) ** 2 for value in values) / period
    deviation = variance.sqrt()
    lower, upper = middle - multiplier * deviation, middle + multiplier * deviation
    return {"period": period, "multiplier": str(multiplier), "lower": str(lower),
            "middle": str(middle), "upper": str(upper), "standard_deviation": str(deviation),
            "bandwidth_pct": str((upper - lower) / middle * HUNDRED),
            "percent_b": str((values[-1] - lower) / (upper - lower)) if upper != lower else None,
            "method": "SMA_close_population_standard_deviation",
            "quality_flags": ["flat_band_percent_b_undefined"] if upper == lower else []}


def adx_dmi(candles: list[dict], _quote: dict, parameters: dict) -> dict:
    period = _integer(parameters, "period", 14)
    smoothing = _integer(parameters, "adx_period", 14)
    rows = _rows(candles, period + smoothing)
    movements = []
    for previous, current in pairwise(rows):
        up, down = current["high"] - previous["high"], previous["low"] - current["low"]
        movements.append((max(current["high"] - current["low"],
                              abs(current["high"] - previous["close"]),
                              abs(current["low"] - previous["close"])),
                          up if up > down and up > 0 else ZERO,
                          down if down > up and down > 0 else ZERO))
    # Seed all three RMAs with the arithmetic mean of the first n transitions.
    state = [sum(move[index] for move in movements[:period]) / period for index in range(3)]
    directions, dxs = [], []
    for index in range(period - 1, len(movements)):
        if index >= period:
            state = [(old * (period - 1) + new) / period
                     for old, new in zip(state, movements[index])]
        tr, plus, minus = state
        plus_di, minus_di = (HUNDRED * plus / tr, HUNDRED * minus / tr) if tr else (ZERO, ZERO)
        denominator = plus_di + minus_di
        dx = HUNDRED * abs(plus_di - minus_di) / denominator if denominator else ZERO
        directions.append((plus_di, minus_di))
        dxs.append(dx)
    adx = sum(dxs[:smoothing]) / smoothing
    previous_adx = None
    for dx in dxs[smoothing:]:
        previous_adx, adx = adx, (adx * (smoothing - 1) + dx) / smoothing
    plus_di, minus_di = directions[-1]
    return {"period": period, "adx_period": smoothing, "adx": str(adx),
            "plus_di": str(plus_di), "minus_di": str(minus_di), "dx": str(dxs[-1]),
            "previous_adx": str(previous_adx) if previous_adx is not None else None,
            "adx_change": str(adx - previous_adx) if previous_adx is not None else None,
            "directional_balance": "plus" if plus_di > minus_di else "minus" if minus_di > plus_di else "equal",
            "method": "Wilder_RMA_SMA_seed_first_n_transitions",
            "quality_flags": ["zero_directional_movement"] if plus_di == minus_di == 0 else [],
            "limitations": "ADX measures strength, not direction; thresholds are contextual, not an entry rule."}


def obv(candles: list[dict], _quote: dict, parameters: dict) -> dict:
    period = _integer(parameters, "period", 20)
    rows = _rows(candles, period + 1)
    cumulative, signed = [ZERO], []
    for previous, current in pairwise(rows):
        flow = current["volume"] if current["close"] > previous["close"] else (
            -current["volume"] if current["close"] < previous["close"] else ZERO)
        signed.append(flow)
        cumulative.append(cumulative[-1] + flow)
    window_flow = sum(signed[-period:])
    total_volume = sum(row["volume"] for row in rows[-period:])
    price_change = (rows[-1]["close"] / rows[-period - 1]["close"] - 1) * HUNDRED
    return {"period": period, "obv": str(cumulative[-1]), "obv_change": str(window_flow),
            "window_volume": str(total_volume),
            "signed_volume_fraction": str(window_flow / total_volume) if total_volume else None,
            "price_change_pct": str(price_change), "initial_obv": "0",
            "anchor_at": candles[0]["close_time"], "window_start_at": candles[-period]["open_time"],
            "method": "close_direction_signed_base_asset_volume",
            "quality_flags": ["zero_volume"] if total_volume == 0 else [],
            "limitations": "OBV signs whole-candle volume by close direction; it is not order flow, taker imbalance, or proof of divergence."}


def donchian(candles: list[dict], _quote: dict, parameters: dict) -> dict:
    period = _integer(parameters, "period", 20)
    rows = _rows(candles, period + 1)
    window, prior = rows[-period:], rows[-period - 1:-1]
    lower, upper = min(row["low"] for row in window), max(row["high"] for row in window)
    prior_lower, prior_upper = min(row["low"] for row in prior), max(row["high"] for row in prior)
    close = rows[-1]["close"]
    relation = "above" if close > prior_upper else "below" if close < prior_lower else "inside_or_boundary"
    return {"period": period, "lower": str(lower), "upper": str(upper), "middle": str((lower + upper) / 2),
            "range_width": str(upper - lower),
            "close_position_pct": str((close - lower) / (upper - lower) * HUNDRED) if upper != lower else None,
            "previous_channel": {"lower": str(prior_lower), "upper": str(prior_upper),
                                 "end_at": candles[-2]["close_time"]},
            "last_close_vs_previous_channel": relation, "method": "rolling_high_low_includes_last_closed_bar",
            "quality_flags": ["flat_range_position_undefined"] if upper == lower else [],
            "limitations": "The prior channel excludes the last closed bar to avoid a self-referential breakout; channel bounds are not v3 zones."}


def _ema(values: list[Decimal], period: int) -> Decimal:
    current, alpha = sum(values[:period]) / period, Decimal(2) / (period + 1)
    for value in values[period:]:
        current = alpha * value + (1 - alpha) * current
    return current


def keltner(candles: list[dict], _quote: dict, parameters: dict) -> dict:
    period = _integer(parameters, "period", 20)
    atr_period = _integer(parameters, "atr_period", 14)
    multiplier = _multiplier(parameters)
    rows = _rows(candles, max(period, atr_period + 1))
    ranges = [max(current["high"] - current["low"], abs(current["high"] - previous["close"]),
                  abs(current["low"] - previous["close"])) for previous, current in pairwise(rows)]
    atr = sum(ranges[:atr_period]) / atr_period
    for value in ranges[atr_period:]:
        atr = (atr * (atr_period - 1) + value) / atr_period
    middle = _ema([row["close"] for row in rows], period)
    lower, upper = middle - atr * multiplier, middle + atr * multiplier
    return {"period": period, "atr_period": atr_period, "multiplier": str(multiplier),
            "lower": str(lower), "middle": str(middle), "upper": str(upper), "atr": str(atr),
            "bandwidth_pct": str((upper - lower) / middle * HUNDRED),
            "close_position": str((rows[-1]["close"] - lower) / (upper - lower)) if upper != lower else None,
            "method": "EMA_close_SMA_seed_plus_minus_Wilder_ATR",
            "quality_flags": ["flat_channel_position_undefined"] if upper == lower else [],
            "limitations": "This is the EMA/Wilder-ATR variant; Keltner implementations differ. Touching a band is not a reversal rule."}


def stochastic(candles: list[dict], _quote: dict, parameters: dict) -> dict:
    period = _integer(parameters, "period", 14)
    smooth_k, smooth_d = _integer(parameters, "smooth_k", 3), _integer(parameters, "smooth_d", 3)
    rows = _rows(candles, period + smooth_k + smooth_d - 2)
    raw = []
    for index in range(period - 1, len(rows)):
        window = rows[index - period + 1:index + 1]
        lower, upper = min(row["low"] for row in window), max(row["high"] for row in window)
        raw.append(HUNDRED * (rows[index]["close"] - lower) / (upper - lower) if upper != lower else None)
    ks = [sum(window) / smooth_k if all(value is not None for value in window) else None
          for index in range(smooth_k - 1, len(raw))
          for window in [raw[index - smooth_k + 1:index + 1]]]
    ds = [sum(window) / smooth_d if all(value is not None for value in window) else None
          for index in range(smooth_d - 1, len(ks))
          for window in [ks[index - smooth_d + 1:index + 1]]]
    return {"period": period, "smooth_k": smooth_k, "smooth_d": smooth_d,
            "raw_k": str(raw[-1]) if raw[-1] is not None else None,
            "k": str(ks[-1]) if ks[-1] is not None else None,
            "d": str(ds[-1]) if ds[-1] is not None else None,
            "method": "SMA_smoothed_close_position_in_rolling_high_low_range",
            "quality_flags": ["flat_range_in_smoothing_window"] if ks[-1] is None or ds[-1] is None else [],
            "limitations": "Overbought or oversold can persist in trends; values alone do not prove reversal or divergence."}


def _confirmed_pivots(candles: list[dict], rows: list[dict], width: int) -> list[dict]:
    points = []
    for index in range(width, len(rows) - width):
        neighbors = rows[index - width:index] + rows[index + 1:index + width + 1]
        kinds = [kind for kind, field in (("low", "low"), ("high", "high"))
                 if all(rows[index][field] < row[field] if kind == "low" else rows[index][field] > row[field]
                        for row in neighbors)]
        # An outside bar can be both extrema. OHLCV does not tell their order.
        if len(kinds) != 1:
            continue
        kind = kinds[0]
        point = {"kind": kind, "price": str(rows[index][kind]), "open_time": candles[index]["open_time"],
                 "confirmed_at": candles[index + width]["close_time"]}
        if points and points[-1]["kind"] == kind:
            better = (Decimal(point["price"]) < Decimal(points[-1]["price"]) if kind == "low" else
                      Decimal(point["price"]) > Decimal(points[-1]["price"]))
            if better:
                points[-1] = point
        else:
            points.append(point)
    return points


def fibonacci(candles: list[dict], quote: dict, parameters: dict) -> dict:
    lookback = _integer(parameters, "lookback", 80)
    width = _integer(parameters, "width", 3, maximum=10)
    direction = parameters.get("direction", "auto")
    if direction not in {"auto", "up", "down"}:
        raise ValueError("Invalid Fibonacci direction")
    rows = _rows(candles, 2 * width + 2)
    selected, values = candles[-lookback:], rows[-lookback:]
    if quote.get("observed_at"):
        cutoff = datetime.fromisoformat(quote["observed_at"])
        closes = [datetime.fromisoformat(row["close_time"]) for row in selected]
        if cutoff.tzinfo is None or any(closed.tzinfo is None or closed > cutoff for closed in closes):
            raise ValueError("Fibonacci candle closes after the analysis cutoff")
    try:
        tick = Decimal(str(quote.get("tick_size", "0")))
    except InvalidOperation as exc:
        raise ValueError("Fibonacci requires a positive exchange tick size") from exc
    if not tick.is_finite() or tick <= 0:
        raise ValueError("Fibonacci requires a positive exchange tick size")
    points = _confirmed_pivots(selected, values, width)
    pair, retracement = None, None
    # An ABC may be preferred only if its C is the newest confirmed pivot.
    # Otherwise use the newest eligible AB, not a stale completed ABC.
    if len(points) >= 3:
        a, b, c = points[-3:]
        ap, bp, cp = (Decimal(point["price"]) for point in (a, b, c))
        move = "up" if a["kind"] == "low" else "down"
        if min(ap, bp) < cp < max(ap, bp) and (direction == "auto" or direction == move):
            pair, retracement = (a, b), c
    if pair is None:
        for index in range(len(points) - 2, -1, -1):
            a, b = points[index:index + 2]
            move = "up" if a["kind"] == "low" else "down"
            if (Decimal(b["price"]) - Decimal(a["price"])) * (1 if move == "up" else -1) > 0 and (
                    direction == "auto" or direction == move):
                pair = (a, b)
                break
    if pair is None:
        return {"status": "unavailable", "reason": "no_confirmed_directional_pivot_pair",
                "lookback": lookback, "width": width, "confirmed_pivot_count": len(points)}
    a, b = pair
    ap, bp = Decimal(a["price"]), Decimal(b["price"])
    spread = bp - ap

    def level(price: Decimal) -> dict:
        rounded = (price / tick).quantize(Decimal(1), rounding=ROUND_HALF_UP) * tick
        return {"raw_price": str(price), "price": str(rounded), "status": "available"} if rounded > 0 else {
            "raw_price": str(price), "price": None, "status": "unavailable", "reason": "nonpositive_projection"}

    ratios = ("0.236", "0.382", "0.5", "0.618", "0.786")
    extension_ratios = ("1.272", "1.618", "2", "2.618")
    final_anchor = retracement or b
    anchor_index = next(index for index, candle in enumerate(selected) if candle["open_time"] == final_anchor["open_time"])
    newer = sum(point["open_time"] > final_anchor["open_time"] for point in points)
    return {"lookback": lookback, "used_candles": len(selected), "width": width,
            "lookback_coverage": "complete" if len(selected) == lookback else "partial",
            "direction": "up" if spread > 0 else "down", "anchors": {"a": a, "b": b, "c": retracement},
            "anchor_method": "newest_pivot_ABC_else_latest_matching_AB", "confirmed_pivot_count": len(points),
            "selection_rationale": "newest_confirmed_pivot_is_C_inside_AB" if retracement else "latest_confirmed_directional_pair",
            "latest_confirmed_pivot": points[-1], "final_anchor_bars_ago": len(selected) - 1 - anchor_index,
            "newer_confirmed_pivots_after_selected_pattern": newer,
            "last_anchor_confirmed_at": final_anchor["confirmed_at"],
            "direction_scope": "selected_historical_impulse_not_current_market_direction",
            "tick_size": str(tick), "rounding": "nearest_tick_half_up", "scale": "linear_price",
            "retracements": {ratio: level(bp - spread * Decimal(ratio)) for ratio in ratios},
            "swing_extensions": {ratio: level(ap + spread * Decimal(ratio)) for ratio in extension_ratios},
            "trend_extensions": {"status": "available", "levels": {
                ratio: level(Decimal(retracement["price"]) + spread * Decimal(ratio))
                for ratio in ("0.618", "1", "1.618", "2", "2.618")}} if retracement else {
                    "status": "unavailable", "reason": "no_confirmed_retracement_anchor"},
            "limitations": "Confirmed pivots lag by width bars and update only as more closed bars confirm. Ratios are contextual projections, not v3 zones or guaranteed reactions."}


FUNCTIONS = {"bollinger": bollinger, "fibonacci": fibonacci, "adx_dmi": adx_dmi,
             "obv": obv, "donchian": donchian, "keltner": keltner, "stochastic": stochastic}
