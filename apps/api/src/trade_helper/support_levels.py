"""Historical swing zones and separately labelled, short-lived order-book evidence."""

from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from statistics import median


def wilder_atr(candles: list[dict], period: int = 14) -> Decimal:
    if len(candles) < period + 1:
        raise ValueError("ATR warmup insufficient")
    highs = [Decimal(c["high"]) for c in candles]
    lows = [Decimal(c["low"]) for c in candles]
    closes = [Decimal(c["close"]) for c in candles]
    ranges = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                  abs(lows[i] - closes[i - 1])) for i in range(1, len(candles))]
    atr = sum(ranges[:period]) / period
    for value in ranges[period:]:
        atr = (atr * (period - 1) + value) / period
    return atr


def historical_levels(candles: list[dict], timeframe: str, reference_price: Decimal) -> list[dict]:
    """Confirmed pivot clusters. Evidence score is a ranking heuristic, not a probability."""
    if len(candles) < 60:
        raise ValueError("At least 60 closed candles are required")
    atr = wilder_atr(candles)
    highs = [Decimal(c["high"]) for c in candles]
    lows = [Decimal(c["low"]) for c in candles]
    volumes = [Decimal(c["volume"]) for c in candles]
    pivots: dict[str, list[dict]] = {"support": [], "resistance": []}
    for index in range(2, len(candles) - 2):
        recent_volumes = volumes[max(0, index - 20):index]
        baseline = median(recent_volumes) if recent_volumes else Decimal(0)
        volume_ratio = volumes[index] / baseline if baseline else Decimal(1)
        if lows[index] < min(lows[index - 2:index]) and lows[index] <= min(lows[index + 1:index + 3]):
            pivots["support"].append({"price": lows[index], "index": index,
                                       "volume_ratio": volume_ratio})
        if highs[index] > max(highs[index - 2:index]) and highs[index] >= max(highs[index + 1:index + 3]):
            pivots["resistance"].append({"price": highs[index], "index": index,
                                          "volume_ratio": volume_ratio})

    tolerance = atr * Decimal("0.35")
    padding = atr * Decimal("0.15")
    results = []
    for kind, points in pivots.items():
        clusters: list[list[dict]] = []
        for point in sorted(points, key=lambda item: item["price"]):
            if clusters and point["price"] - clusters[-1][0]["price"] <= tolerance:
                clusters[-1].append(point)
            else:
                clusters.append([point])
        for cluster in clusters:
            prices = [point["price"] for point in cluster]
            low, high = min(prices) - padding, max(prices) + padding
            # A two-close break resets prior touches, including breaks between two pivots.
            last_break = -1
            for index in range(cluster[0]["index"] + 2, len(candles) - 1):
                a, b = Decimal(candles[index]["close"]), Decimal(candles[index + 1]["close"])
                breached = (a < low and b < low) if kind == "support" else (a > high and b > high)
                if breached:
                    last_break = index + 1
            cluster = [point for point in cluster if point["index"] > last_break]
            if not cluster:
                continue
            prices = [point["price"] for point in cluster]
            low, high = min(prices) - padding, max(prices) + padding
            if kind == "support" and high >= reference_price:
                continue
            if kind == "resistance" and low <= reference_price:
                continue
            latest = max(point["index"] for point in cluster)
            touches = len(cluster)
            volume_ratio = sum(min(point["volume_ratio"], Decimal(3)) for point in cluster) / touches
            age = len(candles) - latest - 1
            score = min(touches, 4) * 2 + min(volume_ratio, Decimal(3)) + Decimal(1) / (1 + Decimal(age) / 30)
            results.append({"kind": kind, "low": str(low), "high": str(high),
                            "center": str(median(prices)),
                            "confirmed_at": candles[latest + 2]["close_time"],
                            "timeframe": timeframe, "method": "confirmed_pivot_cluster_v2",
                            "touch_count": touches, "relative_pivot_volume": str(volume_ratio.quantize(Decimal("0.01"))),
                            "age_bars": age, "evidence_score": str(score.quantize(Decimal("0.01"))),
                            "evidence_label": "heuristic_not_probability"})
    selected = []
    for kind in ("support", "resistance"):
        choices = [item for item in results if item["kind"] == kind]
        choices.sort(key=lambda item: (
            -(Decimal(item["evidence_score"]) /
              (1 + abs(Decimal(item["center"]) - reference_price) / max(atr, Decimal("0.00000001")))),
            abs(Decimal(item["center"]) - reference_price),
        ))
        selected.extend(choices[:3])
    return selected


def order_book_evidence(book: dict | None, reference_price: Decimal) -> dict:
    """One REST depth snapshot; never interpreted as long/short positions or durable walls."""
    if not book:
        return {"status": "unavailable", "zones": []}
    if reference_price <= 0:
        return {"status": "invalid", "zones": []}
    try:
        bids = [(Decimal(price), Decimal(qty)) for price, qty in book["bids"]]
        asks = [(Decimal(price), Decimal(qty)) for price, qty in book["asks"]]
        event_ms = int(book["T"])
        age_seconds = (datetime.now(UTC).timestamp() * 1000 - event_ms) / 1000
        if not bids or not asks or max(price for price, _ in bids) >= min(price for price, _ in asks):
            raise ValueError("crossed or empty order book")
        mid = (max(price for price, _ in bids) + min(price for price, _ in asks)) / 2
        if abs(mid - reference_price) / reference_price > Decimal("0.005"):
            return {"status": "misaligned", "zones": []}
        if age_seconds < -5 or age_seconds > 15:
            return {"status": "stale", "zones": [], "age_seconds": round(age_seconds, 2)}
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return {"status": "invalid", "zones": []}
    # Constant percentage bins make BTC and ETH comparable without inferring volume at price from OHLCV.
    width = reference_price * Decimal("0.0001")
    zones = []
    coverage = {}
    for side, rows in (("bid", bids), ("ask", asks)):
        bins: dict[int, Decimal] = {}
        for price, qty in rows:
            if price <= 0 or qty <= 0:
                continue
            bucket = int((price / width).to_integral_value(rounding=ROUND_FLOOR))
            bins[bucket] = bins.get(bucket, Decimal(0)) + price * qty
        if not bins:
            continue
        values = list(bins.values())
        baseline = median(values)
        total = sum(values)
        coverage[side] = {"low": str(min(price for price, _ in rows)),
                          "high": str(max(price for price, _ in rows)),
                          "bins": len(bins)}
        if len(bins) < 5 or baseline <= 0:
            continue
        for bucket, notional in bins.items():
            concentration = notional / baseline
            share = notional / total
            if concentration < 2 or share < Decimal("0.04"):
                continue
            zones.append({"side": side, "low": str(width * bucket),
                          "high": str(width * (bucket + 1)),
                          "notional_usdt": str(notional.quantize(Decimal("0.01"))),
                          "relative_to_median_bin": str(concentration.quantize(Decimal("0.01"))),
                          "side_share_pct": str((share * 100).quantize(Decimal("0.01")))})
    zones.sort(key=lambda zone: Decimal(zone["notional_usdt"]), reverse=True)
    return {"status": "snapshot", "zones": zones[:6],
            "source": "Binance USD-M depth; visible resting bids/asks, not position direction",
            "observed_at": datetime.fromtimestamp(event_ms / 1000, UTC).isoformat(),
            "age_seconds": round(age_seconds, 2), "coverage": coverage,
            "note": "Single near-price snapshot; orders can be cancelled. No persistence or predictive claim."}


def annotate_liquidity(levels: list[dict], evidence: dict) -> list[dict]:
    zones = evidence.get("zones", []) if evidence.get("status") == "snapshot" else []
    result = []
    for level in levels:
        side = "bid" if level["kind"] == "support" else "ask"
        matches = [zone for zone in zones if zone["side"] == side and
                   Decimal(zone["low"]) <= Decimal(level["high"]) and
                   Decimal(zone["high"]) >= Decimal(level["low"])]
        result.append({**level, "order_book_snapshot_overlap": bool(matches)})
    return result
