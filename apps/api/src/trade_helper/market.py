from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from .market_catalog import MARKETS, market_for  # noqa: F401 -- legacy official-news imports
from .timeframes import (
    ANALYSIS_TIMEFRAMES,
    TIMEFRAME_LADDER,
    advance_candle,
    candle_close,
    candle_open,
    higher_timeframes,
)

BASE_URL = "https://fapi.binance.com"
MAIN_HISTORY_LIMIT = 1000


class CandleHistory(list):
    """List-compatible closed bars with explicit provider quality metadata."""

    def __init__(self, candles: list[dict], history_quality: dict):
        super().__init__(candles)
        self.history_quality = history_quality


def _contiguous_three_day_suffix(candles: list[dict]) -> list[dict]:
    """Old Binance 3D history contains anchor changes and overlapping bars.

    Retain only the latest native, aligned, contiguous segment. The smaller
    count is explicitly reported as partial coverage by the snapshot builder.
    Never connect those historical discontinuities or manufacture candles.
    """
    start = len(candles)
    following = None
    for index in range(len(candles) - 1, -1, -1):
        opened = datetime.fromisoformat(candles[index]['open_time'])
        closed = datetime.fromisoformat(candles[index]['close_time'])
        if (candle_open(opened, '3d') != opened or candle_close(opened, '3d') != closed or
                (following is not None and advance_candle(opened, '3d') != following)):
            break
        start = index
        following = opened
    return candles[start:]


def normalize_candle(item: list) -> dict:
    candle = {
        "open_time": datetime.fromtimestamp(item[0] / 1000, UTC).isoformat(),
        "close_time": datetime.fromtimestamp(item[6] / 1000, UTC).isoformat(),
        **{key: str(Decimal(item[index])) for key, index in
           (("open", 1), ("high", 2), ("low", 3), ("close", 4), ("volume", 5))},
    }
    # Older local snapshots lack these fields. Missing data must not become zero volume.
    if len(item) >= 11:
        candle.update(quote_volume=str(Decimal(item[7])), trade_count=int(item[8]),
                      taker_buy_volume=str(Decimal(item[9])),
                      taker_buy_quote_volume=str(Decimal(item[10])))
    return candle


def symbol_for(market_id: str) -> str:
    return market_for(market_id)["binance_symbol"]


def fetch_candles(market_id: str, timeframe: str, limit: int = 300) -> list[dict]:
    if timeframe not in TIMEFRAME_LADDER:
        raise ValueError("Unsupported timeframe")
    if type(limit) is not int or not 1 <= limit <= MAIN_HISTORY_LIMIT:
        raise ValueError("Candle limit must be between 1 and 1000")
    symbol = symbol_for(market_id)
    response = httpx.get(
        f"{BASE_URL}/fapi/v1/klines",
        params={"symbol": symbol, "interval": timeframe, "limit": min(limit + 1, 1000)},
        timeout=12,
    )
    response.raise_for_status()
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    rows = response.json()
    candles = []
    for item in rows:
        if item[6] >= now_ms:  # The current candle is not confirmed.
            continue
        candles.append(normalize_candle(item))
    # The exchange's 1000-row cap includes the unfinished candle. Fill the one
    # missing older close so chart levels and Agent evidence share 1000 closes.
    # A newly listed market may legitimately return fewer rows; never fabricate.
    if limit == MAIN_HISTORY_LIMIT and len(rows) == MAIN_HISTORY_LIMIT and len(candles) < limit:
        older = httpx.get(
            f"{BASE_URL}/fapi/v1/klines",
            params={"symbol": symbol, "interval": timeframe,
                    "endTime": int(rows[0][0]) - 1, "limit": limit - len(candles)},
            timeout=12,
        )
        older.raise_for_status()
        candles = [normalize_candle(item) for item in older.json()
                   if item[6] < now_ms] + candles
    if timeframe == '3d':
        retained = _contiguous_three_day_suffix(candles)
        if len(retained) != len(candles):
            return CandleHistory(retained[-limit:], {
                "status": "truncated", "reason": "exchange_3d_historical_discontinuity",
                "provided_closed_candles": len(candles), "removed_candles": len(candles) - len(retained),
                "retained_closed_candles": len(retained[-limit:]),
                "earliest_retained_at": retained[0]['open_time'] if retained else None,
            })
        candles = retained
    return candles[-limit:]


def fetch_forming_candle(market_id: str, timeframe: str) -> dict | None:
    """Read the unfinished candle from the selected Binance klines feed."""
    if timeframe not in ANALYSIS_TIMEFRAMES:
        raise ValueError("Unsupported timeframe")
    response = httpx.get(
        f"{BASE_URL}/fapi/v1/klines",
        params={"symbol": symbol_for(market_id), "interval": timeframe, "limit": 2},
        timeout=12,
    )
    response.raise_for_status()
    fetched_at = datetime.now(UTC)
    rows = response.json()
    if not rows:
        return None
    latest = rows[-1]
    now_ms = int(fetched_at.timestamp() * 1000)
    if not (int(latest[0]) <= now_ms < int(latest[6])):
        return None
    return normalize_candle(latest) | {"fetched_at": fetched_at.isoformat()}


def fetch_higher_timeframe_candles(market_id: str, timeframe: str = "1h",
                                 context_candles: list[dict] | None = None) -> dict:
    """Collect the next three frames before the quote is frozen, without duplicating the next frame."""
    frames = higher_timeframes(timeframe)
    def fetch(timeframe):
        if timeframe == frames[0] and context_candles is not None:
            return {"candles": context_candles, "requested_candles": MAIN_HISTORY_LIMIT,
                    **({"history_quality": context_candles.history_quality}
                       if isinstance(context_candles, CandleHistory) else {})}
        try:
            rows = fetch_candles(market_id, timeframe, 180)
            return {"candles": rows, "requested_candles": 180,
                    **({"history_quality": rows.history_quality} if isinstance(rows, CandleHistory) else {})}
        except (httpx.HTTPError, ValueError):
            return {"candles": [], "reason": "fetch_failed"}
    with ThreadPoolExecutor(max_workers=3) as pool:
        return dict(zip(frames, pool.map(fetch, frames), strict=True))


def fetch_candles_range(market_id: str, timeframe: str, start: datetime,
                        end_exclusive: datetime) -> list[dict]:
    """Fetch a contiguous, closed historical window for deterministic replay."""
    if timeframe not in {"1m", "5m", *TIMEFRAME_LADDER}:
        raise ValueError("Unsupported historical timeframe")
    if start.tzinfo is None or end_exclusive.tzinfo is None:
        raise ValueError("Historical bounds must be timezone-aware")
    cursor = int(start.timestamp() * 1000)
    end_ms = int(end_exclusive.timestamp() * 1000)
    if cursor >= end_ms or candle_open(start, timeframe) != start or candle_open(end_exclusive, timeframe) != end_exclusive:
        raise ValueError("Historical bounds must be ordered and candle-aligned")
    if end_ms > int(datetime.now(UTC).timestamp() * 1000):
        raise ValueError("Historical end is in the future")
    candles = []
    symbol = symbol_for(market_id)
    with httpx.Client(timeout=20) as client:
        while cursor < end_ms:
            response = client.get(f"{BASE_URL}/fapi/v1/klines", params={
                "symbol": symbol, "interval": timeframe, "startTime": cursor,
                "endTime": end_ms - 1, "limit": 1000,
            })
            response.raise_for_status()
            rows = response.json()
            if not rows:
                raise ValueError(f"Historical candles missing from {cursor}")
            for row in rows:
                next_ms = int(advance_candle(datetime.fromtimestamp(cursor / 1000, UTC), timeframe).timestamp() * 1000)
                if cursor >= end_ms or int(row[0]) != cursor or int(row[6]) != next_ms - 1:
                    raise ValueError(f"Historical candle gap or duplicate at {cursor}")
                candles.append(normalize_candle(row))
                cursor = next_ms
    return candles


def fetch_quote(market_id: str) -> dict:
    market = market_for(market_id)
    response = httpx.get(
        f"{BASE_URL}/fapi/v1/ticker/price",
        params={"symbol": market["binance_symbol"]}, timeout=8,
    )
    response.raise_for_status()
    mark_response = httpx.get(
        f"{BASE_URL}/fapi/v1/premiumIndex",
        params={"symbol": market["binance_symbol"]}, timeout=8,
    )
    mark_response.raise_for_status()
    mark = mark_response.json()
    return {
        **{key: market[key] for key in ("base_asset", "quote_asset", "settlement_asset",
                                       "margin_asset", "binance_symbol")},
        "price": response.json()["price"],
        "mark_price": mark["markPrice"],
        "index_price": mark["indexPrice"],
        "last_funding_rate": mark.get("lastFundingRate"),
        "next_funding_time": datetime.fromtimestamp(mark["nextFundingTime"] / 1000, UTC).isoformat(),
        "observed_at": datetime.now(UTC).isoformat(),
    }


def fetch_order_book(market_id: str) -> dict:
    response = httpx.get(
        f"{BASE_URL}/fapi/v1/depth",
        params={"symbol": symbol_for(market_id), "limit": 1000}, timeout=10,
    )
    response.raise_for_status()
    return response.json()


def fetch_tick_size(market_id: str) -> Decimal:
    """Use the same cached exchange metadata as catalog and symbol validation."""
    return Decimal(market_for(market_id)["tick_size"])
