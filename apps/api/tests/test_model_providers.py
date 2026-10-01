import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AuthenticationError

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


def test_chatgpt_plan_uses_streaming_full_context_namespace_and_no_api_key(monkeypatch):
    calls = []
    completion = SimpleNamespace(output=[], output_text='{"strategy":"分析"}')
    stream = FakeStream([SimpleNamespace(type="response.completed", response=completion)])
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(model_providers.chatgpt_auth, "get_access_token", lambda **_: "oauth-token")

    def client_factory(**options):
        assert options["api_key"] == "oauth-token"
        assert options["base_url"] == "https://api.openai.com/v1"
        return SimpleNamespace(responses=SimpleNamespace(create=lambda **values: (calls.append(values), stream)[1]), close=lambda: None)

    transport = model_providers.ChatGPTResponses(client_factory)
    context = [{"role": "user", "content": "market context"}]
    tool = {"type": "function", "name": "rsi", "description": "RSI", "parameters": {"type": "object"}}
    result = transport.create(model="account-model", input=context, instructions="Trading analysis",
                              tools=[tool], tool_choice="auto", parallel_tool_calls=False)
    assert result is completion
    assert stream.closed
    assert calls[0]["stream"] is True and calls[0]["store"] is False
    assert calls[0]["input"] is context
    assert calls[0]["tools"] == [{"type": "namespace", "name": "indicators",
        "description": "Optional Python market indicator calculations", "tools": [tool]}]
    assert "previous_response_id" not in calls[0] and "max_output_tokens" not in calls[0]


def test_chatgpt_auth_failure_refreshes_once_and_closes_clients(monkeypatch):
    tokens, closed = [], []
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(401, request=request)
    monkeypatch.setattr(model_providers.chatgpt_auth, "get_access_token",
                        lambda **kwargs: (tokens.append(kwargs["force_refresh"]), "token")[1])

    def client_factory(**_):
        def unauthorized(**__):
            raise AuthenticationError("private upstream error", response=response, body={})
        return SimpleNamespace(responses=SimpleNamespace(create=unauthorized), close=lambda: closed.append(1))

    with pytest.raises(model_providers.ModelProviderError, match="重新登入") as error:
        model_providers.ChatGPTResponses(client_factory).create(input=[], model="model", tools=[])
    assert "private" not in str(error.value)
    assert tokens == [False, True] and len(closed) == 2


def test_chatgpt_agent_uses_remaining_analysis_budget_instead_of_legacy_api_cap(monkeypatch):
    request, candles, context, quote = fixture()
    trace = prepare_analysis_evidence(request, candles, quote, context)
    save_preferences({"model_provider": "chatgpt", "models": {"chatgpt": "account-model"}})
    monkeypatch.setenv("APP_ANALYSIS_TIMEOUT_SECONDS", "240")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(model_providers.chatgpt_auth, "get_access_token", lambda **_: "oauth-token")
    timeouts = []

    def client_factory(**options):
        timeouts.append(options["timeout"])

        def create(**values):
            timeouts.append(values["timeout"])
            completion = SimpleNamespace(output=[], output_text=final_response(trace))
            return FakeStream([SimpleNamespace(type="response.completed", response=completion)])

        return SimpleNamespace(responses=SimpleNamespace(create=create), close=lambda: None)

    monkeypatch.setattr("trade_helper.agent.OpenAI", client_factory)
    decision = analyze_with_tools(request, candles, quote, context, prepared_trace=trace)
    assert decision["analysis_execution"]["provider"] == "chatgpt"
    # Both the SDK transport and SSE request receive the shared remaining budget.
    # A larger account-funded report must not be cut off at the old 90-second cap.
    assert len(timeouts) == 2 and all(90 < value <= 240 for value in timeouts)


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
    response = session.analyze_codex(instructions="professional discussion", context="frozen report",
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
