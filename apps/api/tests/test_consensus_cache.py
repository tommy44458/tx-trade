import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

import trade_helper.consensus_probe as probe
from trade_helper.db import connect, init_db


@pytest.fixture
def configured_source(sqlite_db, monkeypatch):
    monkeypatch.setenv('JBLANKED_API_KEY', 'fake-local-test-key')
    init_db()


def test_missing_key_does_not_initialize_database_or_make_request(monkeypatch):
    monkeypatch.delenv('JBLANKED_API_KEY', raising=False)
    monkeypatch.setattr(probe, 'init_db', lambda: pytest.fail('Unexpected DB initialization'))
    assert probe.probe_jblanked_cached()['status'] == 'missing_key'


@pytest.mark.parametrize('result', [
    {'status': 'checked', 'provider': 'jblanked', 'usable_for_strategy': False,
     'observed_at': '2026-09-30T12:00:00+00:00',
     'unverified_forecast_samples': [{'name': 'CPI m/m', 'forecast': '0.2'}]},
    {'status': 'unavailable', 'provider': 'jblanked', 'http_status': 401,
     'error_code': 'AUTHENTICATION_REJECTED', 'usable_for_strategy': False},
    {'status': 'unavailable', 'provider': 'jblanked', 'error_code': 'TIMEOUT',
     'usable_for_strategy': False},
])
def test_success_and_failure_both_consume_local_daily_budget(configured_source, monkeypatch, result):
    calls = []
    monkeypatch.setattr(probe, 'probe_jblanked', lambda: calls.append(True) or result)
    first = probe.probe_jblanked_cached()
    second = probe.probe_jblanked_cached()
    assert calls == [True]
    assert first['from_cache'] is False
    assert second['from_cache'] is True
    assert second['next_local_attempt_at'] == first['next_local_attempt_at']
    assert second['status'] == result['status']
    assert second['usable_for_strategy'] is False
    if 'observed_at' in result:
        assert second['observed_at'] == result['observed_at']  # No fresh timestamp on cached data.
        assert second['unverified_forecast_samples'] == result['unverified_forecast_samples']
    with connect() as db:
        saved = db.execute('SELECT * FROM consensus_provider_checks').fetchall()
    assert len(saved) == 1
    assert 'fake-local-test-key' not in json.dumps(saved[0]['result'])


def test_concurrent_processes_share_committed_reservation(configured_source, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fetch():
        calls.append(True)
        started.set()
        assert release.wait(timeout=10)
        return {'status': 'checked', 'provider': 'jblanked', 'usable_for_strategy': False}

    monkeypatch.setattr(probe, 'probe_jblanked', fetch)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(probe.probe_jblanked_cached)
        try:
            assert started.wait(timeout=10)
            second = probe.probe_jblanked_cached()
            assert second['from_cache'] is True
            assert second['error_code'] == 'ATTEMPT_RESERVED'
            assert calls == [True]
        finally:
            release.set()
        assert first_future.result(timeout=10)['status'] == 'checked'


def test_abandoned_attempt_and_key_change_do_not_reset_daily_budget(configured_source, monkeypatch):
    now = datetime.now(UTC)
    with connect() as db:
        db.execute(
            'INSERT INTO consensus_provider_checks(provider,reserved_at,next_allowed_at,result) '
            'VALUES (?,?,?,?)',
            ('jblanked', now, now + timedelta(hours=24),
             json.dumps({'status': 'waiting', 'error_code': 'ATTEMPT_RESERVED'})),
        )
    monkeypatch.setenv('JBLANKED_API_KEY', 'another-fake-key')
    monkeypatch.setattr(probe, 'probe_jblanked', lambda: pytest.fail('Unexpected request'))
    assert probe.probe_jblanked_cached()['from_cache'] is True


def test_expired_reservation_allows_next_attempt(configured_source, monkeypatch):
    now = datetime.now(UTC)
    with connect() as db:
        db.execute(
            'INSERT INTO consensus_provider_checks(provider,reserved_at,next_allowed_at,result) '
            'VALUES (?,?,?,?)',
            ('jblanked', now - timedelta(hours=25), now - timedelta(hours=1),
             json.dumps({'status': 'unavailable'})),
        )
    calls = []
    monkeypatch.setattr(probe, 'probe_jblanked', lambda: calls.append(True) or {
        'status': 'checked', 'provider': 'jblanked', 'usable_for_strategy': False,
    })
    assert probe.probe_jblanked_cached()['from_cache'] is False
    assert probe.probe_jblanked_cached()['from_cache'] is True
    assert calls == [True]
    with connect() as db:
        assert db.execute('SELECT count(*) AS n FROM consensus_provider_checks').fetchone()['n'] == 2
