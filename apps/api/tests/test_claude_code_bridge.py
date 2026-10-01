import json
import subprocess
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from trade_helper import claude_code_bridge
from trade_helper.api import app
from trade_helper.local_settings import preferences

FAKE_CLI = textwrap.dedent("""
    import json, os, sys

    def send(value):
        print(json.dumps(value, ensure_ascii=False), flush=True)

    def receive():
        return json.loads(sys.stdin.readline())

    def answer(message):
        if message.get("type") != "control_response":
            raise SystemExit("expected a control response")
        return message["response"]

    arguments = sys.argv[1:]
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps({"argv": arguments, "cwd": os.getcwd(),
            "env": sorted(key for key in os.environ if key.startswith(("OPENAI_", "BINGX_")))}) + "\\n")
    mode = os.environ.get("FAKE_MODE", "ok")
    initialize = receive()
    assert initialize["request"]["subtype"] == "initialize"
    if "--mcp-config" in arguments:
        send({"type": "control_request", "request_id": "m1", "request": {"subtype": "mcp_message",
              "server_name": "indicators", "message": {"jsonrpc": "2.0", "id": 0,
              "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}}})
        assert answer(receive())["response"]["mcp_response"]["result"]["serverInfo"]["name"] == "indicators"
        send({"type": "control_request", "request_id": "m2", "request": {"subtype": "mcp_message",
              "server_name": "indicators", "message": {"jsonrpc": "2.0", "method": "notifications/initialized"}}})
        answer(receive())
        send({"type": "control_request", "request_id": "m3", "request": {"subtype": "mcp_message",
              "server_name": "indicators", "message": {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}}})
        tools = answer(receive())["response"]["mcp_response"]["result"]["tools"]
        with open(os.environ["FAKE_LOG"], "a") as log:
            log.write(json.dumps({"tools": tools}) + "\\n")
    send({"type": "control_response", "response": {"subtype": "success",
          "request_id": initialize["request_id"], "response": {}}})
    user = receive()
    with open(os.environ["FAKE_LOG"], "a") as log:
        log.write(json.dumps({"user": user["message"]["content"]}) + "\\n")
    if mode == "exit":
        raise SystemExit(1)
    send({"type": "stream_event", "event": {"type": "message_start"}})
    send({"type": "stream_event", "event": {"type": "content_block_delta",
          "delta": {"type": "text_delta", "text": "Checking"}}})
    if "--mcp-config" in arguments:
        for request_id, name in (("t1", "rsi"), ("t2", "shell")):
            send({"type": "control_request", "request_id": request_id, "request": {
                  "subtype": "mcp_message", "server_name": "indicators", "message": {
                  "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
                  "params": {"name": name, "arguments": {"period": 14}}}}})
            result = answer(receive())["response"]["mcp_response"]["result"]
            with open(os.environ["FAKE_LOG"], "a") as log:
                log.write(json.dumps({"tool_result": result}) + "\\n")
        send({"type": "stream_event", "event": {"type": "message_start"}})
    send({"type": "control_request", "request_id": "p1", "request": {
          "subtype": "can_use_tool", "tool_name": "Bash", "input": {}}})
    assert answer(receive())["response"]["behavior"] == "deny"
    for chunk in ("看", "支撐。"):
        send({"type": "stream_event", "event": {"type": "content_block_delta",
              "delta": {"type": "text_delta", "text": chunk}}})
    if mode == "error":
        send({"type": "result", "subtype": "error_during_execution", "is_error": True,
              "result": "private upstream failure"})
    else:
        send({"type": "result", "subtype": "success", "is_error": False, "result": "看支撐。",
              "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 3}})
    sys.stdin.read()
""")


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    executable = tmp_path / "claude"
    executable.write_text(f"#!{sys.executable}\n{FAKE_CLI}")
    executable.chmod(0o755)
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    monkeypatch.setenv("OPENAI_API_KEY", "app-secret")
    monkeypatch.setattr(claude_code_bridge, "claude_executable", lambda: str(executable))

    def entries():
        return [json.loads(line) for line in log.read_text().splitlines()]

    return entries


def test_analysis_exposes_only_registered_tools_and_returns_final_text(fake_claude):
    calls = []

    def handler(name, arguments):
        calls.append((name, arguments))
        return {"rsi": 55}

    tool = {"name": "rsi", "description": "RSI", "parameters": {"type": "object"}}
    session = claude_code_bridge.ClaudeCodeSession()
    try:
        result = session.analyze("Frozen instructions", "Frozen context", "sonnet", [tool], handler,
                                 timeout=10, effort="high")
    finally:
        session.close()
    log = fake_claude()
    argv = log[0]["argv"]
    assert argv[argv.index("--tools") + 1] == "" and "--strict-mcp-config" in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--setting-sources=" in argv and "--no-session-persistence" in argv
    assert argv[argv.index("--allowedTools") + 1] == "mcp__indicators__rsi"
    assert "--model=sonnet" in argv and argv[argv.index("--effort") + 1] == "high"
    assert log[0]["env"] == []  # app secrets are removed
    assert log[1]["tools"] == [{"name": "rsi", "description": "RSI", "inputSchema": {"type": "object"}}]
    assert log[2]["user"] == "Frozen context"
    assert calls == [("rsi", {"period": 14})]
    assert json.loads(log[3]["tool_result"]["content"][0]["text"]) == {"rsi": 55}
    assert log[4]["tool_result"]["isError"] is True  # unregistered tool
    assert result["text"] == "看支撐。"
    assert result["usage"] == {"inputTokens": 15, "outputTokens": 3}
    assert result["diagnostics"]["last_stage"] == "撰寫報告"


def test_text_mode_streams_prefixes_and_final_result_replaces_them(fake_claude):
    prefixes = []
    session = claude_code_bridge.ClaudeCodeSession()
    try:
        result = session.analyze("Discussion", "Frozen report", "", [], None, timeout=10,
                                 response_format="text", on_text=prefixes.append)
    finally:
        session.close()
    argv = fake_claude()[0]["argv"]
    assert "--mcp-config" not in argv and "--allowedTools" not in argv
    assert not any(item.startswith("--model") for item in argv)
    # The authoritative final result replaces the streamed prefix, never appends.
    assert prefixes == ["Checking", "Checking看", "Checking看支撐。", "看支撐。"]
    assert result["text"] == "看支撐。"


@pytest.mark.parametrize(("mode", "message"), [("error", "分析未完成"), ("exit", "已停止")])
def test_failures_use_safe_messages(fake_claude, monkeypatch, mode, message):
    monkeypatch.setenv("FAKE_MODE", mode)
    session = claude_code_bridge.ClaudeCodeSession()
    with pytest.raises(claude_code_bridge.ClaudeCodeError, match=message) as error:
        try:
            session.analyze("Discussion", "Frozen", "", [], None, timeout=10, response_format="text")
        finally:
            session.close()
    assert "private" not in str(error.value)


def test_text_mode_rejects_tools():
    session = claude_code_bridge.ClaudeCodeSession()
    try:
        with pytest.raises(ValueError):
            session.analyze("x", "y", "", [{"name": "rsi"}], None, timeout=1, response_format="text")
    finally:
        session.close()


def status_runner(payload, calls=None):
    def run(command, **_kwargs):
        if calls is not None:
            calls.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
    return run


def test_binding_uses_existing_cli_sign_in_and_unbinding_keeps_it(monkeypatch):
    calls = []
    monkeypatch.setattr(claude_code_bridge, "claude_executable", lambda: "/fake/claude")
    original = claude_code_bridge._cli_status
    monkeypatch.setattr(claude_code_bridge, "_cli_status", lambda: original(status_runner(
        {"loggedIn": True, "email": "me@example.com", "subscriptionType": "max",
         "orgId": "private-org"}, calls)))
    client = TestClient(app)
    assert client.get("/api/v1/auth/claude_code/status").json()["status_known"] is False
    bound = client.post("/api/v1/auth/claude_code/login").json()
    assert bound["auth_url"] is None and bound["status"]["authenticated"] is True
    assert bound["status"]["email"] == "me@example.com" and bound["status"]["plan"] == "max"
    assert calls == [["/fake/claude", "auth", "status", "--json"]]
    cached = client.get("/api/v1/auth/claude_code/status").json()
    assert cached["authenticated"] is True and "orgId" not in json.dumps(cached)
    models = client.post("/api/v1/auth/claude_code/models").json()["data"]
    assert [item["model"] for item in models] == ["opus", "sonnet", "haiku"]
    unbound = client.post("/api/v1/auth/claude_code/logout").json()
    assert unbound["authenticated"] is False and preferences()["claude_code_disconnected"] is True
    assert all("logout" not in command for command in calls)
    with pytest.raises(claude_code_bridge.ClaudeCodeError):
        claude_code_bridge.require_authorized()


def test_signed_out_cli_explains_how_to_sign_in(monkeypatch):
    monkeypatch.setattr(claude_code_bridge, "claude_executable", lambda: "/fake/claude")
    original = claude_code_bridge._cli_status
    monkeypatch.setattr(claude_code_bridge, "_cli_status",
                        lambda: original(status_runner({"loggedIn": False})))
    status = TestClient(app).post("/api/v1/auth/claude_code/check").json()
    assert status["authenticated"] is False and "claude auth login" in status["error"]
    assert TestClient(app).post("/api/v1/auth/claude_code/models").status_code == 503


def test_missing_cli_is_reported_without_crashing(monkeypatch):
    def missing():
        raise claude_code_bridge.ClaudeCodeError("找不到 Claude Code。請先安裝 Claude Code CLI。")

    monkeypatch.setattr(claude_code_bridge, "claude_executable", missing)
    status = TestClient(app).post("/api/v1/auth/claude_code/check").json()
    assert status["available"] is False and "找不到 Claude Code" in status["error"]


@pytest.mark.parametrize("provider", ["codex", "claude_code"])
def test_status_reports_missing_cli_without_starting_it(monkeypatch, tmp_path, provider):
    from trade_helper import cli_paths, codex_bridge

    monkeypatch.setattr(cli_paths.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli_paths.shutil, "which", lambda _name: None)
    monkeypatch.setattr(cli_paths, "_common_directories", lambda: [tmp_path / "bin"])
    monkeypatch.setattr(codex_bridge, "_find_codex", lambda: cli_paths.find_executable("codex"))
    monkeypatch.setattr(claude_code_bridge, "_find_claude", lambda: cli_paths.find_executable("claude"))
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("CLI started"))
    client = TestClient(app)
    assert client.get(f"/api/v1/auth/{provider}/status").json()["cli_installed"] is False
    login = client.post(f"/api/v1/auth/{provider}/login").json()
    assert login["auth_url"] is None and login["status"]["cli_installed"] is False
    executable = tmp_path / "bin" / ("codex" if provider == "codex" else "claude")
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    assert client.get(f"/api/v1/auth/{provider}/status").json()["cli_installed"] is True


def test_finder_launched_app_finds_cli_outside_path(monkeypatch, tmp_path):
    from trade_helper import cli_paths

    monkeypatch.setattr(cli_paths.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(cli_paths.shutil, "which", lambda _name: None)
    executable = tmp_path / ".nvm/versions/node/v24.1.0/bin/codex"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)
    assert cli_paths.find_executable("codex") == str(executable)
