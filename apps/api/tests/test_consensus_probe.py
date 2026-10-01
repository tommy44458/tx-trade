import json
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest

from trade_helper.consensus_probe import assess_rows, probe_jblanked


def test_missing_key_makes_no_request(monkeypatch):
    monkeypatch.delenv('JBLANKED_API_KEY', raising=False)
    monkeypatch.setattr(httpx, 'get', lambda *args, **kwargs: pytest.fail('Unexpected request'))
    assert probe_jblanked()['status'] == 'missing_key'


def test_forecast_zero_is_valid_null_is_missing_and_other_currency_ignored():
    result = assess_rows([
        {'Currency': 'USD', 'Name': 'Core CPI m/m', 'Forecast': 0},
        {'Currency': 'USD', 'Name': 'Non-Farm Employment Change', 'Forecast': None},
        {'Currency': 'CAD', 'Name': 'GDP q/q', 'Forecast': 2.1}])
    assert result['coverage']['CPI']['numeric_forecasts'] == 1
    assert result['coverage']['employment']['numeric_forecasts'] == 0
    assert result['coverage']['GDP']['events'] == 0
    assert result['usable_for_strategy'] is False


def test_names_do_not_confuse_jobs_or_gdp_price_index_with_target_series():
    rows = [{'Currency': 'USD', 'Name': name, 'Forecast': 1} for name in (
        'Non-Farm Employment Change', 'ADP Non-Farm Employment Change',
        'Unemployment Claims', 'Revised Nonfarm Productivity q/q',
        'Final GDP q/q', 'Final GDP Price Index q/q', 'GDP q/q',
        'Core PCE Price Index m/m', 'Core PCE Price Index y/y',
    )]
    result = assess_rows(rows)
    assert result['coverage']['employment']['events'] == 1
    assert result['coverage']['GDP']['event_names'] == ['Final GDP q/q']
    assert result['excluded_usd_events'] == 5
    assert result['metrics']['Final GDP q/q']['release_stage_from_name'] == 'third'
    assert result['metrics']['Core PCE Price Index m/m']['candidate_metric'] == 'core_pce_mom'
    assert result['metrics']['Core PCE Price Index y/y']['candidate_metric'] == 'core_pce_yoy'
    assert all(not metric['usable_for_strategy'] for metric in result['metrics'].values())


def test_schedule_distinguishes_pre_release_and_after_release_without_promoting_actuals():
    observed = datetime(2026, 9, 30, 12, tzinfo=UTC)
    dates = ['2026.09.30 16:00:00', '2026.09.30 15:00:00',
             '2026-09-29T13:00:00Z', '2026-10-01T08:30:00-04:00',
             'All Day', '2026-10-01', '2026-10-01T08:30:00']
    result = assess_rows([
        {'Currency': 'USD', 'Name': 'CPI m/m', 'Forecast': 0, 'Date': date,
         'Actual': 777.12345, 'Quality': 'arbitrary-provider-analysis'} for date in dates
    ], observed_at=observed)
    metric = result['metrics']['CPI m/m']
    assert metric['future_numeric_forecasts'] == 3
    assert metric['post_release_numeric_forecasts'] == 2
    assert metric['unknown_schedule'] == 2
    assert '2026-09-30T13:00:00+00:00' in metric['scheduled_at_utc']
    assert '2026-10-01T12:30:00+00:00' in metric['scheduled_at_utc']
    assert result['timezone_verified'] is False
    assert '777.12345' not in json.dumps(result)
    assert 'arbitrary-provider-analysis' not in json.dumps(result)


@pytest.mark.parametrize('forecast', [None, True, False, '', 'NaN', 'Infinity', '-Infinity', '0.4%', '130K'])
def test_ambiguous_nonfinite_or_missing_forecast_is_not_numeric(forecast):
    result = assess_rows([{'Currency': 'USD', 'Name': 'CPI m/m', 'Forecast': forecast}])
    assert result['coverage']['CPI']['numeric_forecasts'] == 0


@pytest.mark.parametrize('key', ['Api-Key secret', 'secret\nother', '"secret"'])
def test_invalid_key_format_makes_no_request(monkeypatch, key):
    monkeypatch.setenv('JBLANKED_API_KEY', key)
    monkeypatch.setattr(httpx, 'get', lambda *args, **kwargs: pytest.fail('Unexpected request'))
    result = probe_jblanked()
    assert result['error_code'] == 'INVALID_KEY_FORMAT'
    assert 'secret' not in json.dumps(result)


@pytest.mark.parametrize('status,code', [
    (401, 'AUTHENTICATION_REJECTED'), (403, 'ACCESS_DENIED'),
    (429, 'RATE_LIMITED'), (500, 'PROVIDER_ERROR'), (400, 'HTTP_ERROR'),
])
def test_http_errors_are_actionable_without_leaking_provider_body(monkeypatch, status, code):
    monkeypatch.setenv('JBLANKED_API_KEY', 'super-secret')
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(status, json={'message': 'super-secret provider message'})

    monkeypatch.setattr(httpx, 'get', get)
    result = probe_jblanked()
    assert len(calls) == 1  # No retries on permission or quota failures.
    assert result['status'] == 'unavailable'
    assert result['http_status'] == status
    assert result['error_code'] == code
    assert result['next_step']
    assert result['usable_for_strategy'] is False
    assert 'super-secret' not in json.dumps(result)


@pytest.mark.parametrize('error,code', [
    (httpx.ReadTimeout('super-secret'), 'TIMEOUT'),
    (httpx.ConnectError('super-secret'), 'NETWORK_ERROR'),
])
def test_network_errors_are_sanitized(monkeypatch, error, code):
    monkeypatch.setenv('JBLANKED_API_KEY', 'super-secret')

    def get(*args, **kwargs):
        raise error

    monkeypatch.setattr(httpx, 'get', get)
    result = probe_jblanked()
    assert result['error_code'] == code
    assert 'super-secret' not in json.dumps(result)


@pytest.mark.parametrize('body', [{'message': 'super-secret'}, 'super-secret'])
def test_success_status_with_non_calendar_payload_is_unavailable(monkeypatch, body):
    monkeypatch.setenv('JBLANKED_API_KEY', 'super-secret')
    monkeypatch.setattr(httpx, 'get', lambda *args, **kwargs: httpx.Response(200, json=body))
    result = probe_jblanked()
    assert result['error_code'] == 'INVALID_RESPONSE'
    assert 'super-secret' not in json.dumps(result)


def test_success_checks_exact_request_and_does_not_assert_monthly_coverage(monkeypatch):
    monkeypatch.setenv('JBLANKED_API_KEY', 'super-secret')

    def get(url, **kwargs):
        assert url == 'https://www.jblanked.com/news/api/forex-factory/calendar/range/'
        assert kwargs['headers']['Authorization'] == 'Api-Key super-secret'
        assert kwargs['headers']['Content-Type'] == 'application/json'
        assert kwargs['params']['currency'] == 'USD'
        assert kwargs['timeout'] == 30
        return httpx.Response(200, json=[{'Currency': 'USD', 'Name': 'CPI m/m', 'Forecast': 0.2}])

    monkeypatch.setattr(httpx, 'get', get)
    result = probe_jblanked()
    assert result['status'] == 'checked'
    assert result['requested_window']['full_coverage_verified'] is False
    assert result['usable_for_strategy'] is False
    assert 'super-secret' not in json.dumps(result)


def test_naive_observation_time_is_rejected():
    with pytest.raises(ValueError, match='timezone'):
        assess_rows([], observed_at=datetime(2026, 9, 30, tzinfo=UTC).replace(tzinfo=None))


def test_database_failure_stops_before_request_and_hides_connection_details(monkeypatch, capsys):
    import trade_helper.consensus_probe as probe

    monkeypatch.setenv('JBLANKED_API_KEY', 'super-secret')

    def unavailable():
        raise sqlite3.OperationalError('/private/db-super-secret')

    monkeypatch.setattr(probe, 'init_db', unavailable)
    monkeypatch.setattr(httpx, 'get', lambda *args, **kwargs: pytest.fail('Unexpected request'))
    probe.main()
    result = capsys.readouterr().out
    assert 'LOCAL_CACHE_UNAVAILABLE' in result
    assert 'super-secret' not in result
