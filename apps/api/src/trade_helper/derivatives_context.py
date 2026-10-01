"""As-of open interest on supported Binance periods, never trade direction."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import httpx

from .market import BASE_URL, symbol_for
from .timeframes import analysis_timeframes

VERSION = 'derivatives_context_v2'
OI_HOURS = {'1h': 1, '4h': 4, '12h': 12, '1d': 24}
SOURCE = 'https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data'


def summarize_open_interest(rows: list[dict], market_id: str, timeframe: str,
                            cutoff: datetime) -> dict:
    if timeframe not in OI_HOURS:
        raise ValueError('Unsupported open interest timeframe')
    symbol = symbol_for(market_id)
    hours = OI_HOURS[timeframe]
    if cutoff.tzinfo is None or not isinstance(rows, list):
        raise ValueError('Invalid open interest input')
    cutoff_ms = int(cutoff.timestamp() * 1000)
    values = []
    for row in rows:
        oi, notional = Decimal(str(row['sumOpenInterest'])), Decimal(str(row['sumOpenInterestValue']))
        stamp = int(row['timestamp'])
        if row['symbol'] != symbol or not oi.is_finite() or not notional.is_finite() or notional < 0 or oi <= 0:
            raise ValueError('Invalid open interest observation')
        if stamp <= cutoff_ms:
            values.append((stamp, oi, notional))
    values.sort()
    if not values:
        return {'status': 'unavailable', 'reason': 'no_as_of_observation'}
    stamps = [v[0] for v in values]
    if len(set(stamps)) != len(stamps):
        raise ValueError('Duplicate open interest observation')
    last = values[-1]
    if cutoff_ms - last[0] > (hours * 3600 + 900) * 1000:
        return {'status': 'unavailable', 'reason': 'stale_observations'}
    start_ms = last[0] - 86_400_000
    window = [v for v in values if v[0] >= start_ms]
    complete = (window[0][0] == start_ms and len(window) == 24 // hours + 1 and
                all(b[0] - a[0] == hours * 3_600_000 for a, b in pairwise(window)))
    return {'status': 'available', 'timeframe': timeframe,
            'observed_at': datetime.fromtimestamp(last[0] / 1000, UTC).isoformat(),
            'open_interest_base_units': str(last[1]), 'open_interest_value_usdt': str(last[2]),
            'change_24h_pct': str(((last[1] / window[0][1] - 1) * 100).quantize(Decimal('.01'))) if complete else None,
            'change_window': 'complete_24h' if complete else 'insufficient_history',
            'observations': [{'timestamp': t, 'sumOpenInterest': str(oi), 'sumOpenInterestValue': str(n)} for t, oi, n in window]}


def fetch_derivatives_context(market_id: str, cutoff: datetime, timeframe: str = '1h') -> dict:
    frames = analysis_timeframes(timeframe)
    context = {'version': VERSION, 'market_id': market_id, 'as_of': cutoff.isoformat(),
               'source_url': SOURCE, 'timeframes': {}, 'primary_timeframe': timeframe,
               'analysis_timeframes': list(frames),
               'interpretation_limit': 'OI is outstanding contracts, not net long/short intent. No liquidation data.'}
    if os.getenv('TRADE_DERIVATIVES_CONTEXT_ENABLED', '1') != '1':
        return context | {'status': 'unavailable', 'reason': 'disabled'}

    def fetch(timeframe):
        if timeframe not in OI_HOURS:
            return {'status': 'unavailable', 'timeframe': timeframe,
                    'reason': 'unsupported_source_period'}
        try:
            response = httpx.get(f'{BASE_URL}/futures/data/openInterestHist', params={
                'symbol': symbol_for(market_id), 'period': timeframe,
                # Include the preceding 24h observation even for daily periods
                # when the latest available observation is almost one day old.
                'limit': 40,
                'startTime': int((cutoff - timedelta(hours=max(30, 25 + OI_HOURS[timeframe]))).timestamp() * 1000),
                'endTime': int(cutoff.timestamp() * 1000)}, timeout=6)
            response.raise_for_status()
            return summarize_open_interest(response.json(), market_id, timeframe, cutoff)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, ArithmeticError):
            return {'status': 'unavailable', 'reason': 'source_unavailable_or_invalid'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        context['timeframes'] = dict(zip(frames, pool.map(fetch, frames), strict=True))
    count = sum(v['status'] == 'available' for v in context['timeframes'].values())
    return context | {'status': 'available' if count == len(frames) else 'partial' if count else 'unavailable'}


def compact_derivatives(context: dict | None) -> dict | None:
    if context is None:
        return None
    return context | {'timeframes': {frame: {key: value for key, value in data.items() if key != 'observations'}
                                    for frame, data in context.get('timeframes', {}).items()}}


def validate_derivatives_context(context: dict, market_id: str, cutoff: datetime) -> None:
    if (context.get('version') not in {VERSION, 'derivatives_context_v1'} or context.get('market_id') != market_id or
            context.get('source_url') != SOURCE or context.get('as_of') != cutoff.isoformat()):
        raise ValueError('Derivatives context differs from market snapshot')
    frames = context.get('timeframes', {})
    expected_frames = {'1h', '4h'}
    if context.get('version') == VERSION:
        primary = context.get('primary_timeframe')
        expected_order = list(analysis_timeframes(primary))
        if context.get('analysis_timeframes') != expected_order:
            raise ValueError('Invalid derivatives analysis timeframes')
        expected_frames = set(expected_order)
    if context.get('reason') == 'disabled' and not frames and context.get('status') == 'unavailable':
        return
    if set(frames) != expected_frames:
        raise ValueError('Invalid derivatives timeframes')
    count = 0
    for frame, result in frames.items():
        if result.get('status') == 'available':
            if frame not in OI_HOURS:
                raise ValueError('Unsupported open interest period cannot be available')
            observations = result.get('observations', [])
            source_rows = [row | {'symbol': symbol_for(market_id)} for row in observations]
            if summarize_open_interest(source_rows, market_id, frame, cutoff) != result:
                raise ValueError('Open interest summary differs from observations')
            count += 1
        elif result.get('status') != 'unavailable' or result.get('observations'):
            raise ValueError('Invalid open interest availability')
    expected = 'available' if count == len(expected_frames) else 'partial' if count else 'unavailable'
    if context.get('status') != expected:
        raise ValueError('Invalid derivatives availability')
