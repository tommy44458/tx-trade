"""As-of price context from the current, explicitly unconfirmed candle."""
from datetime import datetime, timedelta
from decimal import Decimal

from .timeframes import ANALYSIS_TIMEFRAMES, FIXED_SECONDS


def current_candle_context(candles: list[dict], quote: dict, timeframe: str) -> dict:
    price = Decimal(str(quote["price"]))
    previous = Decimal(candles[-1]["close"])
    result = {
        "status": "quote_only", "confirmation": "unclosed",
        "timeframe": timeframe, "quote_time": quote["observed_at"],
        "quote_price": str(price), "last_closed_close": str(previous),
        "price_vs_last_close": "above" if price > previous else "below" if price < previous else "equal",
        "change_from_last_close_pct": str(((price / previous - 1) * 100).quantize(Decimal("0.01"))),
        "candle": None, "price_vs_open": None, "change_from_open_pct": None,
        "quote_inside_observed_range": None,
    }
    candle = quote.get("forming_candle")
    if not isinstance(candle, dict):
        return result
    try:
        observed = datetime.fromisoformat(quote["observed_at"])
        fetched = datetime.fromisoformat(candle["fetched_at"])
        opened = datetime.fromisoformat(candle["open_time"])
        closes = datetime.fromisoformat(candle["close_time"])
        previous_close = datetime.fromisoformat(candles[-1]["close_time"])
        opened_price = Decimal(candle["open"])
        high = Decimal(candle["high"])
        low = Decimal(candle["low"])
        last = Decimal(candle["close"])
        volume = Decimal(candle["volume"])
        interval = timedelta(seconds=FIXED_SECONDS[timeframe]) if timeframe in ANALYSIS_TIMEFRAMES else None
        valid = (
            all(item.tzinfo is not None for item in (observed, fetched, opened, closes, previous_close))
            and 0 <= (opened - previous_close).total_seconds() <= 2
            and interval is not None
            and abs(closes - opened - interval) <= timedelta(milliseconds=1)
            and opened <= fetched <= observed < closes
            and 0 < low <= min(opened_price, last) <= max(opened_price, last) <= high
            and high.is_finite() and low.is_finite() and volume.is_finite() and volume >= 0
        )
    except (KeyError, ValueError, ArithmeticError, TypeError):
        valid = False
    if not valid:
        return result
    return result | {
        "status": "available", "candle": candle,
        "price_vs_open": "above" if price > opened_price else "below" if price < opened_price else "equal",
        "change_from_open_pct": str(((price / opened_price - 1) * 100).quantize(Decimal("0.01"))),
        "quote_inside_observed_range": low <= price <= high,
    }
