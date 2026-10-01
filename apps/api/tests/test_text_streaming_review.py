"""Independent transport edge cases; no real model, credentials, or database."""

from types import SimpleNamespace as NS

import pytest

from trade_helper import codex_bridge, discussions, model_providers

from .test_text_streaming_transport import Stream, codex_event, fake_rpc, message


@pytest.mark.parametrize("after_completion", ["disconnect", "failed"])
def test_completed_response_is_authoritative_without_reading_a_failing_tail(after_completion):
    consumed_tail = []
    completed = NS(status="completed", output=[message("The final answer")], usage=None)

    def events():
        yield NS(type="response.output_item.added", item=NS(
            type="message", id="answer", phase="final_answer"))
        yield NS(type="response.output_text.delta", item_id="answer", delta="A draft")
        yield NS(type="response.completed", response=completed)
        consumed_tail.append(True)
        if after_completion == "disconnect":
            raise ConnectionError("private-provider-payload")
        yield NS(type="response.failed")

    prefixes = []
    result = model_providers._read_response_stream(
        Stream(events()), deadline=float("inf"), on_text=prefixes.append)
    assert result is completed
    assert prefixes == ["A draft", "The final answer"]
    assert consumed_tail == []


@pytest.mark.parametrize("empty", ["", "   \n"])
def test_invalid_empty_snapshot_cannot_clear_received_long_prefix(monkeypatch, empty):
    saved = []
    monkeypatch.setattr(discussions, "_persist_prefix", lambda _job, text:
                        (saved.append(text), True)[1])
    prefix = discussions._ReplyPrefix({})
    try:
        text = "A received visible answer. " * 20
        prefix(text)
        prefix(empty)
        assert prefix.close() == text
        assert saved == [text]
    finally:
        prefix.close()


def test_empty_completed_response_does_not_emit_a_prefix_erasing_snapshot():
    prefixes = []
    completed = NS(status="completed", output=[message("")])
    result = model_providers._read_response_stream(Stream([
        NS(type="response.output_item.added", item=NS(
            type="message", id="answer", phase="final_answer")),
        NS(type="response.output_text.delta", item_id="answer", delta="An incomplete answer"),
        NS(type="response.completed", response=completed),
    ]), deadline=float("inf"), on_text=prefixes.append)
    assert model_providers._visible_response_text(result) == ""
    assert prefixes == ["An incomplete answer"]


def test_codex_deferred_completion_cannot_bypass_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(codex_bridge, "monotonic", lambda: clock[0])
    rpc = fake_rpc(iter([]))
    rpc._timeout = lambda: codex_bridge.CodexTimeoutError("Safe model timeout")

    def request(method, *_args, **_kwargs):
        if method == "config/read":
            return {}
        if method == "thread/start":
            return {"thread": {"id": "thread"}}
        clock[0] = 5.0  # turn/start consumed the one-second overall budget.
        rpc.deferred.extend([
            codex_event("item/started", item={"type": "agentMessage", "id": "answer",
                                               "phase": "final_answer"}),
            codex_event("item/completed", item={"type": "agentMessage", "id": "answer",
                                                 "phase": "final_answer", "text": "Late answer"}),
            codex_event("turn/completed", turn={"id": "turn", "status": "completed"}),
        ])
        return {"turn": {"id": "turn"}}

    rpc.request = request
    with pytest.raises(codex_bridge.CodexTimeoutError):
        rpc.analyze("Discussion", "Context", "fixture", [], None,
                    timeout=1, response_format="text", on_text=lambda _text: None)


@pytest.mark.parametrize("timeout", [1, 5])
def test_codex_session_counts_rpc_startup_in_total_model_budget(monkeypatch, timeout):
    clock, calls, closed = [0.0], [], []
    monkeypatch.setattr(model_providers, "monotonic", lambda: clock[0])

    def factory(**_kwargs):
        clock[0] = 2.0

        def analyze(*_args, **kwargs):
            calls.append(kwargs)
            return {"text": "The answer", "usage": {}}

        return NS(analyze=analyze, close=lambda: closed.append(True))

    monkeypatch.setattr(model_providers, "CodexRpc", factory)
    session = model_providers.ModelSession.__new__(model_providers.ModelSession)
    session.isolated_codex = False
    session.provider, session.model = "codex", "fixture"
    if timeout == 1:
        with pytest.raises(TimeoutError):
            session.analyze_local_agent(instructions="Frozen instructions", context="Saved context",
                                        tools=[], tool_handler=None, timeout=timeout, response_format="text")
        assert calls == []
    else:
        response = session.analyze_local_agent(
            instructions="Frozen instructions", context="Saved context",
            tools=[], tool_handler=None, timeout=timeout, response_format="text")
        assert response.output_text == "The answer"
        assert calls[0]["timeout"] == pytest.approx(3.0)
    assert closed == [True]
