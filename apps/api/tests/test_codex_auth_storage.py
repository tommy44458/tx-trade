"""Only product-owned Codex login material is staged; shared stores are untouched."""

import json
import os

import pytest

from trade_helper import credential_store as store
from trade_helper.codex_auth_storage import LocalCodexAuth


def record(token):
    return {'auth': {'auth_mode': 'chatgpt', 'tokens': {
        'access_token': token, 'refresh_token': 'test-refresh-token', 'account_id': 'test-account'}}}


def test_new_login_persists_to_sqlite_and_next_session_restores_then_cleans():
    session = LocalCodexAuth()
    home = session.home
    assert not session.path.exists()
    session.path.write_text(json.dumps(record('test-access-token')['auth']))
    session.persist()
    assert store.load_credentials('codex') == record('test-access-token')
    if os.name == 'posix':
        assert session.path.stat().st_mode & 0o777 == 0o600
        assert session.home.stat().st_mode & 0o777 == 0o700
    session.close()
    assert not home.exists()
    restored = LocalCodexAuth()
    try:
        assert json.loads(restored.path.read_text()) == record('test-access-token')['auth']
    finally:
        restored.close()


def test_cli_token_refresh_persists_and_logout_removes_only_app_credentials():
    store.save_credentials('codex', record('first-test-token'))
    store.save_credentials('bingx', {'api_key': 'test-bingx-key'})
    session = LocalCodexAuth()
    session.path.write_text(json.dumps(record('new-test-token')['auth']))
    session.persist()
    assert store.load_credentials('codex') == record('new-test-token')
    session.path.unlink()
    session.persist()
    assert store.load_credentials('codex') == record('new-test-token')
    store.delete_credentials('codex')
    session.close()
    assert store.load_credentials('codex') is None
    assert store.load_credentials('bingx') == {'api_key': 'test-bingx-key'}


def test_stale_process_cannot_restore_logout():
    store.save_credentials('codex', record('first-test-token'))
    session = LocalCodexAuth()
    store.delete_credentials('codex')
    session.path.write_text(json.dumps(record('stale-refresh-test-token')['auth']))
    session.persist()
    session.close()
    assert store.load_credentials('codex') is None


def test_stale_process_cannot_overwrite_new_login_or_its_later_logout():
    store.save_credentials('codex', record('first-test-token'))
    stale = LocalCodexAuth()
    store.save_credentials('codex', record('other-account-test-token'))
    stale.path.write_text(json.dumps(record('stale-refresh-test-token')['auth']))
    stale.persist()
    assert store.load_credentials('codex') == record('other-account-test-token')
    store.delete_credentials('codex')
    stale.persist()
    stale.close()
    assert store.load_credentials('codex') is None


def test_normal_close_is_idempotent_and_unchanged_records_do_not_write():
    store.save_credentials('codex', record('first-test-token'))
    _, revision = store.load_credentials_with_revision('codex')
    session = LocalCodexAuth()
    session.persist()
    assert store.load_credentials_with_revision('codex')[1] == revision
    session.close()
    session.close()
    assert store.load_credentials('codex') == record('first-test-token')


@pytest.mark.parametrize('payload', ['[]', 'not json', '"private-test-token"', '{}', '{"tokens":[]}',
                                     '{"tokens":{"access_token":"test-token"}}'])
def test_invalid_cli_payload_cleanup_preserves_existing_record(payload):
    store.save_credentials('codex', record('first-test-token'))
    session = LocalCodexAuth()
    home = session.home
    session.path.write_text(payload)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        session.close()
    assert not home.exists()
    assert store.load_credentials('codex') == record('first-test-token')


def test_removed_temporary_directory_does_not_remove_saved_authorization():
    store.save_credentials('codex', record('first-test-token'))
    session = LocalCodexAuth()
    session.directory.cleanup()
    session.persist()
    session.close()
    assert store.load_credentials('codex') == record('first-test-token')


def test_dangling_symlink_is_rejected_without_clearing_saved_authorization():
    store.save_credentials('codex', record('first-test-token'))
    session = LocalCodexAuth()
    session.path.unlink()
    session.path.symlink_to(session.home / 'missing-file')
    with pytest.raises(ValueError):
        session.close()
    assert store.load_credentials('codex') == record('first-test-token')
