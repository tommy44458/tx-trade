"""SSE total-budget regressions with fake, blocking transports only."""

from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace as NS

import pytest

from trade_helper import model_providers

from .test_text_streaming_transport import Stream, message


class BlockingStream:
    def __init__(self, *, swallowed_heartbeats=False, block_close=False):
        self.swallowed_heartbeats = swallowed_heartbeats
        self.block_close = block_close
        self.blocked, self.release = Event(), Event()
        self.close_started, self.allow_close, self.closed = Event(), Event(), Event()
        self.reader_ended = Event()
        self.heartbeats = 0

    def __iter__(self):
        try:
            yield NS(type="response.output_item.added", item=NS(
                type="message", id="answer", phase="final_answer"))
            yield NS(type="response.output_text.delta", item_id="answer", delta="Early prefix")
            self.blocked.set()
            if self.swallowed_heartbeats:
                # HTTP/SSE comments keep the network alive but the SDK does not
                # yield model events, so a per-read socket timeout is inadequate.
                while not self.release.wait(0.005):
                    self.heartbeats += 1
            else:
                self.release.wait(2)
            yield NS(type="response.output_text.delta", item_id="answer", delta=" late data")
            yield NS(type="response.completed", response=NS(
                status="completed", output=[message("A late complete answer")]))
        finally:
            self.reader_ended.set()

    def close(self):
        self.close_started.set()
        if self.block_close:
            self.allow_close.wait(2)
        self.release.set()
        self.closed.set()

    def release_all(self):
        self.allow_close.set()
        self.release.set()


class BlockingClient:
    def __init__(self, stream, *, block_close=False):
        self.responses = NS(create=lambda **_kwargs: stream)
        self.block_close = block_close
        self.close_started, self.allow_close, self.closed = Event(), Event(), Event()

    def close(self):
        self.close_started.set()
        if self.block_close:
            self.allow_close.wait(2)
        self.closed.set()


@pytest.mark.parametrize("swallowed_heartbeats", [False, True])
@pytest.mark.parametrize("block_close", [False, True])
def test_blocking_sse_iterator_has_hard_deadline_and_no_late_callbacks(
        monkeypatch, swallowed_heartbeats, block_close):
    stream = BlockingStream(swallowed_heartbeats=swallowed_heartbeats, block_close=block_close)
    client = BlockingClient(stream, block_close=block_close)
    prefixes, readers = [], []
    original_reader = model_providers._BoundedResponseReader

    def reader(*args):
        result = original_reader(*args)
        readers.append(result)
        return result

    monkeypatch.setattr(model_providers, "_BoundedResponseReader", reader)
    session = model_providers.ModelSession.__new__(model_providers.ModelSession)
    session.provider, session.model = "openai", "fixture"
    session.client = client
    started = monotonic()
    try:
        with pytest.raises(TimeoutError):
            session.stream_text(instructions="Frozen", context="Saved", timeout=0.06,
                                on_text=prefixes.append)
        session.close()
        # Closing a stuck stream or client must not block this worker. The fake
        # network can otherwise block for two seconds.
        assert monotonic() - started < 0.3
        assert prefixes == ["Early prefix"]
        assert stream.blocked.is_set() and stream.close_started.wait(0.5)
        assert client.close_started.wait(0.5)
        assert readers[0].stopped.is_set()
        assert readers[0].events.maxsize == model_providers.STREAM_QUEUE_SIZE
        if swallowed_heartbeats:
            assert stream.heartbeats > 0
    finally:
        stream.release_all()
        client.allow_close.set()
        session.close()
    assert stream.reader_ended.wait(1) and stream.closed.wait(1) and client.closed.wait(1)
    readers[0].thread.join(timeout=1)
    assert not readers[0].thread.is_alive()
    assert prefixes == ["Early prefix"]


def test_bounded_event_queue_applies_backpressure_and_stops_without_callbacks():
    produced, filled = [], Event()

    def events():
        for index in range(10_000):
            produced.append(index)
            if len(produced) == model_providers.STREAM_QUEUE_SIZE + 1:
                filled.set()
            yield NS(type="response.keepalive")

    stream = Stream(events())
    reader = model_providers._BoundedResponseReader(stream, monotonic() + 2)
    try:
        assert filled.wait(1)
        sleep(0.02)
        assert reader.events.qsize() == model_providers.STREAM_QUEUE_SIZE
        # One in-flight item is the only allowance beyond the finite queue.
        assert len(produced) == model_providers.STREAM_QUEUE_SIZE + 1
    finally:
        reader.close()
    reader.thread.join(timeout=1)
    assert not reader.thread.is_alive() and stream.closed


def test_callback_time_counts_toward_deadline_and_no_final_callback_runs(monkeypatch):
    clock, prefixes = [0.0], []
    monkeypatch.setattr(model_providers, "monotonic", lambda: clock[0])
    stream = Stream([
        NS(type="response.output_text.delta", item_id="answer", delta="The first prefix"),
        NS(type="response.completed", response=NS(status="completed", output=[message("Final")]))])

    def callback(text):
        prefixes.append(text)
        clock[0] = 20.0  # Callback used the remaining ten-second budget.

    with pytest.raises(TimeoutError):
        model_providers._read_response_stream(stream, deadline=10, on_text=callback)
    assert prefixes == ["The first prefix"]


@pytest.mark.parametrize("expensive_iter", [False, True])
def test_expired_budget_never_reads_iterator_or_delivers_callback(expensive_iter):
    reads, prefixes = [], []

    def events():
        reads.append(True)
        yield NS(type="response.output_text.delta", item_id="answer", delta="Late")

    class ExpensiveIterator:
        def __iter__(self):
            # Some SDK wrappers do work in iter() itself, before next().
            reads.append("iter")
            return iter([])

        def close(self):
            pass

    with pytest.raises(TimeoutError):
        model_providers._read_response_stream(
            ExpensiveIterator() if expensive_iter else Stream(events()),
            deadline=monotonic() - 1, on_text=prefixes.append)
    assert reads == prefixes == []


def test_normal_completion_is_authoritative_with_stuck_cleanup():
    tail, close_started, close_release, closed = [], Event(), Event(), Event()

    class CompletedStream:
        def __iter__(self):
            yield NS(type="response.output_text.delta", item_id="answer", delta="Draft")
            yield NS(type="response.completed", response=NS(
                status="completed", output=[message("Final answer")]))
            tail.append(True)
            close_release.wait(2)

        def close(self):
            close_started.set()
            close_release.wait(2)
            closed.set()

    started = monotonic()
    prefixes = []
    try:
        result = model_providers._read_response_stream(
            CompletedStream(), deadline=started + 0.2, on_text=prefixes.append)
        assert monotonic() - started < 0.3
        assert model_providers._visible_response_text(result) == "Final answer"
        assert prefixes == ["Draft", "Final answer"] and tail == []
        assert close_started.wait(0.5)
    finally:
        close_release.set()
    assert closed.wait(1)
