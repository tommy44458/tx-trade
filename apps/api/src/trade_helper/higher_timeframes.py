"""Closed higher-frame evidence, with honest warmup and calendar coverage."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .indicators import TOOL_FUNCTIONS
from .support_levels import wilder_atr
from .timeframes import (
    advance_candle,
    candle_close,
    candle_open,
    closed_history_is_stale,
    duration_seconds,
    higher_timeframes,
    interval_hours,
)

VERSION = 'higher_timeframe_context_v3'
RECENT_LIMITS = {'1h': 72, '4h': 60, '12h': 36, '1d': 30, '3d': 30, '1w': 26, '1M': 24}
PARAMETERS = {'trend_ema': {}, 'rsi': {'period': 14}, 'macd': {},
              'volatility_atr': {'period': 14},
              'volume_signal': {}, 'rolling_vwap': {'period': 20},
              'swing_points': {'width': 3}}
WARMUP = {'trend_ema': 50, 'rsi': 15, 'macd': 35,
          'volatility_atr': 15, 'volume_signal': 21, 'rolling_vwap': 20,
          'swing_points': 7}


def _validate(rows: list[dict], timeframe: str, cutoff: datetime) -> None:
    previous = None
    for row in rows:
        opened, closed = (datetime.fromisoformat(row[k]) for k in ('open_time', 'close_time'))
        if (opened.tzinfo is None or closed.tzinfo is None or closed > cutoff or
                candle_open(opened, timeframe) != opened or closed != candle_close(opened, timeframe)):
            raise ValueError('Higher timeframe has invalid or future candle times')
        if previous is not None and opened != advance_candle(previous, timeframe):
            raise ValueError('Higher timeframe has a candle gap or duplicate')
        previous = opened
        op, high, low, close, volume = (Decimal(row[k]) for k in ('open', 'high', 'low', 'close', 'volume'))
        if (not all(n.is_finite() for n in (op, high, low, close, volume)) or low <= 0 or
                volume < 0 or high < max(op, close, low) or low > min(op, close)):
            raise ValueError('Higher timeframe has invalid OHLCV')


def _structure(rows: list[dict], width: int = 3) -> dict:
    points = []
    for index in range(width, len(rows) - width):
        neighbors = rows[index-width:index] + rows[index+1:index+width+1]
        for kind, field, compare in (('high', 'high', lambda a, b: a > b),
                                     ('low', 'low', lambda a, b: a < b)):
            price = Decimal(rows[index][field])
            if all(compare(price, Decimal(row[field])) for row in neighbors):
                points.append({'kind': kind, 'price': str(price),
                               'open_time': rows[index]['open_time'],
                               'confirmed_at': rows[index+width]['close_time']})
    relations = {}
    for kind in ('high', 'low'):
        selected = [p for p in points if p['kind'] == kind][-2:]
        relations[kind] = {'points': selected, 'comparison': 'insufficient' if len(selected) < 2 else
                          'rising' if Decimal(selected[-1]['price']) > Decimal(selected[0]['price']) else
                          'falling' if Decimal(selected[-1]['price']) < Decimal(selected[0]['price']) else 'equal'}
    high, low = relations['high']['comparison'], relations['low']['comparison']
    state = high if high == low and high in {'rising', 'falling'} else 'mixed'
    if 'insufficient' in (high, low):
        state = 'insufficient'
    return {'width': width, 'state': state, **relations, 'recent_points': points[-12:]}


def calculate_context_metrics(rows: list[dict], timeframe: str) -> dict:
    closes = [Decimal(row['close']) for row in rows]
    ma20 = sum(closes[-20:])/20 if len(rows) >= 20 else None
    ma50 = sum(closes[-50:])/50 if len(rows) >= 50 else None
    trend = ('bullish' if closes[-1] > ma20 > ma50 else
             'bearish' if closes[-1] < ma20 < ma50 else 'mixed') if ma50 is not None else None
    return {'last_close': rows[-1]['close'], 'ma20': str(ma20) if ma20 is not None else None,
            'ma50': str(ma50) if ma50 is not None else None, 'trend': trend,
            'atr14': str(wilder_atr(rows)) if len(rows) >= 15 else None,
            'last_candle_at': rows[-1]['close_time'], 'candle_count': len(rows),
            'status': 'complete' if len(rows) >= 60 else 'partial'}


def calculate_context_indicators(rows: list[dict], quote: dict) -> dict:
    indicators = {}
    for name, parameters in PARAMETERS.items():
        needed = WARMUP[name]
        if len(rows) < needed:
            indicators[name] = {'status': 'unavailable', 'reason': 'insufficient_closed_candles',
                                'required_candles': needed, 'candle_count': len(rows)}
        elif name == 'rolling_vwap' and not any(Decimal(row['volume']) > 0 for row in rows[-20:]):
            indicators[name] = {'status': 'unavailable', 'reason': 'zero_volume', **parameters}
        else:
            indicators[name] = TOOL_FUNCTIONS[name](rows, quote, parameters)
    return indicators


def _range(rows: list[dict], timeframe: str, days: int, price: Decimal) -> dict:
    end = advance_candle(datetime.fromisoformat(rows[-1]['open_time']), timeframe)
    requested_start = end - timedelta(days=days)
    requested = 1
    while advance_candle(end, timeframe, -requested) > requested_start:
        requested += 1
    selected = rows[-requested:]
    high = max(Decimal(row['high']) for row in selected)
    low = min(Decimal(row['low']) for row in selected)
    change = ((Decimal(selected[-1]['close']) / Decimal(selected[0]['open']) - 1) * 100).quantize(Decimal('.01'))
    return {'requested_days': days, 'requested_candles': requested,
            'window_basis': 'whole_closed_candles', 'interval': timeframe,
            'requested_start_at': requested_start.isoformat(),
            'candle_count': len(selected), 'duration_days': str(Decimal(duration_seconds(selected))/86400),
            'status': 'complete' if len(selected) == requested else 'partial',
            'start_at': selected[0]['open_time'], 'end_at': selected[-1]['close_time'],
            'high': str(high), 'low': str(low), 'last_close': selected[-1]['close'],
            'change_pct': str(change), 'change_magnitude_pct': str(abs(change)),
            'volume': str(sum((Decimal(row['volume']) for row in selected), Decimal(0))),
            'quote_position_pct': str(((price-low)/(high-low)*100).quantize(Decimal('.01'))) if high != low else None}


def build_higher_timeframe_context(quote: dict, primary_timeframe: str = '1h') -> dict:
    cutoff = datetime.fromisoformat(quote['observed_at'])
    if cutoff.tzinfo is None:
        raise ValueError('Higher timeframe cutoff must be timezone-aware')
    source = quote.get('higher_timeframe_candles', {})
    frames = {}
    for timeframe in higher_timeframes(primary_timeframe):
        item = source.get(timeframe, {})
        rows = item.get('candles', [])
        if not rows:
            frames[timeframe] = {'status': 'unavailable', 'reason': item.get('reason', 'not_in_snapshot')}
            continue
        _validate(rows, timeframe, cutoff)
        if closed_history_is_stale(rows, timeframe, cutoff):
            frames[timeframe] = {'status': 'unavailable', 'reason': 'stale_closed_candles'}
            continue
        requested = item.get('requested_candles', 180)
        frames[timeframe] = {
            'status': 'available', 'source': 'binance_perpetual_closed_klines',
            'history_quality': item.get('history_quality', getattr(rows, 'history_quality', None)),
            'interval': timeframe, 'interval_hours': interval_hours(timeframe),
            'calendar_months': 1 if timeframe == '1M' else None,
            'requested_candles': requested, 'candle_count': len(rows),
            'coverage_status': 'complete' if len(rows) >= requested else 'partial',
            'duration_days': str(Decimal(duration_seconds(rows))/86400),
            'last_closed_at': rows[-1]['close_time'],
            'metrics': calculate_context_metrics(rows, timeframe),
            'indicators': calculate_context_indicators(rows, quote), 'structure': _structure(rows),
            'range_windows': [_range(rows, timeframe, days, Decimal(quote['price'])) for days in (7, 30, 90, 180)],
            'recent_closed_candles': [{key: row[key] for key in ('open_time', 'close_time', 'open', 'high', 'low', 'close', 'volume')}
                                      for row in rows[-RECENT_LIMITS[timeframe]:]],
        }
    return {'version': VERSION, 'as_of': cutoff.astimezone(UTC).isoformat(),
            'primary_timeframe': primary_timeframe, 'context_timeframes': list(higher_timeframes(primary_timeframe)),
            'data_basis': 'closed_candles', 'purpose': 'trend_background_not_entry_signal',
            'timeframes': frames}
