import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trade_helper import macro_interpretation as macro
from trade_helper.api import app
from trade_helper.codex_bridge import CodexError
from trade_helper.db import connect, init_db


def evidence(value="3", *, now=None, identifier="actual:cpi"):
    now = now or datetime.now(UTC)
    fact = {"id": identifier, "type": "official_actual", "source": "bls", "kind": "cpi",
            "metric": "cpi_yoy", "period": "2026-08", "unit": "percent", "value": value,
            "previous_value": "2.9", "published_at": (now - timedelta(days=1)).isoformat(),
            "observed_at": (now - timedelta(days=1)).isoformat(), "version": 1,
            "source_url": "https://www.bls.gov/news.release/cpi.htm"}
    fingerprint = sha256(json.dumps({key: item for key, item in fact.items()
                                    if key not in {"id", "observed_at", "version"}},
                                   sort_keys=True).encode()).hexdigest()
    return {"version": "macro_interpretation_evidence_v1", "fingerprint": fingerprint,
            "as_of": now.isoformat(), "evidence_count": 1, "evidence": [fact],
            "coverage": {"official_actual_kinds": ["cpi"], "missing_actuals": ["employment", "pce", "gdp"],
                         "consensus_status": "not_available"}}


def result(identifier="actual:cpi"):
    return {"outlook": {"stance": "neutral", "summary": "物價壓力仍在，但單一數據不足以判斷政策方向。"},
            "drivers": [{"title": "CPI", "explanation": "八月年增率 3%，略高於前值 2.9%，需與其他數據比較。",
                         "evidence_ids": [identifier]}],
            "uncertainties": ["就業與成長資料不完整。"], "watch_next": ["留意下一次物價與官方政策公布。"]}


def make_macro_data(monkeypatch):
    init_db()
    snapshot = evidence()
    calls = []
    monkeypatch.setattr(macro, "load_macro_interpretation_evidence", lambda *_: deepcopy(snapshot))

    def generate(data, **_kwargs):
        # A second writer can acquire the database while the model is running.
        # The expensive request never holds the claim transaction open.
        with connect() as db:
            db.execute("SELECT count(*) FROM macro_interpretations")
        calls.append(deepcopy(data))
        return result(data["evidence"][0]["id"]), {"provider": "fake", "model": "fixture-model", "model_requests": 1}

    monkeypatch.setattr(macro, "_generate", generate)
    return snapshot, calls


@pytest.fixture
def macro_data(monkeypatch):
    return make_macro_data(monkeypatch)


def test_get_is_read_only_and_never_generates_or_checks_auth(macro_data, monkeypatch):
    monkeypatch.setattr(macro, "ModelSession", lambda **_kwargs: pytest.fail("GET must not check model auth"))
    with TestClient(app) as client:
        first = client.get("/api/v1/events/macro-interpretation")
        second = client.get("/api/v1/events/macro-interpretation")
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "missing"
    assert first.json()["interpretation"] is None
    assert macro_data[1] == []
    with connect(readonly=True) as db:
        assert db.execute("SELECT count(*) AS count FROM macro_interpretations").fetchone()["count"] == 0


def test_explicit_ensure_runs_once_and_same_facts_reuse_across_poll_retry_or_provider_changes(macro_data, monkeypatch):
    with TestClient(app) as client:
        started = client.post("/api/v1/events/macro-interpretation/ensure", json={})
        assert started.json()["status"] == "running"
        ready = client.get("/api/v1/events/macro-interpretation").json()
        assert ready["status"] == "succeeded" and ready["cached"] and not ready["stale"]
        assert ready["interpretation"]["outlook"]["stance"] == "neutral"
        identifier = ready["interpretation"]["id"]
        monkeypatch.setenv("OPENAI_MODEL", "changed-model-but-same-official-data")
        for body in ({}, {"retry": True}):
            response = client.post("/api/v1/events/macro-interpretation/ensure", json=body).json()
            assert response["interpretation"]["id"] == identifier
        assert client.post("/api/v1/events/macro-interpretation/ensure", json={"force": True}).status_code == 422
    assert len(macro_data[1]) == 1


def test_actual_revision_creates_new_interpretation_and_old_result_is_only_stale_display(macro_data):
    snapshot, calls = macro_data
    cutoff = datetime.fromisoformat(snapshot["as_of"])
    original = macro.ensure_macro_interpretation(cutoff)
    snapshot.update(evidence("3.2", now=cutoff))
    pending = macro.macro_interpretation_status(cutoff)
    assert pending["status"] == "missing" and pending["stale"]
    assert pending["interpretation"]["id"] == original["interpretation"]["id"]
    assert macro.analysis_macro_interpretation(pending)["interpretation"] is None
    updated = macro.ensure_macro_interpretation(cutoff)
    assert updated["status"] == "succeeded" and not updated["stale"]
    assert updated["interpretation"]["id"] != original["interpretation"]["id"]
    assert len(calls) == 2
    with connect(readonly=True) as db:
        saved = db.execute("SELECT status FROM macro_interpretations").fetchall()
        assert len(saved) == 2 and all(row["status"] == "succeeded" for row in saved)


@pytest.mark.parametrize("failure,status,code", [
    (TimeoutError("secret request detail"), "failed", "MACRO_MODEL_TIMEOUT"),
    (ValueError("secret invalid model text"), "failed", "MACRO_RESPONSE_FORMAT"),
    (RuntimeError("secret upstream detail"), "failed", "MACRO_MODEL_FAILED"),
    (RuntimeError("OPENAI_API_KEY and OPENAI_MODEL are required for Agent analysis"),
     "unconfigured", "MACRO_MODEL_UNCONFIGURED"),
])
def test_failed_same_version_does_not_auto_retry_and_explicit_retry_recovers(macro_data, monkeypatch,
                                                                          failure, status, code):
    snapshot, calls = macro_data
    working = macro._generate
    failures = []

    def fail(_data, **_kwargs):
        failures.append(1)
        raise failure

    monkeypatch.setattr(macro, "_generate", fail)
    cutoff = datetime.fromisoformat(snapshot["as_of"])
    failed = macro.ensure_macro_interpretation(cutoff)
    assert failed["status"] == status and failed["error"]["code"] == code
    assert "secret" not in json.dumps(failed)
    assert failed["interpretation"] is None
    assert macro.ensure_macro_interpretation(cutoff)["status"] == status
    assert len(failures) == 1
    monkeypatch.setattr(macro, "_generate", working)
    recovered = macro.ensure_macro_interpretation(cutoff, retry=True)
    assert recovered["status"] == "succeeded" and len(calls) == 1
    with connect(readonly=True) as db:
        row = db.execute("SELECT attempts,claim_token,lease_until,error_code FROM macro_interpretations").fetchone()
        assert row == {"attempts": 2, "claim_token": None, "lease_until": None, "error_code": None}


def test_threads_share_one_generation_and_wait_for_the_same_persisted_result(macro_data, monkeypatch):
    snapshot, calls = macro_data
    generate = macro._generate

    def slow(data, **kwargs):
        time.sleep(0.05)
        return generate(data, **kwargs)

    monkeypatch.setattr(macro, "_generate", slow)
    cutoff = datetime.fromisoformat(snapshot["as_of"])
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: macro.ensure_macro_interpretation(cutoff, wait_seconds=5), range(6)))
    assert len(calls) == 1
    assert all(item["status"] == "succeeded" for item in responses)
    assert len({item["interpretation"]["id"] for item in responses}) == 1


def _claim_in_process(snapshot):
    return macro._claim(snapshot, "cross-process-owner", retry=False)


def test_cross_process_singleflight_claims_one_model_call(macro_data):
    snapshot, calls = macro_data
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        claims = list(pool.map(_claim_in_process, [snapshot] * 8))
    winners = [item for item in claims if item is not None]
    assert len(winners) == 1
    macro._run_claim(winners[0])
    state = macro.macro_interpretation_status(datetime.fromisoformat(snapshot["as_of"]),
                                            user_id="cross-process-owner")
    assert state["status"] == "succeeded" and len(calls) == 1


def test_expired_lease_is_read_only_failure_and_late_old_completion_cannot_replace_retry(macro_data):
    snapshot, calls = macro_data
    cutoff = datetime.fromisoformat(snapshot["as_of"])
    abandoned = macro._claim(snapshot, "owner", retry=False)
    with connect() as db:
        db.execute("UPDATE macro_interpretations SET lease_until=? WHERE id=?",
                   ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), abandoned["id"]))
    expired = macro.macro_interpretation_status(cutoff, user_id="owner")
    assert expired["status"] == "failed" and expired["error"]["code"] == "MACRO_LEASE_EXPIRED"
    assert macro._claim(snapshot, "owner", retry=False) is None
    recovered = macro._claim(snapshot, "owner", retry=True)
    assert recovered["token"] != abandoned["token"]
    macro._store_result(abandoned, result(), {"provider": "late-old-model"})
    assert macro.macro_interpretation_status(cutoff, user_id="owner")["status"] == "running"
    macro._run_claim(abandoned)
    assert calls == []  # A delayed old background task must not start a model.
    macro._run_claim(recovered)
    assert macro.macro_interpretation_status(cutoff, user_id="owner")["status"] == "succeeded"
    assert len(calls) == 1


def test_same_semantic_values_rebind_cutoff_provenance_and_driver_ids_without_future_revision(macro_data):
    snapshot, calls = macro_data
    cutoff = datetime.fromisoformat(snapshot["as_of"])
    future = deepcopy(snapshot)
    future["as_of"] = (cutoff + timedelta(days=1)).isoformat()
    future["evidence"][0].update(id="future-document-version-id", version=2,
                                 observed_at=(cutoff + timedelta(hours=2)).isoformat())
    macro.ensure_macro_interpretation(cutoff + timedelta(days=1), evidence=future)
    historical = macro.ensure_macro_interpretation(cutoff, evidence=snapshot)
    assert len(calls) == 1 and historical["status"] == "succeeded"
    frozen = macro.analysis_macro_interpretation(historical)["interpretation"]
    assert frozen["evidence"] == snapshot["evidence"]
    assert frozen["drivers"][0]["evidence_ids"] == ["actual:cpi"]
    assert frozen["as_of"] == cutoff.isoformat()
    assert all(datetime.fromisoformat(item["observed_at"]) <= cutoff for item in frozen["evidence"])


def test_no_usable_evidence_is_insufficient_and_does_not_create_model_job(macro_data):
    snapshot, calls = macro_data
    snapshot["evidence_count"] = 0
    snapshot["evidence"] = []
    with TestClient(app) as client:
        assert client.get("/api/v1/events/macro-interpretation").json()["status"] == "insufficient"
        assert client.post("/api/v1/events/macro-interpretation/ensure", json={"retry": True}).json()["status"] == "insufficient"
    assert calls == []


@pytest.mark.parametrize("provider", ["codex", "claude_code", "openai"])
def test_generation_uses_existing_provider_adapter_without_tools(macro_data, monkeypatch, provider):
    snapshot, _calls = macro_data
    response = SimpleNamespace(output_text=json.dumps(result()),
                               usage=SimpleNamespace(input_tokens=123, output_tokens=78))
    requests, closes = [], []

    def create(**kwargs):
        requests.append(kwargs)
        assert kwargs["tools"] == []
        return response

    session = SimpleNamespace(provider=provider, model="fixture-provider-model",
                              uses_local_agent=provider != "openai",
                              analyze_local_agent=create, client=SimpleNamespace(responses=SimpleNamespace(create=create)),
                              close=lambda: closes.append(1))
    monkeypatch.setattr(macro, "ModelSession", lambda **_kwargs: session)
    # Fixture replaced _generate for integration; call the real function saved
    # below to verify transport without using a real credential or model.
    generated, execution = _REAL_GENERATE(snapshot)
    assert generated["outlook"]["stance"] == "neutral"
    assert execution["provider"] == provider and execution["input_tokens"] == 123
    assert closes == [1] and len(requests) == 1
    if provider != "openai":
        assert "context" in requests[0] and requests[0]["tool_handler"]("anything") == {}
    else:
        assert requests[0]["tool_choice"] == "none"
        assert requests[0]["input"][0]["role"] == "user"


_REAL_GENERATE = macro._generate


def test_codex_setup_failure_is_unconfigured_and_never_exposes_private_error(macro_data, monkeypatch):
    snapshot, _ = macro_data
    def unavailable(**_kwargs):
        raise CodexError("private diagnostic")
    monkeypatch.setattr(macro, "ModelSession", unavailable)
    monkeypatch.setattr(macro, "_generate", _REAL_GENERATE)
    state = macro.ensure_macro_interpretation(datetime.fromisoformat(snapshot["as_of"]))
    assert state["status"] == "unconfigured"
    assert "private" not in json.dumps(state)


def test_parser_does_not_invent_stance_or_keep_unverified_driver_ids():
    data = evidence()
    model = result()
    model["drivers"][0]["evidence_ids"].append("fabricated-id")
    parsed = macro._read_result("```json\n" + json.dumps(model) + "\n```", data)
    assert parsed["drivers"][0]["evidence_ids"] == ["actual:cpi"]
    with pytest.raises(ValueError):
        macro._read_result(json.dumps({"outlook": {"summary": "only prose"}}), data)


def test_current_interpretation_is_isolated_by_owner(macro_data):
    snapshot, calls = macro_data
    cutoff = datetime.fromisoformat(snapshot["as_of"])
    first = macro.ensure_macro_interpretation(cutoff, user_id="owner-a")
    assert macro.macro_interpretation_status(cutoff, user_id="owner-b")["status"] == "missing"
    second = macro.ensure_macro_interpretation(cutoff, user_id="owner-b")
    assert first["interpretation"]["id"] != second["interpretation"]["id"]
    assert len(calls) == 2


def test_macro_canonical_locale_and_instructions_are_frozen_without_locale_in_semantic_key(macro_data, monkeypatch):
    snapshot, calls = macro_data
    original_generate = macro._generate
    claimed = macro._claim(snapshot, "english-owner", retry=False, output_locale="en-US")
    with connect(readonly=True) as db:
        row = db.execute("SELECT * FROM macro_interpretations WHERE id=?", (claimed["id"],)).fetchone()
        instructions = db.execute("SELECT instructions FROM prompt_artifacts WHERE id=?",
                                  (row["prompt_artifact_id"],)).fetchone()["instructions"]
    received = []

    def generate(data, **kwargs):
        received.append(kwargs)
        return original_generate(data, **kwargs)

    monkeypatch.setattr(macro, "_generate", generate)
    monkeypatch.setattr(macro, "resolve_prompt", lambda *_a, **_kw: pytest.fail("macro prompt recompiled"))
    macro._run_claim(claimed)
    cutoff = datetime.fromisoformat(snapshot["as_of"])
    english = macro.ensure_macro_interpretation(cutoff, user_id="english-owner", output_locale="en-US")
    chinese = macro.ensure_macro_interpretation(cutoff, user_id="english-owner", output_locale="zh-TW")
    assert english["interpretation"]["id"] == chinese["interpretation"]["id"]
    assert english["interpretation"]["output_locale"] == "en-US"
    assert chinese["interpretation"]["output_locale"] == "en-US"
    assert chinese["translation"]["status"] == "missing"
    assert len(calls) == len(received) == 1
    assert received[0] == {"instructions": instructions, "output_locale": "en-US"}


def test_macro_error_display_follows_requested_locale_without_rewriting_saved_failure(macro_data, monkeypatch):
    snapshot, _ = macro_data
    monkeypatch.setattr(macro, "_generate", lambda *_a, **_kw:
                        (_ for _ in ()).throw(TimeoutError("private upstream")))
    cutoff = datetime.fromisoformat(snapshot["as_of"])
    english = macro.ensure_macro_interpretation(cutoff, output_locale="en-US")
    assert english["error"]["code"] == "MACRO_MODEL_TIMEOUT"
    assert "timed out" in english["error"]["message"]
    chinese = macro.macro_interpretation_status(cutoff, output_locale="zh-TW")
    assert "逾時" in chinese["error"]["message"]
    with connect(readonly=True) as db:
        stored = db.execute("SELECT error_message FROM macro_interpretations").fetchone()["error_message"]
    assert stored == english["error"]["message"] and "private" not in stored
