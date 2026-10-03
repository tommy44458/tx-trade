import json
import os
import subprocess
from types import SimpleNamespace

import pytest
from cli_fakes import runnable_script

from trade_helper import codex_bridge
from trade_helper.local_settings import save_preferences


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    executable = tmp_path / "fake-codex"
    executable = runnable_script(executable, '''
import json
import sys

def send(value):
    print(json.dumps(value), flush=True)

for line in sys.stdin:
    m=json.loads(line)
    method=m.get('method')
    if method=='initialize':send({'id':m['id'],'result':{}})
    elif method=='config/read':send({'id':m['id'],'result':{'config':{'mcp_servers':{'unsafe':{}}}}})
    elif method=='thread/start':
        p=m['params']
        assert p['environments']==[] and p['sandbox']=='read-only' and p['approvalPolicy']=='never'
        assert p['config']['features.shell_tool'] is False
        assert p['config']['features.plugins'] is False
        assert p['config']['mcp_servers.unsafe.enabled'] is False
        assert p['dynamicTools'][0]['inputSchema']['type']=='object'
        send({'id':m['id'],'result':{'thread':{'id':'test-thread'}}})
    elif method=='turn/start':
        assert m['params']['effort']=='medium'
        send({'id':m['id'],'result':{'turn':{'id':'test-turn'}}})
        send({'id':100,'method':'item/tool/call','params':{'threadId':'test-thread','tool':'rsi','arguments':{'period':14}}})
    elif m.get('id')==100:
        assert m['result']['success'] is True
        value=json.loads(m['result']['contentItems'][0]['text'])
        assert value['rsi']=='55.25'
        send({'id':101,'method':'command/exec','params':{'command':'must-not-run'}})
    elif m.get('id')==101:
        assert m['error']['code']==-32601
        send({'method':'item/completed','params':{'threadId':'test-thread','item':{'type':'agentMessage','phase':'final_answer','text':'{"strategy":"hold"}'}}})
        send({'method':'thread/tokenUsage/updated','params':{'threadId':'test-thread','tokenUsage':{'last':{'inputTokens':50,'outputTokens':10},'total':{'inputTokens':100,'outputTokens':20}}}})
        send({'method':'turn/completed','params':{'threadId':'test-thread','turn':{'id':'test-turn','status':'completed'}}})
''')
    monkeypatch.setattr(codex_bridge, "codex_executable", lambda: str(executable))
    return executable


def test_stdio_protocol_only_executes_registered_python_tools_and_closes(fake_codex):
    calls = []
    rpc = codex_bridge.CodexRpc()
    process = rpc.process
    try:
        result = rpc.analyze("Instructions", "Context", "model", [{"name": "rsi", "description": "RSI",
            "parameters": {"type": "object"}}],
            lambda name, arguments: (calls.append((name, arguments)), {"rsi": "55.25"})[1], timeout=2)
        assert json.loads(result["text"]) == {"strategy": "hold"}
        assert calls == [("rsi", {"period": 14})]
        assert result["usage"]["inputTokens"] == 100
        assert result["diagnostics"]["last_stage"] == "撰寫報告"
        assert result["diagnostics"]["notification_counts"]["turn/completed"] == 1
        assert "Context" not in str(result["diagnostics"])
    finally:
        rpc.close()
    assert process.poll() is not None


@pytest.mark.parametrize("desktop", [True, False])
def test_desktop_codex_inherits_supervisor_group_and_secrets_are_not_in_environment(fake_codex, monkeypatch, desktop):
    monkeypatch.setenv("APP_DESKTOP", "1" if desktop else "0")
    for name in ("BINGX_API_KEY", "TYPESAFE_API_KEY", "OPENAI_API_KEY", "DATABASE_URL", "APP_DESKTOP_TOKEN"):
        monkeypatch.setenv(name, "secret")
    captured = []

    def start(*args, **kwargs):
        captured.append(kwargs)
        return subprocess.Popen(*args, **kwargs)

    rpc = codex_bridge.CodexRpc(process_factory=start)
    try:
        assert captured[0]["start_new_session"] is (not desktop and os.name != "nt")
        assert not any(name in captured[0]["env"] for name in
                       ("BINGX_API_KEY", "TYPESAFE_API_KEY", "OPENAI_API_KEY", "DATABASE_URL", "APP_DESKTOP_TOKEN"))
    finally:
        rpc.close()
    assert rpc.process.poll() is not None


def test_existing_codex_logout_only_disconnects_product(monkeypatch):
    save_preferences({"codex_auth_scope": "existing"})
    monkeypatch.setattr(codex_bridge, "_auth_server", lambda: pytest.fail("must not log out shared CLI"))
    monkeypatch.setattr(codex_bridge, "shutdown", lambda: None)
    result = codex_bridge.logout()
    assert result["authenticated"] is False
    assert codex_bridge.preferences()["codex_disconnected"] is True


def test_silent_server_deadline_cleans_child(fake_codex):
    rpc = codex_bridge.CodexRpc()
    try:
        with pytest.raises(TimeoutError, match="最後階段"):
            rpc.request("unsupported-test-method", timeout=0.05)
    finally:
        rpc.close()
    assert rpc.process.poll() is not None


def test_passive_status_and_model_catalog_never_start_authenticated_codex(monkeypatch):
    monkeypatch.setattr(codex_bridge, "_login_id", None)
    monkeypatch.setattr(codex_bridge, "_auth_server", lambda: pytest.fail("passive requests must not start Codex"))
    assert not codex_bridge.status()["status_known"]
    assert not codex_bridge.status()["authenticated"]
    codex_bridge._cache_status({"authenticated": True, "available": True,
                               "auth_source": "existing_codex", "email": "test@example.test"})
    for _ in range(3):
        assert codex_bridge.status()["authenticated"]
        assert codex_bridge.cached_models() == {"data": [], "nextCursor": None}


def test_cached_status_is_not_used_to_authorize_actual_analysis(monkeypatch):
    monkeypatch.setattr(codex_bridge, "_login_id", None)
    codex_bridge._cache_status({"authenticated": True, "available": True,
                               "auth_source": "existing_codex"})
    requests = []
    rpc = SimpleNamespace(isolated=False, request=lambda method, params, **kw:
                          (requests.append((method, params)), {"account": None})[1])
    monkeypatch.setattr(codex_bridge, "_auth_server", lambda: rpc)
    with pytest.raises(codex_bridge.CodexError, match="連接 Codex"):
        codex_bridge.require_authorized()
    assert requests == [("account/read", {"refreshToken": False})]
    assert not codex_bridge.status()["authenticated"]


def test_authorized_login_polling_stops_live_probes_after_login_completes(monkeypatch):
    monkeypatch.setattr(codex_bridge, "_login_id", "explicit-user-login")
    rpc = SimpleNamespace(isolated=True, request=lambda *_args, **_kw:
                          {"account": {"type": "chatgpt", "email": "test@example.test"}})
    monkeypatch.setattr(codex_bridge, "_auth_server", lambda: rpc)
    assert codex_bridge.status()["authenticated"]
    assert codex_bridge._login_id is None
    monkeypatch.setattr(codex_bridge, "_auth_server", lambda: pytest.fail("completed login must use metadata"))
    assert codex_bridge.status()["authenticated"]


def test_plain_text_discussion_removes_json_instruction_and_exposes_no_tools(tmp_path, monkeypatch):
    executable = tmp_path / "fake-discussion-codex"
    executable = runnable_script(executable, '''
import json
import sys

def send(value):
    print(json.dumps(value), flush=True)

for line in sys.stdin:
    message=json.loads(line)
    method=message.get('method')
    if method=='initialize':send({'id':message['id'],'result':{}})
    elif method=='config/read':send({'id':message['id'],'result':{'config':{'mcp_servers':{'private':{}}}}})
    elif method=='thread/start':
        params=message['params']
        assert params['dynamicTools']==[]
        assert params['environments']==[]
        assert params['config']['web_search']=='disabled'
        assert params['config']['features.shell_tool'] is False
        assert params['config']['mcp_servers.private.enabled'] is False
        assert 'Return the report as JSON' not in params['developerInstructions']
        assert 'response language specified by the task instructions' in params['developerInstructions']
        assert 'Traditional Chinese' not in params['developerInstructions']
        send({'id':message['id'],'result':{'thread':{'id':'discussion-thread'}}})
    elif method=='turn/start':
        send({'id':message['id'],'result':{'turn':{'id':'discussion-turn'}}})
        send({'id':101,'method':'item/tool/call','params':{'tool':'shell','arguments':{}}})
    elif message.get('id')==101:
        assert message['error']['code']==-32601
        send({'method':'item/completed','params':{'threadId':'discussion-thread','item':{'type':'agentMessage','phase':'final_answer','text':'先依原報告的價格結構評估。'}}})
        send({'method':'turn/completed','params':{'threadId':'discussion-thread','turn':{'id':'discussion-turn','status':'completed'}}})
''')
    monkeypatch.setattr(codex_bridge, "codex_executable", lambda: str(executable))
    rpc = codex_bridge.CodexRpc()
    try:
        result = rpc.analyze("Trading discussion", "Frozen result and history", "fake", [], None,
                             timeout=2, response_format="text")
        assert result["text"] == "先依原報告的價格結構評估。"
    finally:
        rpc.close()
    assert rpc.process.poll() is not None


def test_cli_uses_file_only_for_all_sessions_and_app_login_round_trips_sqlite(tmp_path, monkeypatch):
    import os

    from trade_helper.credential_store import load_credentials

    executable = tmp_path / 'fake-owned-auth-codex'
    executable = runnable_script(executable, '''
import json
import os
import sys
from pathlib import Path
assert 'cli_auth_credentials_store="file"' in sys.argv
path=Path(os.environ['CODEX_HOME'])/'auth.json'
def send(value):print(json.dumps(value),flush=True)
for line in sys.stdin:
    m=json.loads(line)
    method=m.get('method')
    if method=='initialize':send({'id':m['id'],'result':{}})
    elif method=='account/login/start':
        path.write_text(json.dumps({'tokens':{'access_token':'test-owned-token','refresh_token':'test-refresh'},'auth_mode':'chatgpt'}))
        send({'id':m['id'],'result':{'loginId':'owned-login','authUrl':'https://auth.openai.com/test-only'}})
    elif method=='account/read':
        send({'id':m['id'],'result':{'account':{'type':'chatgpt','email':'test@example.test'} if path.exists() else None}})
    elif method=='test/refresh':
        payload=json.loads(path.read_text())
        payload['tokens']['access_token']='test-refreshed-token'
        path.write_text(json.dumps(payload))
        send({'id':m['id'],'result':{}})
    elif method=='account/logout':
        path.unlink(missing_ok=True)
        send({'id':m['id'],'result':{}})
''')
    monkeypatch.setattr(codex_bridge, 'codex_executable', lambda: str(executable))
    shared = tmp_path / 'external-home'
    shared.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(shared))
    owned = codex_bridge.CodexRpc(isolated=True)
    temporary = owned.local_auth.home
    try:
        owned.request('account/login/start', {'type': 'chatgpt'})
        assert load_credentials('codex')['auth']['tokens']['access_token'] == 'test-owned-token'
        if os.name == 'posix':
            assert owned.local_auth.path.stat().st_mode & 0o777 == 0o600
            assert temporary.stat().st_mode & 0o777 == 0o700
    finally:
        owned.close()
    assert not temporary.exists()
    restored = codex_bridge.CodexRpc(isolated=True)
    try:
        assert restored.request('account/read')['account']['type'] == 'chatgpt'
        restored.request('test/refresh')
        assert load_credentials('codex')['auth']['tokens']['access_token'] == 'test-refreshed-token'
        save_preferences({'codex_auth_scope': 'application'})
        monkeypatch.setattr(codex_bridge, '_auth_rpc', restored)
        monkeypatch.setattr(codex_bridge, '_auth_server', lambda: restored)
        codex_bridge.logout()
        assert load_credentials('codex') is None
    finally:
        restored.close()
    external = codex_bridge.CodexRpc(isolated=False)
    try:
        assert external.local_auth is None
        assert external.request('account/read')['account'] is None
        assert list(shared.iterdir()) == []
    finally:
        external.close()


def test_explicit_app_logout_clears_record_refreshed_by_another_session(monkeypatch):
    from trade_helper.codex_auth_storage import LocalCodexAuth
    from trade_helper.credential_store import load_credentials, save_credentials

    save_preferences({'codex_auth_scope': 'application'})
    save_credentials('codex', {'auth': {'tokens': {'access_token': 'test-original-token', 'refresh_token': 'test-refresh'}}})
    auth_session = LocalCodexAuth()
    save_credentials('codex', {'auth': {'tokens': {'access_token': 'test-newer-refresh-token', 'refresh_token': 'test-refresh-new'}}})
    def request(method):
        assert method == 'account/logout'
        auth_session.path.unlink()
        auth_session.persist()
        return {}
    rpc = SimpleNamespace(request=request, close=auth_session.close)
    monkeypatch.setattr(codex_bridge, '_auth_rpc', rpc)
    monkeypatch.setattr(codex_bridge, '_auth_server', lambda: rpc)
    result = codex_bridge.logout()
    assert result['authenticated'] is False
    assert load_credentials('codex') is None
    restored = LocalCodexAuth()
    try:
        assert not restored.path.exists()
    finally:
        restored.close()
