import threading
from types import SimpleNamespace as NS

import httpx
import pytest
from openai import AuthenticationError

from trade_helper import codex_bridge, model_providers


class Stream:
    def __init__(self, events):
        self.events = events
        self.closed = False

    def __iter__(self):
        return iter(self.events)

    def close(self):
        self.closed = True


def message(text, phase="final_answer"):
    return NS(type="message", phase=phase, content=[NS(type="output_text", text=text)])


def text_events(completed):
    yield NS(type="response.output_item.added", item=NS(type="message", id="progress",
                                                        phase="commentary"))
    yield NS(type="response.output_text.delta", item_id="progress", output_index=0,
             content_index=0, delta="private progress")
    yield NS(type="response.reasoning_text.delta", delta="hidden reasoning")
    yield NS(type="response.output_item.added", item=NS(type="message", id="answer",
                                                        phase="final_answer"))
    for chunk in ("先看", "支撐", "。"):
        yield NS(type="response.output_text.delta", item_id="answer", output_index=1,
                 content_index=0, delta=chunk)
    yield NS(type="response.output_text.done", item_id="answer", output_index=1,
             content_index=0, text="先看支撐。")
    yield NS(type="response.completed", response=completed)


@pytest.mark.parametrize("provider", ["openai", "chatgpt"])
def test_authorized_transport_emits_real_unicode_prefix_before_completion(monkeypatch, provider):
    completed = NS(status="completed", output=[message("private progress", "commentary"),
                                              message("先看支撐。")], usage=None)
    prefixes, calls, closed = [], [], []
    stream = Stream(text_events(completed))
    monkeypatch.setattr(model_providers.chatgpt_auth, "get_access_token", lambda **_: "oauth")

    def create(**options):
        calls.append(options)
        return stream

    factory = lambda **_: NS(responses=NS(create=create), close=lambda: closed.append(True))
    session = model_providers.ModelSession.__new__(model_providers.ModelSession)
    session.provider, session.model = provider, "chosen-model"
    session.client = (factory() if provider == "openai" else
                      NS(responses=model_providers.ChatGPTResponses(factory)))
    try:
        result = session.stream_text(instructions="Frozen discussion", context="Input",
                                     timeout=120, on_text=prefixes.append)
        assert prefixes == ["先看", "先看支撐", "先看支撐。"]
        assert result.output_text == "先看支撐。"
        assert result.status == "completed"
        assert calls[0]["stream"] and calls[0]["store"] is False
        assert calls[0]["model"] == "chosen-model"
        assert "on_text" not in calls[0] and "tools" not in calls[0]
    finally:
        session.close()
    assert stream.closed and closed == [True]


def test_final_snapshot_replaces_streamed_prefix_and_is_not_appended():
    stream = Stream([
        NS(type="response.output_text.delta", delta="draft", item_id="a"),
        NS(type="response.completed", response=NS(status="completed", output=[],
                                                   output_text="final revised answer")),
    ])
    prefixes = []
    response = model_providers._read_response_stream(stream, deadline=float("inf"),
                                                     on_text=prefixes.append)
    assert prefixes == ["draft", "final revised answer"]
    assert response.output_text == "final revised answer"


@pytest.mark.parametrize("terminal", ["response.failed", "response.incomplete", "error", None])
def test_incomplete_response_keeps_partial_but_never_claims_completion(terminal):
    events = [NS(type="response.output_text.delta", delta="partial", item_id="a")]
    if terminal:
        events.append(NS(type=terminal))
    prefixes = []
    with pytest.raises(model_providers.ModelProviderError):
        model_providers._read_response_stream(Stream(events), deadline=float("inf"),
                                              on_text=prefixes.append)
    assert prefixes == ["partial"]


def test_chatgpt_does_not_restart_generation_after_visible_prefix(monkeypatch):
    response = httpx.Response(401, request=httpx.Request("POST", "https://api.openai.com"))
    prefixes, tokens, clients = [], [], []

    def events():
        yield NS(type="response.output_text.delta", item_id="a", delta="partial")
        raise AuthenticationError("sensitive upstream", response=response, body={})

    stream = Stream(events())
    monkeypatch.setattr(model_providers.chatgpt_auth, "get_access_token",
                        lambda **kw: (tokens.append(kw["force_refresh"]), "token")[1])

    def factory(**_):
        clients.append(True)
        return NS(responses=NS(create=lambda **_: stream), close=lambda: None)

    with pytest.raises(model_providers.ModelProviderError, match="重新登入"):
        model_providers.ChatGPTResponses(factory).create(timeout=120, on_text=prefixes.append)
    assert prefixes == ["partial"] and tokens == [False] and clients == [True]
    assert stream.closed


def test_openai_closes_stream_if_callback_rejects_stale_worker():
    stream = Stream([NS(type="response.output_text.delta", item_id="a", delta="partial")])
    session = model_providers.ModelSession.__new__(model_providers.ModelSession)
    session.provider, session.model = "openai", "model"
    session.client = NS(responses=NS(create=lambda **_: stream))

    def stale(_):
        raise RuntimeError("claim lost")

    with pytest.raises(RuntimeError, match="claim lost"):
        session.stream_text(instructions="test", context="test", timeout=30, on_text=stale)
    assert stream.closed


def test_codex_connection_startup_cannot_restart_the_shared_deadline(monkeypatch):
    calls = []

    class SlowRpc:
        def __init__(self, **_):
            calls.append("connect")

        def analyze(self, *_args, **_kwargs):
            pytest.fail("Expired setup cannot start generation")

        def close(self):
            calls.append("close")

    clock = iter([0, 11])
    monkeypatch.setattr(model_providers, "monotonic", lambda: next(clock))
    monkeypatch.setattr(model_providers, "CodexRpc", SlowRpc)
    session = model_providers.ModelSession.__new__(model_providers.ModelSession)
    session.provider, session.model, session.isolated_codex = "codex", "selected-model", False
    with pytest.raises(TimeoutError, match="連接逾時"):
        session.stream_text(instructions="test", context="test", timeout=10)
    assert calls == ["connect", "close"]


def codex_event(method, **params):
    return {"method": method, "params": {"threadId": "thread", "turnId": "turn", **params}}


def fake_rpc(events):
    rpc = codex_bridge.CodexRpc.__new__(codex_bridge.CodexRpc)
    rpc.lock, rpc.workspace, rpc.deferred = threading.RLock(), NS(name="/tmp/test"), []
    responses = {"config/read": {}, "thread/start": {"thread": {"id": "thread"}},
                 "turn/start": {"turn": {"id": "turn"}}}
    rpc.request = lambda method, *_args, **_kwargs: responses[method]
    rpc._next = lambda _deadline: next(events)
    rpc._persist_auth = lambda: None
    rpc.diagnostics = dict
    return rpc


@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
def test_codex_only_streams_final_answer_and_waits_for_successful_turn(status):
    prefixes, source = [], []

    def events():
        yield codex_event("item/started", item={"id": "progress", "type": "agentMessage",
                                               "phase": "commentary"})
        yield codex_event("item/agentMessage/delta", itemId="progress", delta="private progress")
        yield codex_event("item/completed", item={"id": "progress", "type": "agentMessage",
                                                 "phase": "commentary", "text": "private progress"})
        yield codex_event("item/reasoning/textDelta", delta="hidden thoughts")
        yield codex_event("item/started", item={"id": "answer", "type": "agentMessage",
                                               "phase": "final_answer"})
        yield codex_event("item/agentMessage/delta", itemId="answer", delta="錯誤上下文",
                          turnId="another-turn")
        yield codex_event("item/agentMessage/delta", itemId="answer", delta="看")
        assert prefixes == ["看"]  # Received while the provider is still generating.
        yield codex_event("item/agentMessage/delta", itemId="answer", delta="支撐。")
        assert prefixes == ["看", "看支撐。"]
        yield codex_event("item/completed", item={"id": "answer", "type": "agentMessage",
                                                 "phase": "final_answer", "text": "看支撐。"})
        source.append("terminal")
        yield codex_event("turn/completed", turn={"id": "turn", "status": status})

    rpc = fake_rpc(events())
    if status == "completed":
        assert rpc.analyze("Discussion", "Frozen context", "model", [], None, timeout=5,
                           response_format="text", on_text=prefixes.append)["text"] == "看支撐。"
    else:
        with pytest.raises(codex_bridge.CodexError, match="未完成"):
            rpc.analyze("Discussion", "Frozen context", "model", [], None, timeout=5,
                        response_format="text", on_text=prefixes.append)
    assert source == ["terminal"] and prefixes == ["看", "看支撐。"]


def test_codex_commentary_alone_cannot_be_a_successful_answer():
    rpc = fake_rpc(iter([
        codex_event("item/started", item={"id": "progress", "type": "agentMessage",
                                         "phase": "commentary"}),
        codex_event("item/agentMessage/delta", itemId="progress", delta="Still thinking"),
        codex_event("turn/completed", turn={"id": "turn", "status": "completed"}),
    ]))
    with pytest.raises(codex_bridge.CodexError, match="沒有回傳"):
        rpc.analyze("Discussion", "Context", "model", [], None, timeout=5, response_format="text")
