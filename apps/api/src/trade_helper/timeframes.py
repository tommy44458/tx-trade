"""Analysis hierarchy and UTC Binance candle boundaries.

Monthly intervals follow the calendar, weekly candles start on Monday, and
Binance's three-day candles are anchored one day after the Unix epoch.
"""

from datetime import UTC, datetime, timedelta

ANALYSIS_TIMEFRAMES = ("1h", "4h", "12h", "1d")
TIMEFRAME_LADDER = (*ANALYSIS_TIMEFRAMES, "3d", "1w", "1M")
FIXED_SECONDS = {"1m": 60, "5m": 300, "1h": 3600, "4h": 14400,
                 "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800}


def higher_timeframes(primary: str) -> tuple[str, str, str]:
    if primary not in ANALYSIS_TIMEFRAMES:
        raise ValueError("Unsupported analysis timeframe")
    index = TIMEFRAME_LADDER.index(primary)
    return TIMEFRAME_LADDER[index + 1:index + 4]


def analysis_timeframes(primary: str) -> tuple[str, str, str, str]:
    return (primary, *higher_timeframes(primary))


def candle_open(at: datetime, timeframe: str) -> datetime:
    if at.tzinfo is None:
        raise ValueError("Candle time requires a timezone")
    at = at.astimezone(UTC)
    if timeframe == "1M":
        return at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if timeframe == "1w":
        return (at - timedelta(days=at.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = FIXED_SECONDS[timeframe]
    anchor = 86400 if timeframe == "3d" else 0
    epoch = int(at.timestamp())
    return datetime.fromtimestamp((epoch - anchor) // seconds * seconds + anchor, UTC)


def advance_candle(opened: datetime, timeframe: str, count: int = 1) -> datetime:
    if timeframe != "1M":
        return opened + timedelta(seconds=FIXED_SECONDS[timeframe] * count)
    month = opened.year * 12 + opened.month - 1 + count
    return opened.replace(year=month // 12, month=month % 12 + 1)


def candle_close(opened: datetime, timeframe: str) -> datetime:
    return advance_candle(opened, timeframe) - timedelta(milliseconds=1)


def interval_hours(timeframe: str) -> int | None:
    return None if timeframe == "1M" else FIXED_SECONDS[timeframe] // 3600


def duration_seconds(rows: list[dict]) -> int:
    start = datetime.fromisoformat(rows[0]["open_time"])
    end = datetime.fromisoformat(rows[-1]["close_time"]) + timedelta(milliseconds=1)
    return round((end - start).total_seconds())


def closed_history_is_stale(rows: list[dict], timeframe: str, cutoff: datetime) -> bool:
    """A series is stale once the following candle should also have closed."""
    return cutoff >= advance_candle(datetime.fromisoformat(rows[-1]["open_time"]), timeframe, 2)
