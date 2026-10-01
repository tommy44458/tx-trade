"""Immutable, checksummed candle cache shared by replay and the local shadow worker."""

import json
import math
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from .db import connect, utc_now
from .market import fetch_candles_range
from .timeframes import FIXED_SECONDS, advance_candle, candle_close, candle_open

SECONDS = FIXED_SECONDS


def stamp(value: str) -> int:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timezone required")
    return round(parsed.timestamp() * 1000)


def iso(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, UTC).isoformat()


def digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_candle(candle: dict, timeframe: str) -> None:
    opened, closed = stamp(candle["open_time"]), stamp(candle["close_time"])
    opened_at = datetime.fromtimestamp(opened / 1000, UTC)
    if candle_open(opened_at, timeframe) != opened_at or closed != stamp(candle_close(opened_at, timeframe).isoformat()):
        raise ValueError("Unaligned candle")
    op, hi, lo, close, volume = (float(candle[k]) for k in ("open", "high", "low", "close", "volume"))
    if not all(math.isfinite(v) for v in (op, hi, lo, close, volume)):
        raise ValueError("Nonfinite candle")
    if lo <= 0 or hi < max(op, close, lo) or lo > min(op, close) or volume < 0:
        raise ValueError("Invalid OHLCV")
    q, b = candle.get("quote_volume"), candle.get("taker_buy_quote_volume")
    if q is not None or b is not None:
        if q is None or b is None:
            raise ValueError("Incomplete quote volume fields")
        q, b = float(q), float(b)
        if not math.isfinite(q) or not math.isfinite(b) or not 0 <= b <= q:
            raise ValueError("Invalid directional volume")


def init_market_store() -> None:
    from .db import init_db

    init_db()


def cached_candles(market: str, timeframe: str, start: datetime, end: datetime,
                   progress=None) -> list[dict]:
    """Only request missing pages; reject corrections instead of silently changing old evidence."""
    first, stop = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    if first >= stop or candle_open(start, timeframe) != start or candle_open(end, timeframe) != end:
        raise ValueError("Historical bounds must be ordered and candle-aligned")
    opens = []
    current = start
    while current < end:
        opens.append(int(current.timestamp() * 1000))
        current = advance_candle(current, timeframe)
    with connect(readonly=True) as db:
        rows = db.execute("SELECT open_ms,payload,sha256 FROM market_candles_v4 WHERE market=? AND timeframe=? AND open_ms>=? AND open_ms<? ORDER BY open_ms",
                          (market, timeframe, first, stop)).fetchall()
    existing = {}
    for row in rows:
        payload = json.loads(row["payload"])
        if digest(payload) != row["sha256"]:
            raise ValueError("Candle cache checksum mismatch")
        existing[row["open_ms"]] = payload
    index = 0
    while index < len(opens):
        cursor = opens[index]
        if cursor in existing:
            index += 1
            continue
        next_index = index + 1
        while next_index < min(len(opens), index + 1000) and opens[next_index] not in existing:
            next_index += 1
        page_end = opens[next_index] if next_index < len(opens) else stop
        if progress:
            progress()
        batch = fetch_candles_range(market, timeframe,
                                    datetime.fromtimestamp(cursor / 1000, UTC),
                                    datetime.fromtimestamp(page_end / 1000, UTC))
        if len(batch) != next_index - index:
            raise ValueError("Incomplete historical page")
        received = utc_now()
        with connect() as db:
            for row_index, candle in enumerate(batch):
                validate_candle(candle, timeframe)
                opened = stamp(candle["open_time"])
                if opened != opens[index + row_index]:
                    raise ValueError("Historical page gap")
                db.execute("INSERT INTO market_candles_v4 VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING",
                           (market, timeframe, opened, json.dumps(candle), digest(candle), received))
                existing[opened] = candle
            db.commit()
        index = next_index
    return [existing[t] for t in opens]


def floor_time(at: datetime, timeframe: str) -> datetime:
    return candle_open(at, timeframe)


def history_start(at: datetime, timeframe: str) -> datetime:
    days = {"1h": 90, "4h": 365, "12h": 730, "1d": 730}[timeframe]
    return floor_time(at, timeframe) - timedelta(days=days, seconds=250 * SECONDS[timeframe])
