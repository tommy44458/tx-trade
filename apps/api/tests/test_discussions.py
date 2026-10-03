import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from trade_helper import discussions, worker
from trade_helper.api import app
from trade_helper.config import local_user_id
from trade_helper.db import SCHEMA_VERSION, connect, init_db, utc_now


def analysis(identifier="analysis-one", *, owner=None, status="completed", kind="market"):
    now = utc_now()
    request = {"kind": kind, "market_id": "binance:perp:BTCUSDT", "timeframe": "1h",
               "risk_tolerance": "high", "directional_bias": "bullish", "leverage": 5}
    report = {"as_of": now, "headline": "測試報告", "strategy": "依支撐反應評估",
              "metrics": {"current_price": "50000", "levels": [{"price": "48000"}]},
              "reasoning": {"market": "原有分析"}, "tool_trace": []}
    with connect() as db:
        db.execute(
            """INSERT INTO analyses(id,user_id,idempotency_key,request_hash,request_json,
               positions_json,status,phase,report_json,snapshot_json,created_at,completed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (identifier, owner or local_user_id(), identifier, "hash", json.dumps(request),
             json.dumps([{"id": "saved-position", "entry_price": "51000", "version": 1}])
             if kind == "positions" else "[]", status, "done", json.dumps(report),
             json.dumps({"quote": {"price": "50000", "observed_at": now},
                         "candles": [], "positions": []}), now, now),
        )
    return identifier


def macro(identifier="macro-one", *, owner=None, status="succeeded"):
    now = utc_now()
    with connect() as db:
        db.execute(
            """INSERT INTO macro_interpretations(id,user_id,evidence_version,prompt_version,
               fingerprint,evidence_json,status,result_json,created_at,updated_at,generated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (identifier, owner or local_user_id(), "evidence-v1", "prompt-v1", identifier,
             json.dumps({"as_of": now, "evidence_count": 1,
                         "evidence": [{"metric": "cpi", "value": "3.0"}], "coverage": {}}),
             status, json.dumps({"outlook": {"stance": "neutral", "summary": "舊版CPI背景"},
                                 "drivers": [], "uncertainties": [], "watch_next": []}),
             now, now, now),
        )
    return identifier


@pytest.fixture
def client():
    with TestClient(app) as value:
        yield value


def send(client, identifier="analysis-one", message="這次支撐為什麼重要？", *, request_id=None,
         subject_type="analysis"):
    return client.post(f"/api/v1/discussions/{subject_type}/{identifier}/messages",
                       json={"message": message, "request_id": request_id or str(uuid4())})


def fake_reply(monkeypatch, text="先觀察原報告支撐的反應，再衡量這次持倉風險。"):
    calls = []

    def generate(context, messages, *, timeout, instructions=None, on_text=None):
        # Prove a second writer is available throughout the expensive call.
        with connect() as db:
            db.execute("SELECT count(*) FROM discussion_messages")
        calls.append((context, messages, timeout))
        return text, "fake", "fixture-model"

    monkeypatch.setattr(discussions, "generate_reply", generate)
    return calls


def test_get_and_submit_never_initialize_model_or_credentials(client, monkeypatch):
    analysis()
    monkeypatch.setattr(discussions, "ModelSession", lambda **_: pytest.fail("unexpected model auth"))
    monkeypatch.setattr("trade_helper.credential_store.load_credentials", lambda *_: pytest.fail("vault"))
    initial = client.get("/api/v1/discussions/analysis/analysis-one")
    assert initial.status_code == 200
    assert initial.json()["session"] is None and initial.json()["messages"] == []
    assert not initial.json()["busy"]
    response = send(client)
    assert response.status_code == 202
    state = response.json()
    assert state["busy"] and len(state["messages"]) == 2
    assert [row["status"] for row in state["messages"]] == ["completed", "queued"]
    assert state["messages"][0]["request_id"] == state["messages"][1]["request_id"]
    assert client.get("/api/v1/discussions/analysis/analysis-one").json()["busy"]


@pytest.mark.parametrize("subject_type", ["analysis", "macro"])
def test_subject_ownership_readiness_and_missing_targets(client, subject_type):
    factory = analysis if subject_type == "analysis" else macro
    factory("owned-ready")
    factory("other-owner", owner="different-owner")
    factory("not-ready", status="running")
    prefix = f"/api/v1/discussions/{subject_type}"
    for identifier, expected in (("owned-ready", 200), ("other-owner", 404),
                                 ("missing", 404), ("not-ready", 409)):
        assert client.get(f"{prefix}/{identifier}").status_code == expected
        posted = send(client, identifier, subject_type=subject_type)
        assert posted.status_code == (202 if expected == 200 else expected)


@pytest.mark.parametrize("body", [
    {"message": "   ", "request_id": str(uuid4())},
    {"message": "x" * 6001, "request_id": str(uuid4())},
    {"message": "test", "request_id": "not-a-uuid"},
    {"message": "test", "request_id": str(uuid4()), "force": True},
])
def test_invalid_submission_is_rejected_without_queueing(client, body):
    analysis()
    assert client.post("/api/v1/discussions/analysis/analysis-one/messages", json=body).status_code == 422
    assert client.get("/api/v1/discussions/analysis/analysis-one").json()["session"] is None


def test_idempotency_busy_retry_guards_and_completion(client, monkeypatch):
    analysis()
    calls = fake_reply(monkeypatch)
    request_id = str(uuid4())
    first = send(client, request_id=request_id).json()
    duplicate = send(client, request_id=request_id).json()
    assert first == duplicate
    assert send(client, message="不同問題", request_id=request_id).status_code == 409
    assert send(client, message="另一個問題").status_code == 409
    assert discussions.run_once()
    assert not discussions.run_once()
    state = client.get("/api/v1/discussions/analysis/analysis-one").json()
    assert not state["busy"] and state["messages"][-1]["status"] == "completed"
    assert state["messages"][-1]["provider"] == "fake"
    assert state["messages"][-1]["model"] == "fixture-model"
    assert len(calls) == 1 and 0 < calls[0][2] <= 240
    assert [message["role"] for message in calls[0][1]] == ["user"]
    assistant_id = state["messages"][-1]["id"]
    assert client.post(f"/api/v1/discussions/analysis/analysis-one/messages/{assistant_id}/retry").status_code == 409
    assert client.post(f"/api/v1/discussions/analysis/analysis-one/messages/{state['messages'][0]['id']}/retry").status_code == 404
    assert send(client, request_id=request_id).json()["messages"] == state["messages"]


def test_complete_history_and_frozen_context_survive_restart_and_new_version(client, monkeypatch):
    analysis(kind="positions")
    analysis("analysis-two")
    calls = fake_reply(monkeypatch)
    send(client, message="持倉的第一個問題")
    assert discussions.run_once()
    with connect() as db:
        frozen = db.execute("SELECT context_json FROM discussion_sessions").fetchone()["context_json"]
        db.execute("UPDATE analyses SET report_json=?,snapshot_json=? WHERE id='analysis-one'",
                   (json.dumps({"headline": "different-price-99999"}), json.dumps({"quote": {"price": "99999"}})))
    # Reinitialize the schema like a clean process startup, then continue.
    init_db()
    send(client, message="第二個問題")
    assert discussions.run_once()
    assert [row["role"] for row in calls[-1][1]] == ["user", "assistant", "user"]
    assert "99999" not in json.dumps(calls[-1][0])
    with connect(readonly=True) as db:
        assert db.execute("SELECT context_json FROM discussion_sessions").fetchone()["context_json"] == frozen
    assert client.get("/api/v1/discussions/analysis/analysis-two").json()["messages"] == []
    send(client, "analysis-two", message="新版問題")
    assert discussions.run_once()
    assert len(calls[-1][1]) == 1 and calls[-1][1][0]["content"] == "新版問題"


def test_macro_old_interpretation_keeps_its_evidence_with_a_new_version(client, monkeypatch):
    macro()
    calls = fake_reply(monkeypatch)
    send(client, "macro-one", "舊版CPI對市場的影響？", subject_type="macro")
    assert discussions.run_once()
    macro("macro-new-version")
    send(client, "macro-one", "請继续解釋舊版背景", subject_type="macro")
    assert discussions.run_once()
    assert "3.0" in json.dumps(calls[-1][0]) and "macro-new-version" not in json.dumps(calls[-1][0])
    assert client.get("/api/v1/discussions/macro/macro-new-version").json()["session"] is None


def test_safe_failure_is_not_auto_retried_and_explicit_retry_uses_same_turn(client, monkeypatch):
    analysis()
    failed_calls = []

    def fail(*_, **__):
        failed_calls.append(1)
        raise RuntimeError("secret-provider-payload-token")

    monkeypatch.setattr(discussions, "generate_reply", fail)
    original = send(client).json()
    assert discussions.run_once()
    for _ in range(3):
        state = client.get("/api/v1/discussions/analysis/analysis-one").json()
        assert not discussions.run_once()
    assert len(failed_calls) == 1 and "secret" not in json.dumps(state)
    assert state["messages"][-1]["error"]["retryable"]
    message_id = original["messages"][-1]["id"]
    path = f"/api/v1/discussions/analysis/analysis-one/messages/{message_id}/retry"
    first, second = client.post(path), client.post(path)
    assert first.status_code == second.status_code == 202
    assert first.json()["messages"] == second.json()["messages"]
    fake_reply(monkeypatch)
    assert discussions.run_once()
    result = client.get("/api/v1/discussions/analysis/analysis-one").json()
    assert result["messages"][-1]["id"] == message_id
    assert len(result["messages"]) == 2
    with connect(readonly=True) as db:
        assert db.execute("SELECT attempts FROM discussion_messages WHERE id=?", (message_id,)).fetchone()["attempts"] == 2


def test_expired_lease_requires_manual_retry_and_stale_result_cannot_replace_it(client, monkeypatch):
    analysis()
    send(client)
    job = discussions.claim_next(timeout=240)
    with connect() as db:
        db.execute("UPDATE discussion_messages SET lease_until=? WHERE id=?",
                   ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), job["id"]))
    projected = client.get("/api/v1/discussions/analysis/analysis-one").json()
    assert projected["messages"][-1]["status"] == "failed" and not projected["busy"]
    with connect(readonly=True) as db:
        assert db.execute("SELECT status FROM discussion_messages WHERE id=?", (job["id"],)).fetchone()["status"] == "running"
    assert discussions.claim_next(timeout=240) is None
    failed = client.get("/api/v1/discussions/analysis/analysis-one").json()
    assert failed["messages"][-1]["error"]["code"] == "DISCUSSION_RESULT_UNKNOWN"
    assert not discussions._finish(job, content="late untrusted old answer")
    path = f"/api/v1/discussions/analysis/analysis-one/messages/{job['id']}/retry"
    assert client.post(path).status_code == 202
    replacement = discussions.claim_next(timeout=240)
    assert replacement["claim_token"] != job["claim_token"]
    assert not discussions._finish(job, content="old attempt after explicit retry")
    assert discussions._finish(replacement, content="new successful attempt")
    assert client.get("/api/v1/discussions/analysis/analysis-one").json()["messages"][-1]["content"] == "new successful attempt"


def _claim_in_process(_index):
    from trade_helper.discussions import claim_next

    return claim_next(timeout=240)


def test_threads_submit_one_pair_and_processes_claim_only_once(client):
    analysis()
    request_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: send(client, request_id=request_id), range(6)))
    assert all(result.status_code == 202 for result in results)
    assert len(client.get("/api/v1/discussions/analysis/analysis-one").json()["messages"]) == 2
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        claims = list(pool.map(_claim_in_process, range(6)))
    assert sum(job is not None for job in claims) == 1


def test_paginated_history_keeps_all_messages_and_model_window_keeps_recent_pairs(client, monkeypatch):
    analysis()
    calls = fake_reply(monkeypatch)
    for index in range(45):
        assert send(client, message=f"第 {index} 個問題").status_code == 202
        assert discussions.run_once()
    latest = client.get("/api/v1/discussions/analysis/analysis-one").json()
    assert len(latest["messages"]) == 50 and latest["has_more"]
    assert latest["messages"][0]["sequence"] == 41
    earlier = client.get(f"/api/v1/discussions/analysis/analysis-one?before_sequence={latest['before_sequence']}").json()
    assert len(earlier["messages"]) == 40 and not earlier["has_more"]
    assert earlier["before_sequence"] is None
    assert [row["sequence"] for row in earlier["messages"] + latest["messages"]] == list(range(1, 91))
    assert len(calls[-1][1]) == 79 and calls[-1][1][0]["content"] == "第 5 個問題"
    assert calls[-1][0]["discussion_history_window"]["earlier_history_omitted"]


def test_failed_pair_not_sent_to_model_and_retry_respects_busy(client, monkeypatch):
    analysis()
    monkeypatch.setattr(discussions, "generate_reply", lambda *_, **__: (_ for _ in ()).throw(ValueError("raw model")))
    first = send(client, message="失敗的舊問題").json()
    assert discussions.run_once()
    calls = fake_reply(monkeypatch)
    send(client, message="新問題")
    first_id = first["messages"][-1]["id"]
    assert client.post(f"/api/v1/discussions/analysis/analysis-one/messages/{first_id}/retry").status_code == 409
    assert discussions.run_once()
    assert [row["content"] for row in calls[-1][1]] == ["新問題"]


def test_history_character_budget_keeps_whole_pairs_and_current_max_length_question(client, monkeypatch):
    analysis()
    calls = fake_reply(monkeypatch, text="回覆" * 5000)
    for index in range(8):
        assert send(client, message=str(index) + "問" * 5999).status_code == 202
        assert discussions.run_once()
    messages = calls[-1][1]
    assert len(messages) == 7  # Three complete earlier turns and this question.
    assert sum(len(row["content"]) for row in messages) <= discussions.HISTORY_MAX_CHARS
    assert [row["role"] for row in messages] == ["user", "assistant"] * 3 + ["user"]
    assert messages[-1]["content"] == "7" + "問" * 5999
    assert calls[-1][0]["history_notice"]
    with connect(readonly=True) as db:
        assert db.execute("SELECT count(*) AS n FROM discussion_messages").fetchone()["n"] == 16


def test_retry_recovers_expired_job_without_waiting_for_another_worker(client):
    analysis()
    send(client)
    job = discussions.claim_next(timeout=240)
    with connect() as db:
        db.execute("UPDATE discussion_messages SET lease_until=NULL WHERE id=?", (job["id"],))
    assert client.get("/api/v1/discussions/analysis/analysis-one").json()["messages"][-1]["status"] == "failed"
    response = client.post(f"/api/v1/discussions/analysis/analysis-one/messages/{job['id']}/retry")
    assert response.status_code == 202 and response.json()["messages"][-1]["status"] == "queued"
    with connect(readonly=True) as db:
        assert db.execute("SELECT attempts FROM discussion_messages WHERE id=?", (job["id"],)).fetchone()["attempts"] == 1


def test_deleted_analysis_cascades_discussion_without_retaining_personal_snapshot(client):
    analysis()
    send(client)
    with connect() as db:
        db.execute("DELETE FROM analyses WHERE id='analysis-one'")
    assert client.get("/api/v1/discussions/analysis/analysis-one").status_code == 404
    with connect(readonly=True) as db:
        assert db.execute("SELECT count(*) AS n FROM discussion_messages").fetchone()["n"] == 0
        assert db.execute("SELECT count(*) AS n FROM discussion_sessions").fetchone()["n"] == 0


@pytest.mark.parametrize("provider", ["openai", "claude_code", "codex"])
def test_plain_text_model_transport_uses_existing_session_without_tools(monkeypatch, provider):
    calls, closed = [], []
    response = SimpleNamespace(output=[], output_text="繁體中文的自由對話", status="completed")

    def create(**kwargs):
        calls.append(kwargs)
        return response

    session = SimpleNamespace(provider=provider, model="authorized-model",
                              stream_text=create, close=lambda: closed.append(1))
    monkeypatch.setattr(discussions, "ModelSession", lambda **_: session)
    result = discussions.generate_reply({}, [{"role": "user", "content": "追問"}], timeout=240)
    assert result == ("繁體中文的自由對話", provider, "authorized-model")
    assert closed == [1] and 230 < calls[0]["timeout"] <= 240
    assert "tools" not in calls[0]
    assert calls[0]["on_text"] is None
    assert json.loads(calls[0]["context"])["conversation"][-1]["content"] == "追問"


@pytest.mark.parametrize("response", [
    SimpleNamespace(output=[], output_text="", status="completed"),
    SimpleNamespace(output=[], output_text="partial", status="incomplete"),
    SimpleNamespace(output=[SimpleNamespace(type="function_call")], output_text="tool", status="completed"),
])
def test_unfinished_or_tool_reply_never_becomes_completed_text(monkeypatch, response):
    closed = []
    monkeypatch.setattr(discussions, "ModelSession", lambda **_: SimpleNamespace(
        provider="openai", model="fake", stream_text=lambda **_: response,
        close=lambda: closed.append(1)))
    with pytest.raises(ValueError):
        discussions.generate_reply({}, [], timeout=240)
    assert closed == [1]


def test_worker_rotates_queue_priority_and_once_processes_at_most_one(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "run_once", lambda: (calls.append("analysis"), True)[1])
    monkeypatch.setattr(worker, "run_discussion_once", lambda: (calls.append("discussion"), True)[1])
    assert worker.run_work_once()
    assert worker.run_work_once(discussions_first=True)
    assert calls == ["analysis", "discussion"]
    monkeypatch.setattr(worker, "assert_local_mode", lambda: None)
    monkeypatch.setattr(worker, "init_db", lambda: None)
    monkeypatch.setattr(worker.sys, "argv", ["worker", "--once"])
    worker.main()
    assert calls == ["analysis", "discussion", "analysis"]


def test_schema_upgrade_preserves_old_report_and_initializes_empty_discussion_tables(client):
    analysis()
    with connect() as db:
        db.execute("DROP TABLE discussion_messages")
        db.execute("DROP TABLE discussion_sessions")
        db.execute("DROP INDEX macro_interpretations_id_user_unique")
        db.execute("DROP TABLE credential_records")
        db.execute("DROP TABLE credential_local_keys")
        db.execute("ALTER TABLE credential_legacy_records RENAME TO credential_records")
        db.execute("ALTER TABLE credential_legacy_key_metadata RENAME TO credential_key_metadata")
        db.execute("DELETE FROM schema_migrations WHERE version>=4")
        db.execute("PRAGMA user_version=3")
    init_db()
    with connect(readonly=True) as db:
        assert db.execute("PRAGMA user_version").fetchone()["user_version"] == SCHEMA_VERSION
        assert db.execute("SELECT report_json FROM analyses").fetchone()["report_json"]
        assert db.execute("SELECT count(*) AS n FROM discussion_sessions").fetchone()["n"] == 0


def test_analysis_session_follows_frozen_report_locale_and_each_message_archives_prompt(client, monkeypatch):
    from trade_helper.prompts_artifacts import load_prompt_artifact

    analysis()
    with connect() as db:
        row = db.execute("SELECT request_json FROM analyses WHERE id='analysis-one'").fetchone()
        request = json.loads(row["request_json"]) | {"output_locale": "en-US"}
        db.execute("UPDATE analyses SET request_json=? WHERE id='analysis-one'", (json.dumps(request),))
    original = client.post("/api/v1/discussions/analysis/analysis-one/messages", json={
        "message": "Explain this support zone", "request_id": str(uuid4()), "output_locale": "zh-TW",
    }).json()
    assert original["session"]["output_locale"] == "en-US"
    queued = original["messages"][-1]
    assert queued["output_locale"] == "en-US" and queued["prompt_artifact_id"]
    with connect(readonly=True) as db:
        artifact = load_prompt_artifact(db, queued["prompt_artifact_id"])
    received = []

    def generate(context, messages, *, timeout, instructions=None, on_text=None):
        received.append((context, instructions))
        return "Hold for the saved support reaction.", "fake", "fixture"

    monkeypatch.setattr(discussions, "generate_reply", generate)
    # The worker must not consult a changed registry or current UI language.
    monkeypatch.setattr(discussions, "resolve_prompt", lambda *_a, **_kw: pytest.fail("recompiled queued prompt"))
    assert discussions.run_once()
    assert received[0][1] == artifact.instructions
    assert received[0][0]["output_locale"] == "en-US"
    assert received[0][0]["original_report"]["headline"] == "測試報告"
    loaded = client.get("/api/v1/discussions/analysis/analysis-one").json()
    assert loaded["messages"][-1]["content"] == "Hold for the saved support reaction."
    assert loaded["messages"][-1]["prompt_bundle"]["response_locale"] == "en-US"


def test_macro_session_first_locale_is_fixed_and_ui_changes_do_not_rewrite_history(client, monkeypatch):
    macro()
    calls = fake_reply(monkeypatch, text="English answer based on the saved CPI value.")
    first = client.post("/api/v1/discussions/macro/macro-one/messages", json={
        "message": "What does this CPI mean?", "request_id": str(uuid4()), "output_locale": "en-US",
    }).json()
    assert first["session"]["output_locale"] == "en-US"
    assert discussions.run_once()
    second = client.post("/api/v1/discussions/macro/macro-one/messages", json={
        "message": "還有哪些不確定性？", "request_id": str(uuid4()), "output_locale": "zh-TW",
    }).json()
    assert second["session"]["output_locale"] == "en-US"
    assert second["messages"][-1]["output_locale"] == "en-US"
    assert second["messages"][1]["content"] == "English answer based on the saved CPI value."
    assert discussions.run_once()
    assert calls[-1][0]["output_locale"] == "en-US"
    assert calls[-1][0]["original_interpretation"]["outlook"]["summary"] == "舊版CPI背景"


def test_retry_retains_exact_prompt_artifact_even_after_registry_update(client, monkeypatch):
    analysis()
    monkeypatch.setattr(discussions, "generate_reply", lambda *_a, **_kw: (_ for _ in ()).throw(TimeoutError("private")))
    original = send(client).json()["messages"][-1]
    assert discussions.run_once()
    monkeypatch.setattr(discussions, "resolve_prompt", lambda *_a, **_kw: pytest.fail("retry recompiled prompt"))
    response = client.post(f"/api/v1/discussions/analysis/analysis-one/messages/{original['id']}/retry")
    assert response.status_code == 202
    assert response.json()["messages"][-1]["prompt_artifact_id"] == original["prompt_artifact_id"]
    received = []
    monkeypatch.setattr(discussions, "generate_reply", lambda *_, **kw:
                        (received.append(kw["instructions"]) or "Saved language reply", "fake", "model"))
    assert discussions.run_once() and received
    with connect(readonly=True) as db:
        expected = db.execute("SELECT instructions FROM prompt_artifacts WHERE id=?",
                              (original["prompt_artifact_id"],)).fetchone()["instructions"]
    assert received == [expected]


@pytest.mark.parametrize("locale", ["zh-TW", "en-US"])
def test_discussion_safe_failure_and_expired_lease_use_session_language(client, monkeypatch, locale):
    analysis()
    with connect() as db:
        row = db.execute("SELECT request_json FROM analyses WHERE id='analysis-one'").fetchone()
        db.execute("UPDATE analyses SET request_json=? WHERE id='analysis-one'",
                   (json.dumps(json.loads(row["request_json"]) | {"output_locale": locale}),))
    monkeypatch.setattr(discussions, "generate_reply", lambda *_a, **_kw:
                        (_ for _ in ()).throw(TimeoutError("secret provider")))
    send(client)
    assert discussions.run_once()
    failed = client.get("/api/v1/discussions/analysis/analysis-one").json()["messages"][-1]
    assert failed["error"]["code"] == "DISCUSSION_TIMEOUT"
    assert ("timed out" in failed["error"]["message"]) == (locale == "en-US")
    assert "secret" not in failed["error"]["message"]
    client.post(f"/api/v1/discussions/analysis/analysis-one/messages/{failed['id']}/retry")
    job = discussions.claim_next(timeout=240)
    with connect() as db:
        db.execute("UPDATE discussion_messages SET lease_until=NULL WHERE id=?", (job["id"],))
    expired = client.get("/api/v1/discussions/analysis/analysis-one").json()["messages"][-1]
    assert expired["error"]["code"] == "DISCUSSION_RESULT_UNKNOWN"
    assert ("interrupted" in expired["error"]["message"]) == (locale == "en-US")
