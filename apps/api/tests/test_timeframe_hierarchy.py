from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trade_helper.higher_timeframes import build_higher_timeframe_context
from trade_helper.market import fetch_candles, fetch_higher_timeframe_candles
from trade_helper.market_store import validate_candle
from trade_helper.strategy_engine import compare_timeframes, other_timeframe
from trade_helper.technical_snapshot import (
    additional_tool_schemas,
    build_technical_snapshot,
    compact_technical_snapshot,
    execute_additional_indicator,
)
from trade_helper.timeframes import (
    advance_candle,
    analysis_timeframes,
    candle_close,
    candle_open,
    higher_timeframes,
)

from .test_higher_timeframes import higher_rows
from .test_main_market_history import Response

MAPPING = {'1h': ('4h', '12h', '1d'), '4h': ('12h', '1d', '3d'),
           '12h': ('1d', '3d', '1w'), '1d': ('3d', '1w', '1M')}


@pytest.mark.parametrize('primary', MAPPING)
def test_hierarchy_and_comparison_keep_all_three_backgrounds(primary):
    assert higher_timeframes(primary) == MAPPING[primary]
    assert analysis_timeframes(primary) == (primary, *MAPPING[primary])
    assert other_timeframe(primary) == MAPPING[primary][0]
    metric = {'trend': 'bullish', 'last_candle_at': '2026-09-30T00:00:00+00:00',
              'ma20': '100', 'ma50': '90'}
    background = {frame: metric | {'trend': 'bearish' if frame == MAPPING[primary][-1] else 'bullish'}
                  for frame in MAPPING[primary]}
    result = compare_timeframes(metric, metric, primary, background)
    assert result['analysis_timeframes'] == list(analysis_timeframes(primary))
    assert result['context_timeframes'] == list(MAPPING[primary])
    assert result['contexts'][MAPPING[primary][-1]]['trend'] == 'bearish'
    # An aligned immediate background cannot erase the longer-frame conflict.
    assert result['relation'] == 'aligned'


@pytest.mark.parametrize('primary', MAPPING)
def test_fetch_reuses_next_frame_and_requests_only_the_remaining_two(primary, monkeypatch):
    next_rows = [{'close': '101'}]
    calls = []
    def fetch(market, frame, limit):
        calls.append((frame, limit))
        return [{'close': '102'}]
    monkeypatch.setattr('trade_helper.market.fetch_candles', fetch)
    data = fetch_higher_timeframe_candles('binance:perp:BTCUSDT', primary,
                                        context_candles=next_rows)
    assert tuple(data) == MAPPING[primary]
    assert data[MAPPING[primary][0]]['candles'] is next_rows
    assert sorted(calls) == sorted((frame, 180) for frame in MAPPING[primary][1:])
    assert data[MAPPING[primary][0]]['requested_candles'] == 1000


@pytest.mark.parametrize('primary', MAPPING)
def test_four_frame_snapshot_and_additional_indicators_use_own_evidence(primary):
    cutoff = datetime(2026, 10, 1, 8, 16, tzinfo=UTC)
    source = {frame: {'candles': higher_rows(frame, cutoff)} for frame in MAPPING[primary]}
    for frame_index, data in enumerate(source.values(), 2):
        for row in data['candles']:
            for key in ('open', 'high', 'low', 'close'):
                row[key] = str(Decimal(row[key]) * frame_index)
    quote = {'price': '140', 'tick_size': '0.1', 'observed_at': cutoff.isoformat(),
             'higher_timeframe_candles': source}
    rows = higher_rows(primary, cutoff)
    context = source[MAPPING[primary][0]]['candles']
    request = {'market_id': 'binance:perp:BTCUSDT', 'timeframe': primary}
    snapshot = build_technical_snapshot(request, rows, quote, context)
    assert tuple(snapshot['timeframes']) == analysis_timeframes(primary)
    assert all(data['status'] == 'available' for data in snapshot['timeframes'].values())
    for frame in MAPPING[primary]:
        assert snapshot['timeframes'][frame]['metrics']['last_close'] == source[frame]['candles'][-1]['close']
    schemas = additional_tool_schemas(snapshot)
    assert all(schema['parameters']['properties']['timeframe']['enum'] == list(analysis_timeframes(primary))
               for schema in schemas)
    target = MAPPING[primary][-1]
    output = execute_additional_indicator('rsi', {'reason': '較大週期動能', 'period': 21, 'timeframe': target},
                                         request, rows, quote, context, snapshot)
    from trade_helper.indicators import rsi
    assert output['result']['value'] == rsi(source[target]['candles'], quote, {'period': 21})['value']
    compact = compact_technical_snapshot(snapshot)
    for frame in MAPPING[primary]:
        background = compact['higher_timeframe_context']['timeframes'][frame]
        assert background['technical_frame_ref'] == f'timeframes.{frame}'
        assert 'recent_closed_candles' not in background
        assert 'indicators' not in background
        assert compact['timeframes'][frame]['recent_closed_candles']['rows']
    if primary == '1d':
        month = compact['timeframes']['1M']['recent_closed_candles']
        assert month['interval'] == '1M' and month['interval_hours'] is None
        assert month['calendar_months'] == 1


@pytest.mark.parametrize('frame,opened,expected', [
    ('3d', '2026-09-30T13:16:00+00:00', '2026-09-29T00:00:00+00:00'),
    ('1w', '2026-10-01T13:16:00+00:00', '2026-09-28T00:00:00+00:00'),
    ('1M', '2024-02-29T13:16:00+00:00', '2024-02-01T00:00:00+00:00'),
])
def test_exchange_open_boundaries(frame, opened, expected):
    assert candle_open(datetime.fromisoformat(opened), frame).isoformat() == expected


def test_monthly_leap_year_close_and_staleness_follow_calendar():
    opened = datetime(2024, 2, 1, tzinfo=UTC)
    assert candle_close(opened, '1M').isoformat() == '2024-02-29T23:59:59.999000+00:00'
    assert advance_candle(opened, '1M', -1) == datetime(2024, 1, 1, tzinfo=UTC)
    quote = {'price': '140', 'observed_at': '2024-03-31T12:00:00+00:00',
             'higher_timeframe_candles': {'1M': {'candles': higher_rows('1M', datetime(2024, 3, 1, tzinfo=UTC), 24)}}}
    month = build_higher_timeframe_context(quote, '1d')['timeframes']['1M']
    assert month['status'] == 'available'
    assert month['duration_days'] == '731'
    assert month['metrics']['ma50'] is None
    assert month['indicators']['rsi']['value']
    assert month['indicators']['macd']['status'] == 'unavailable'
    assert month['range_windows'][-1]['duration_days'] != '180'  # Whole calendar months.
    quote['observed_at'] = '2024-04-01T00:00:00+00:00'
    assert build_higher_timeframe_context(quote, '1d')['timeframes']['1M']['reason'] == 'stale_closed_candles'


def test_short_monthly_history_still_supplies_prices_without_fabricated_warmup():
    cutoff = datetime(2026, 10, 1, 8, 16, tzinfo=UTC)
    quote = {'price': '140', 'observed_at': cutoff.isoformat(),
             'higher_timeframe_candles': {'1M': {'candles': higher_rows('1M', cutoff, 10)}}}
    month = build_higher_timeframe_context(quote, '1d')['timeframes']['1M']
    assert month['status'] == 'available' and month['coverage_status'] == 'partial'
    assert month['metrics']['trend'] is None
    assert month['metrics']['ma20'] is None and month['metrics']['ma50'] is None
    assert month['indicators']['rsi']['status'] == 'unavailable'
    assert len(month['recent_closed_candles']) == 10
    assert month['range_windows'][0]['candle_count'] == 1


@pytest.mark.parametrize('frame', ['3d', '1w', '1M'])
def test_provider_returns_native_interval_and_excludes_forming_candle(frame, monkeypatch):
    now = datetime.now(UTC)
    opened = candle_open(now, frame)
    def raw(at):
        return [int(at.timestamp() * 1000), '100', '102', '98', '101', '10',
                int(candle_close(at, frame).timestamp() * 1000)]
    supplied = [raw(advance_candle(opened, frame, -2)), raw(advance_candle(opened, frame, -1)), raw(opened)]
    def get(url, *, params, timeout):
        assert params['interval'] == frame
        return Response(supplied)
    monkeypatch.setattr('trade_helper.market.httpx.get', get)
    result = fetch_candles('binance:perp:BTCUSDT', frame, 2)
    assert len(result) == 2
    assert result[-1]['close_time'] == candle_close(advance_candle(opened, frame, -1), frame).isoformat()
    for row in result:
        validate_candle(row, frame)


def test_three_day_exchange_anchor_changes_keep_only_valid_recent_suffix(monkeypatch):
    # Binance's real historical series changes from 2023-08-14 to 2023-08-16
    # after only two days. Treat the earlier segment as incomplete history.
    opens = [datetime(2023, 8, day, tzinfo=UTC) for day in (11, 14, 16, 19, 22)]
    supplied = [[int(at.timestamp() * 1000), '100', '102', '98', '101', '10',
                 int(candle_close(at, '3d').timestamp() * 1000)] for at in opens]
    monkeypatch.setattr('trade_helper.market.httpx.get', lambda *_args, **_kwargs: Response(supplied))
    result = fetch_candles('binance:perp:BTCUSDT', '3d', 1000)
    assert len(result) == 3
    assert result[0]['open_time'] == '2023-08-16T00:00:00+00:00'
    assert result[-1]['open_time'] == '2023-08-22T00:00:00+00:00'
    assert result.history_quality['status'] == 'truncated'
    assert result.history_quality['removed_candles'] == 2
    assert result.history_quality['provided_closed_candles'] == 5
    assert result.history_quality['retained_closed_candles'] == 3
    assert result.history_quality['reason'] == 'exchange_3d_historical_discontinuity'
    for row in result:
        validate_candle(row, '3d')
    background = fetch_higher_timeframe_candles('binance:perp:BTCUSDT', '1d', context_candles=result)
    assert background['3d']['history_quality'] == result.history_quality
