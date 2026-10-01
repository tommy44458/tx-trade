import json

import pytest

from trade_helper import auth_metadata, local_settings
from trade_helper.db import connect


def test_auth_metadata_migrates_once_without_importing_tokens_or_replacing_backup():
    directory = local_settings.data_directory()
    directory.mkdir(parents=True)
    original = json.dumps({"status": {"authenticated": True, "account_type": "chatgpt"},
                           "access_token": "not-for-public-metadata", "models": [{"id": "test-model"}]})
    legacy = directory / "auth-chatgpt.json"
    legacy.write_text(original)
    actual = auth_metadata.read_metadata("chatgpt")
    assert actual == {"status": {"authenticated": True, "account_type": "chatgpt"},
                      "models": [{"id": "test-model"}]}
    auth_metadata.write_metadata("chatgpt", {})
    assert auth_metadata.read_metadata("chatgpt") == {}
    assert legacy.read_text() == original


def test_auth_metadata_is_in_sqlite_and_remains_nonsecret():
    auth_metadata.write_metadata("chatgpt", {"status": {"authenticated": True},
                                             "refresh_token": "must-not-be-imported"})
    auth_metadata.write_metadata("codex", {"status": {"authenticated": False}})
    assert auth_metadata.read_metadata("chatgpt") == {"status": {"authenticated": True}}
    assert auth_metadata.read_metadata("codex") == {"status": {"authenticated": False}}
    with connect(readonly=True) as db:
        rows = db.execute("SELECT provider,value_json FROM auth_metadata ORDER BY provider").fetchall()
    assert [row["provider"] for row in rows] == ["chatgpt", "codex"]
    assert "must-not" not in str(rows)
    assert not (local_settings.data_directory() / "auth-chatgpt.json").exists()


@pytest.mark.parametrize("provider", ["../chatgpt", "chatgpt/../../other", "", "bad\nname"])
def test_auth_metadata_rejects_provider_path_traversal(provider):
    with pytest.raises(ValueError):
        auth_metadata.read_metadata(provider)
    with pytest.raises(ValueError):
        auth_metadata.write_metadata(provider, {})
