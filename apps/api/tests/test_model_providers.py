import json

import pytest

from trade_helper import model_providers
from trade_helper.agent import analyze_with_tools
from trade_helper.local_settings import save_preferences
from trade_helper.technical_snapshot import ADDITIONAL_INDICATORS, prepare_analysis_evidence

from .test_technical_snapshot import final_response, fixture


class FakeStream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __iter__(self):
        return iter(self.events)

    def close(self):
        self.closed = True


def test_claude_code_agent_uses_same_evidence_and_optional_python_tool(monkeypatch):
    request, candles, context, quote = fixture()
    trace = prepare_analysis_evidence(request, candles, quote, context)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("APP_CLAUDE_CODE_EFFORT", raising=False)
    save_preferences({"model_provider": "claude_code", "models": {"claude_code": "sonnet"}})
    monkeypatch.setattr(model_providers.claude_code_bridge, "require_authorized", lambda: None)
    calls = []

    class FakeSession:
        def analyze(self, instructions, supplied, model, tools, handler, *, timeout, effort):
            payload = json.loads(supplied)
            assert payload["current_candle"]["quote_price"] == quote["price"]
            assert model == "sonnet" and timeout <= 240 and effort == "medium"
            assert {item["name"] for item in tools} == set(ADDITIONAL_INDICATORS)
            calls.append(handler("rsi", {"period": 21, "timeframe": "4h", "reason": "檢查動能"}))
            return {"text": final_response(trace), "usage": {"inputTokens": 900, "outputTokens": 150}}

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(model_providers.claude_code_bridge, "ClaudeCodeSession", FakeSession)
    monkeypatch.setattr(model_providers, "CodexRpc", lambda **_: pytest.fail("wrong provider"))
    decision = analyze_with_tools(request, candles, quote, context, prepared_trace=trace)
    assert calls[0]["timeframe"] == "4h" and calls[-1] == "closed"
    assert decision["analysis_execution"]["provider"] == "claude_code"
    assert decision["analysis_execution"]["model"] == "sonnet"
    assert decision["analysis_execution"]["additional_tool_calls"] == 1
    assert decision["analysis_execution"]["input_tokens"] == 900


def test_claude_code_without_model_uses_cli_default_and_does_not_bypass_sign_in(monkeypatch):
    from trade_helper.claude_code_bridge import ClaudeCodeError

    save_preferences({"model_provider": "claude_code"})
    monkeypatch.setattr(model_providers.claude_code_bridge, "require_authorized", lambda: None)
    session = model_providers.ModelSession(openai_factory=lambda **_: pytest.fail("API fallback"))
    assert session.provider == "claude_code" and session.model == "" and session.uses_local_agent

    def unauthorized():
        raise ClaudeCodeError("請先登入")

    monkeypatch.setattr(model_providers.claude_code_bridge, "require_authorized", unauthorized)
    with pytest.raises(ClaudeCodeError, match="登入"):
        model_providers.ModelSession(openai_factory=lambda **_: pytest.fail("API fallback"))


@pytest.mark.parametrize(("codex", "explicit", "expected"), [
    ("medium", None, "medium"), ("minimal", None, "low"), ("ultra", None, "max"),
    ("medium", "xhigh", "xhigh"),
])
def test_claude_code_effort_follows_codex_setting_unless_overridden(monkeypatch, codex, explicit, expected):
    monkeypatch.setenv("APP_CODEX_REASONING_EFFORT", codex)
    if explicit:
        monkeypatch.setenv("APP_CLAUDE_CODE_EFFORT", explicit)
    else:
        monkeypatch.delenv("APP_CLAUDE_CODE_EFFORT", raising=False)
    assert model_providers.claude_code_effort() == expected
    monkeypatch.setenv("APP_CLAUDE_CODE_EFFORT", "ultra")
    with pytest.raises(model_providers.ModelProviderError):
        model_providers.claude_code_effort()


def test_codex_agent_uses_same_evidence_and_optional_python_tool_without_api_key(monkeypatch):
    request, candles, context, quote = fixture()
    trace = prepare_analysis_evidence(request, candles, quote, context)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    save_preferences({"model_provider": "codex", "models": {"codex": "account-model"}})
    monkeypatch.setattr(model_providers, "require_authorized", lambda: False)
    calls = []

    class FakeRpc:
        def __init__(self, **kwargs):
            assert kwargs == {"isolated": False}

        def analyze(self, instructions, supplied, model, tools, handler, *, timeout, effort):
            payload = json.loads(supplied)
            assert payload["current_candle"]["quote_price"] == quote["price"]
            assert payload["precomputed_evidence"]["technical_snapshot"]["timeframes"]["1h"]
            assert model == "account-model" and timeout <= 240
            assert effort == "medium"
            assert {item["name"] for item in tools} == set(ADDITIONAL_INDICATORS)
            calls.append(handler("rsi", {"period": 21, "timeframe": "4h", "reason": "檢查動能"}))
            return {"text": final_response(trace), "usage": {"inputTokens": 1000, "outputTokens": 200}}

        def close(self):
            calls.append("closed")

    monkeypatch.setattr(model_providers, "CodexRpc", FakeRpc)
    decision = analyze_with_tools(request, candles, quote, context, prepared_trace=trace)
    assert calls[0]["timeframe"] == "4h" and calls[-1] == "closed"
    assert decision["analysis_execution"]["provider"] == "codex"
    assert decision["analysis_execution"]["additional_tool_calls"] == 1
    assert decision["analysis_execution"]["input_tokens"] == 1000
    assert decision["tool_trace"][-1]["tool"] == "rsi"


def test_unapproved_tool_names_remain_unapproved():
    assert model_providers.tool_name("indicators.rsi") == "rsi"
    assert model_providers.tool_name("shell.exec") == "shell.exec"


@pytest.mark.parametrize("value", ["0", "29", "901", "invalid"])
def test_analysis_timeout_rejects_invalid_or_unbounded_values(monkeypatch, value):
    monkeypatch.setenv("APP_ANALYSIS_TIMEOUT_SECONDS", value)
    with pytest.raises(model_providers.ModelProviderError, match="30 與 900"):
        model_providers.analysis_timeout_seconds()


def test_analysis_budget_and_codex_effort_are_configurable(monkeypatch):
    monkeypatch.delenv("APP_ANALYSIS_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("APP_CODEX_REASONING_EFFORT", raising=False)
    assert model_providers.analysis_timeout_seconds() == 240
    assert model_providers.codex_reasoning_effort() == "medium"
    monkeypatch.setenv("APP_ANALYSIS_TIMEOUT_SECONDS", "480")
    monkeypatch.setenv("APP_CODEX_REASONING_EFFORT", "high")
    assert model_providers.analysis_timeout_seconds() == 480
    assert model_providers.codex_reasoning_effort() == "high"


def test_codex_plain_text_uses_authorized_session_and_selects_conversation_mode(monkeypatch):
    save_preferences({"model_provider": "codex", "models": {"codex": "authorized-model"}})
    calls = []
    monkeypatch.setattr(model_providers, "require_authorized", lambda: (calls.append("authorize"), True)[1])

    class FakeRpc:
        def __init__(self, *, isolated):
            assert isolated

        def analyze(self, instructions, context, model, tools, handler, **options):
            assert options["response_format"] == "text" and 0 < options["timeout"] <= 240
            assert tools == [] and handler is None and model == "authorized-model"
            calls.append((instructions, context))
            return {"text": "繁體中文對話"}

        def close(self):
            calls.append("close")

    monkeypatch.setattr(model_providers, "CodexRpc", FakeRpc)
    session = model_providers.ModelSession(openai_factory=lambda **_: pytest.fail("API key fallback"))
    response = session.analyze_local_agent(instructions="professional discussion", context="frozen report",
                                   tools=[], tool_handler=None, timeout=240, response_format="text")
    assert response.output_text == "繁體中文對話"
    assert calls == ["authorize", ("professional discussion", "frozen report"), "close"]


def test_codex_plain_text_does_not_bypass_authorization(monkeypatch):
    from trade_helper.codex_bridge import CodexError

    save_preferences({"model_provider": "codex", "models": {"codex": "authorized-model"}})

    def unauthorized():
        raise CodexError("請重新連接")

    monkeypatch.setattr(model_providers, "require_authorized", unauthorized)
    monkeypatch.setattr(model_providers, "CodexRpc", lambda **_: pytest.fail("unauthorized generation"))
    with pytest.raises(CodexError, match="重新連接"):
        model_providers.ModelSession(openai_factory=lambda **_: pytest.fail("API fallback"))
