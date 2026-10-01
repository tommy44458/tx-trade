from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from trade_helper.higher_timeframes import build_higher_timeframe_context
from trade_helper.indicators import TOOL_FUNCTIONS
from trade_helper.market import fetch_higher_timeframe_candles
from trade_helper.reasoning_evidence import validate_reason_numbers
from trade_helper.report_contract import validate_report
from trade_helper.technical_snapshot import build_technical_snapshot, compact_technical_snapshot
from trade_helper.timeframes import advance_candle, analysis_timeframes, candle_close, candle_open

from .test_technical_snapshot import final_response, fixture


def higher_rows(timeframe, cutoff, count=180):
    end = candle_open(cutoff, timeframe)
    rows = []
    for i in range(count):
        opened = advance_candle(end, timeframe, -(count-i))
        # Unequal cycle highs/lows produce confirmed rising waves, without ties.
        close = Decimal(100 + i // 12 * 2 + (i % 12 if i % 12 <= 6 else 12-i % 12))
        rows.append({'open_time': opened.isoformat(),
                     'close_time': candle_close(opened, timeframe).isoformat(),
                     'open': str(close-Decimal('.3')), 'high': str(close+1),
                     'low': str(close-1), 'close': str(close), 'volume': str(100+i)})
    return rows


def higher_quote(cutoff=None):
    cutoff = cutoff or datetime.now(UTC)
    return {'observed_at': cutoff.isoformat(), 'price': '140', 'tick_size': '0.1',
            'higher_timeframe_candles': {tf: {'candles': higher_rows(tf, cutoff)} for tf in ('4h', '12h', '1d')}}


def test_background_uses_own_frames_and_truthful_window_coverage():
    quote = higher_quote()
    for row in quote['higher_timeframe_candles']['1d']['candles']:
        for key in ('open', 'high', 'low', 'close'):
            row[key] = str(Decimal(row[key])*2)
    context = build_higher_timeframe_context(quote)
    assert context['data_basis'] == 'closed_candles'
    for tf, days, raw_count in (('12h', '90', 36), ('1d', '180', 30)):
        data = context['timeframes'][tf]
        rows = quote['higher_timeframe_candles'][tf]['candles']
        assert data['status'] == 'available'
        assert data['candle_count'] == 180 and data['duration_days'] == days
        assert len(data['recent_closed_candles']) == raw_count
        assert data['recent_closed_candles'][-1] == rows[-1]
        assert data['indicators']['rsi'] == TOOL_FUNCTIONS['rsi'](rows, quote, {'period': 14})
        assert data['structure']['state'] == 'rising'
        assert all(point['confirmed_at'] <= data['last_closed_at'] for point in data['structure']['recent_points'])
        assert data['range_windows'][0]['candle_count'] == (14 if tf == '12h' else 7)
        assert data['range_windows'][-1]['status'] == ('partial' if tf == '12h' else 'complete')
        assert data['range_windows'][-1]['duration_days'] == days
        # Trend context cannot invent entry support/resistance zones.
        assert 'levels' not in data
    assert context['timeframes']['12h']['indicators']['trend_ema'] != context['timeframes']['1d']['indicators']['trend_ema']


@pytest.mark.parametrize('mutation,message', [
    ('future', 'future'), ('gap', 'gap'), ('duplicate', 'duplicate'),
    ('misaligned', 'times'), ('subsecond', 'times'), ('invalid_high', 'OHLCV'), ('nan', 'OHLCV'),
])
def test_invalid_or_future_background_is_never_used(mutation, message):
    quote = higher_quote()
    rows = quote['higher_timeframe_candles']['12h']['candles']
    if mutation == 'future':
        quote['observed_at'] = rows[-2]['close_time']
    elif mutation == 'gap':
        rows.pop(50)
    elif mutation == 'duplicate':
        rows[50] = deepcopy(rows[49])
    elif mutation in {'misaligned', 'subsecond'}:
        delta = timedelta(hours=1) if mutation == 'misaligned' else timedelta(microseconds=1)
        for row in rows:
            for key in ('open_time', 'close_time'):
                row[key] = (datetime.fromisoformat(row[key])+delta).isoformat()
    else:
        rows[-1]['high'] = '1' if mutation == 'invalid_high' else 'NaN'
    with pytest.raises(ValueError, match=message):
        build_higher_timeframe_context(quote)


def test_missing_short_stale_and_flat_background_are_explicit():
    quote = higher_quote()
    quote['higher_timeframe_candles']['12h']['candles'] = quote['higher_timeframe_candles']['12h']['candles'][-59:]
    quote['higher_timeframe_candles']['1d'] = {'candles': [], 'reason': 'fetch_failed'}
    frames = build_higher_timeframe_context(quote)['timeframes']
    assert frames['12h']['status'] == 'available'
    assert frames['12h']['metrics']['status'] == 'partial'
    assert 'fibonacci' not in frames['12h']['indicators']
    assert 'bollinger' not in frames['12h']['indicators']
    assert frames['1d']['reason'] == 'fetch_failed'
    quote = higher_quote()
    quote['observed_at'] = (datetime.fromisoformat(quote['observed_at'])+timedelta(days=1)).isoformat()
    assert build_higher_timeframe_context(quote)['timeframes']['1d']['reason'] == 'stale_closed_candles'
    quote = higher_quote()
    for item in quote['higher_timeframe_candles'].values():
        for row in item['candles']:
            row.update(open='100', high='100', low='100', close='100', volume='0')
    frames = build_higher_timeframe_context(quote)['timeframes']
    assert frames['12h']['structure']['state'] == 'insufficient'
    assert frames['12h']['structure']['recent_points'] == []
    assert frames['12h']['range_windows'][0]['quote_position_pct'] is None
    assert frames['1d']['indicators']['volume_signal']['relative_volume'] is None


def test_range_position_can_be_outside_and_unconfirmed_last_wave_excluded():
    quote = higher_quote()
    frames = build_higher_timeframe_context(quote)['timeframes']
    assert Decimal(frames['1d']['range_windows'][0]['quote_position_pct']) > 100
    rows = quote['higher_timeframe_candles']['12h']['candles']
    rows[-1]['high'] = '999'
    frame = build_higher_timeframe_context(quote)['timeframes']['12h']
    assert all(p['price'] != '999' for p in frame['structure']['recent_points'])
    assert frame['range_windows'][0]['high'] == '999'


def test_model_context_compaction_keeps_prices_and_report_rejects_tampering(monkeypatch):
    import json
    from types import SimpleNamespace

    from trade_helper.agent import analyze_with_tools
    from trade_helper.analysis import build_report
    from trade_helper.technical_snapshot import prepare_analysis_evidence

    request, candles, context, quote = fixture()
    quote.update(higher_quote(datetime.fromisoformat(quote['observed_at'])))
    snapshot = build_technical_snapshot(request, candles, quote, context)
    compact = compact_technical_snapshot(snapshot)
    assert set(compact['timeframes']) == set(analysis_timeframes('1h'))
    for tf, count in (('12h', 36), ('1d', 30)):
        frame = compact['higher_timeframe_context']['timeframes'][tf]
        assert frame['technical_frame_ref'] == f'timeframes.{tf}'
        assert not {'metrics', 'indicators', 'recent_closed_candles'} & frame.keys()
        table = compact['timeframes'][tf]['recent_closed_candles']
        assert len(table['rows']) == count
        assert table['rows'][-1][4] == quote['higher_timeframe_candles'][tf]['candles'][-1]['close']
    trace = prepare_analysis_evidence(request, candles, quote, context)
    def create(**kwargs):
        payload = json.loads(kwargs['input'][0]['content'])
        higher = payload['precomputed_evidence']['technical_snapshot']['higher_timeframe_context']
        assert higher == compact['higher_timeframe_context']
        return SimpleNamespace(output=[], output_text=final_response(trace))
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    monkeypatch.setenv('OPENAI_MODEL', 'test-model')
    monkeypatch.setattr('trade_helper.agent.OpenAI', lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)))
    decision = analyze_with_tools(request, candles, quote, context)
    report = build_report(request, candles, quote, [], decision, context)
    validate_report(report)
    missing = deepcopy(report)
    missing['technical_snapshot'].pop('higher_timeframe_context')
    with pytest.raises(ValueError, match='Higher timeframe context'):
        validate_report(missing)
    report['technical_snapshot']['higher_timeframe_context']['timeframes']['12h']['range_windows'][0]['high'] = '100000'
    with pytest.raises(ValueError, match='Higher timeframe context'):
        validate_report(report)


def test_higher_indicators_must_be_attributed_to_correct_frame():
    trace = [{'tool': 'technical_snapshot', 'result': {'timeframes': {}, 'higher_timeframe_context': {'timeframes': {
        '12h': {'indicators': {'rsi': {'value': '43.2'}}},
        '1d': {'indicators': {'rsi': {'value': '51.8'}}},
    }}}}]
    validate_reason_numbers('12H RSI14為43.2；1D RSI14為51.8。', trace)
    for text in ('12H RSI14為51.8。', '1D RSI14為43.2。'):
        with pytest.raises(ValueError, match='wrong indicator or timeframe'):
            validate_reason_numbers(text, trace)


def test_public_fetch_requests_180_closed_candles_and_marks_partial_failure(monkeypatch):
    calls = []
    def fetch(market_id, timeframe, count):
        calls.append((market_id, timeframe, count))
        if timeframe == '1d':
            raise httpx.ReadTimeout('unavailable')
        return [{'close': '100'}]
    monkeypatch.setattr('trade_helper.market.fetch_candles', fetch)
    result = fetch_higher_timeframe_candles('binance:perp:SOLUSDT')
    assert sorted(calls) == [('binance:perp:SOLUSDT', '12h', 180), ('binance:perp:SOLUSDT', '1d', 180), ('binance:perp:SOLUSDT', '4h', 180)]
    assert result['12h']['candles'] == [{'close': '100'}]
    assert result['12h']['requested_candles'] == 180
    assert result['1d'] == {'candles': [], 'reason': 'fetch_failed'}
