from datetime import UTC, datetime, timedelta

import pytest

from trade_helper.derivatives_context import (
    SOURCE,
    VERSION,
    compact_derivatives,
    fetch_derivatives_context,
    summarize_open_interest,
    validate_derivatives_context,
)


def rows(frame='1h'):
    step = {'1h': 1, '4h': 4, '12h': 12, '1d': 24}[frame]
    end = datetime(2026, 9, 30, 12, tzinfo=UTC)
    return [{'symbol': 'BTCUSDT', 'timestamp': int((end - timedelta(hours=24 - i * step)).timestamp() * 1000),
             'sumOpenInterest': str(100 + i * step), 'sumOpenInterestValue': '1000000'}
            for i in range(24 // step + 1)]


@pytest.mark.parametrize('frame', ['1h', '4h', '12h', '1d'])
def test_complete_open_interest_window_and_future_filter(frame):
    data = rows(frame)
    cutoff = datetime(2026, 9, 30, 12, tzinfo=UTC)
    future = data[-1] | {'timestamp': int((cutoff + timedelta(hours=4)).timestamp() * 1000), 'sumOpenInterest': '99999'}
    result = summarize_open_interest([*data, future], 'binance:perp:BTCUSDT', frame, cutoff)
    assert result['change_24h_pct'] == '24.00'
    assert result['open_interest_base_units'] == '124'
    assert 'observations' not in compact_derivatives({'timeframes': {frame: result}})['timeframes'][frame]


def test_missing_history_does_not_fabricate_change():
    data = rows()
    result = summarize_open_interest(data[1:], 'binance:perp:BTCUSDT', '1h', datetime(2026, 9, 30, 12, tzinfo=UTC))
    assert result['change_24h_pct'] is None
    assert result['change_window'] == 'insufficient_history'


def test_invalid_symbol_duplicate_and_staleness():
    cutoff = datetime(2026, 9, 30, 12, tzinfo=UTC)
    for data in [[rows()[0] | {'symbol': 'ETHUSDT'}], [*rows(), rows()[-1]]]:
        with pytest.raises(ValueError):
            summarize_open_interest(data, 'binance:perp:BTCUSDT', '1h', cutoff)
    assert summarize_open_interest(rows()[:-2], 'binance:perp:BTCUSDT', '1h', cutoff)['status'] == 'unavailable'


def test_provider_failure_is_optional(monkeypatch):
    import httpx
    monkeypatch.setenv('TRADE_DERIVATIVES_CONTEXT_ENABLED', '1')
    def unavailable(*args, **kwargs):
        raise httpx.ReadTimeout('no response')
    monkeypatch.setattr('trade_helper.derivatives_context.httpx.get', unavailable)
    result = fetch_derivatives_context('binance:perp:BTCUSDT', datetime.now(UTC))
    assert result['status'] == 'unavailable'
    assert list(result['timeframes']) == ['1h', '4h', '12h', '1d']


def test_report_summary_recomputed_from_as_of_observations():
    cutoff = datetime(2026, 9, 30, 12, tzinfo=UTC)
    frames = {frame: summarize_open_interest(rows(frame), 'binance:perp:BTCUSDT', frame, cutoff)
              for frame in ('1h', '4h', '12h', '1d')}
    context = {'version': VERSION, 'source_url': SOURCE, 'market_id': 'binance:perp:BTCUSDT',
               'as_of': cutoff.isoformat(), 'status': 'available', 'timeframes': frames,
               'primary_timeframe': '1h', 'analysis_timeframes': ['1h', '4h', '12h', '1d']}
    validate_derivatives_context(context, 'binance:perp:BTCUSDT', cutoff)
    frames['4h']['change_24h_pct'] = '100.00'
    with pytest.raises(ValueError, match='differs'):
        validate_derivatives_context(context, 'binance:perp:BTCUSDT', cutoff)


def test_daily_analysis_marks_unsupported_oi_periods_without_network(monkeypatch):
    import httpx

    requested = []
    cutoff = datetime(2026, 9, 30, 12, tzinfo=UTC)

    def get(url, *, params, **_kwargs):
        requested.append(params)
        assert params['period'] == '1d'
        assert params['startTime'] <= int((cutoff - timedelta(hours=48)).timestamp() * 1000)
        return httpx.Response(200, json=rows('1d'), request=httpx.Request('GET', url))

    monkeypatch.setenv('TRADE_DERIVATIVES_CONTEXT_ENABLED', '1')
    monkeypatch.setattr('trade_helper.derivatives_context.httpx.get', get)
    result = fetch_derivatives_context('binance:perp:BTCUSDT', cutoff, '1d')
    assert len(requested) == 1
    assert result['status'] == 'partial'
    assert result['analysis_timeframes'] == ['1d', '3d', '1w', '1M']
    for frame in ('3d', '1w', '1M'):
        assert result['timeframes'][frame]['reason'] == 'unsupported_source_period'
        assert result['timeframes'][frame]['status'] == 'unavailable'
    validate_derivatives_context(result, 'binance:perp:BTCUSDT', cutoff)


def test_old_frozen_two_frame_oi_context_is_still_readable():
    cutoff = datetime(2026, 9, 30, 12, tzinfo=UTC)
    context = {'version': 'derivatives_context_v1', 'source_url': SOURCE,
               'market_id': 'binance:perp:BTCUSDT', 'as_of': cutoff.isoformat(),
               'status': 'available', 'timeframes': {
                   frame: summarize_open_interest(rows(frame), 'binance:perp:BTCUSDT', frame, cutoff)
                   for frame in ('1h', '4h')}}
    validate_derivatives_context(context, 'binance:perp:BTCUSDT', cutoff)
