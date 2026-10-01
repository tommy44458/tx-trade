import copy
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trade_helper import macro_interpretation as macro
from trade_helper import macro_translation as translation
from trade_helper.api import app
from trade_helper.config import local_user_id
from trade_helper.db import connect, init_db, utc_now
from trade_helper.prompts_artifacts import load_prompt_artifact

from .test_macro_interpretation import make_macro_data


def source_result():
    return {"outlook": {"stance": "bearish", "summary": "物價為 3.0%，流動性仍承壓。"},
            "drivers": [{"title": "CPI", "explanation": "2026-08 年增率 3.0%，前值 2.9%。",
                         "evidence_ids": ["actual:cpi:2026-08"]}],
            "uncertainties": ["共識缺少，不能宣稱高於預期。"],
            "watch_next": ["留意 https://example.test/cpi 的下一次公布。"],
            "saved_values": {"value": "3.0", "period": "2026-08", "unit": "percent"}}


def save_source(identifier="macro-original", owner=None):
    init_db()
    now = utc_now()
    result = source_result()
    with connect() as db:
        db.execute(
            "INSERT INTO macro_interpretations(id,user_id,evidence_version,prompt_version,fingerprint,"
            "evidence_json,status,result_json,created_at,updated_at,generated_at) "
            "VALUES(?,?,?,?,?,?,'succeeded',?,?,?,?)",
            (identifier, owner or local_user_id(), "macro_interpretation_evidence_v1", macro.PROMPT_VERSION,
             identifier, json.dumps({"as_of": now, "evidence": [{"id": "actual:cpi:2026-08", "value": "3.0"}]}),
             json.dumps(result, ensure_ascii=False), now, now, now),
        )
    return result


def fake_translated(source):
    supplied, literals = translation.translation_input(source, "zh-TW", "en-US")
    texts = {path: "English interpretation: " + text for path, text in supplied["texts"].items()}
    return translation.read_translation(json.dumps({"texts": texts}), source, literals)


def fake_model(monkeypatch):
    calls = []

    def generate(job, instructions, *, timeout):
        # Another writer is available throughout model generation.
        with connect() as db:
            db.execute("SELECT count(*) FROM macro_translation_jobs")
        calls.append((job, instructions))
        return fake_translated(json.loads(job["source_result_json"])), {"provider": "fake", "model": "fixture"}

    monkeypatch.setattr(translation, "_generate_translation", generate)
    return calls


def test_get_language_switch_is_readonly_and_explicit_translation_reuses_semantic_result(monkeypatch):
    snapshot, decisions = make_macro_data(monkeypatch)
    calls = fake_model(monkeypatch)
    with TestClient(app) as client:
        client.post("/api/v1/events/macro-interpretation/ensure", json={"output_locale": "zh-TW"})
        original = client.get("/api/v1/events/macro-interpretation").json()["interpretation"]
        identifier = original["id"]
        english = client.get("/api/v1/events/macro-interpretation?output_locale=en-US").json()
        assert english["translation"]["status"] == "missing"
        assert english["interpretation"]["output_locale"] == "zh-TW"
        assert english["requested_locale"] == "en-US"
        for _ in range(3):
            client.get("/api/v1/events/macro-interpretation?locale=en-US")
        assert len(decisions) == 1 and not calls
        with connect(readonly=True) as db:
            assert db.execute("SELECT count(*) AS n FROM macro_translation_jobs").fetchone()["n"] == 0
        path = f"/api/v1/events/macro-interpretation/{identifier}/translate"
        first = client.post(path, json={"output_locale": "en-US"})
        second = client.post(path, json={"output_locale": "en-US"})
        assert first.status_code == second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]
        assert translation.run_macro_translation_once()
        translated = client.get("/api/v1/events/macro-interpretation?output_locale=en-US").json()
        assert translated["translation"]["status"] == "available"
        assert translated["interpretation"]["id"] == identifier
        assert translated["interpretation"]["output_locale"] == "en-US"
        assert translated["interpretation"]["source_locale"] == "zh-TW"
        assert translated["interpretation"]["outlook"]["stance"] == original["outlook"]["stance"]
        assert translated["interpretation"]["drivers"][0]["evidence_ids"] == original["drivers"][0]["evidence_ids"]
        assert translated["interpretation"]["evidence"] == snapshot["evidence"]
        assert client.post(path, json={"output_locale": "en-US"}).json()["status"] == "available"
        assert not translation.run_macro_translation_once()
        # Asking for another locale also never redoes the canonical direction.
        client.post("/api/v1/events/macro-interpretation/ensure", json={"output_locale": "en-US"})
    assert len(decisions) == len(calls) == 1


def test_translation_preserves_entire_original_json_numbers_periods_and_references(monkeypatch):
    original = save_source()
    fake_model(monkeypatch)
    queued = translation.request_macro_translation("macro-original", local_user_id(), "en-US")
    assert queued["status"] == "queued"
    assert translation.run_macro_translation_once()
    with connect(readonly=True) as db:
        source = db.execute("SELECT * FROM macro_interpretations WHERE id='macro-original'").fetchone()
        rendered, locale = translation.localized_macro_result(db, source, "en-US")
        view = db.execute("SELECT * FROM macro_interpretation_views").fetchone()
    assert json.loads(source["result_json"]) == original
    assert locale == "en-US" and rendered["saved_values"] == original["saved_values"]
    assert rendered["outlook"]["stance"] == original["outlook"]["stance"]
    assert rendered["drivers"][0]["evidence_ids"] == original["drivers"][0]["evidence_ids"]
    assert "2026-08" in rendered["drivers"][0]["explanation"]
    assert "3.0%" in rendered["outlook"]["summary"]
    assert "2.9%" in rendered["drivers"][0]["explanation"]
    assert "https://example.test/cpi" in rendered["watch_next"][0]
    assert json.loads(view["execution_json"])["translation_only"]


def test_queue_and_retry_preserve_prompt_without_global_recompile_or_auto_retry(monkeypatch):
    save_source()
    state = translation.request_macro_translation("macro-original", local_user_id(), "en-US")
    with connect(readonly=True) as db:
        job = db.execute("SELECT * FROM macro_translation_jobs WHERE id=?", (state["job_id"],)).fetchone()
        original_bundle = load_prompt_artifact(db, job["prompt_artifact_id"])
    monkeypatch.setattr(translation, "resolve_prompt", lambda *_a, **_kw: pytest.fail("queued prompt changed"))
    monkeypatch.setattr(translation, "_generate_translation", lambda *_a, **_kw:
                        (_ for _ in ()).throw(TimeoutError("private provider detail")))
    assert translation.run_macro_translation_once()
    assert not translation.run_macro_translation_once()
    failed = translation.request_macro_translation("macro-original", local_user_id(), "en-US")
    assert failed["status"] == "failed" and "private" not in json.dumps(failed)
    assert translation.request_macro_translation("macro-original", local_user_id(), "en-US", retry=True)["status"] == "queued"
    calls = fake_model(monkeypatch)
    assert translation.run_macro_translation_once()
    assert calls[0][1] == original_bundle.instructions
    with connect(readonly=True) as db:
        retried = db.execute("SELECT * FROM macro_translation_jobs").fetchone()
    assert retried["attempts"] == 2 and retried["prompt_artifact_id"] == job["prompt_artifact_id"]


def _enqueue_process(_):
    return translation.request_macro_translation("macro-original", "cross-process", "en-US")


def _claim_process(_):
    return translation.claim_macro_translation(timeout=120)


def test_cross_process_actions_and_claims_are_singleflight():
    save_source(owner="cross-process")
    with ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn")) as pool:
        queued = list(pool.map(_enqueue_process, range(6)))
        claims = list(pool.map(_claim_process, range(6)))
    assert len({item["job_id"] for item in queued}) == 1
    winners = [item for item in claims if item]
    assert len(winners) == 1
    job = winners[0]
    assert translation._finish_translation(job, payload=fake_translated(json.loads(job["source_result_json"])),
                                           execution={"provider": "fake"})
    with connect(readonly=True) as db:
        assert db.execute("SELECT count(*) AS n FROM macro_interpretation_views").fetchone()["n"] == 1


def test_expired_lease_requires_manual_retry_and_late_result_is_ignored():
    original = save_source()
    translation.request_macro_translation("macro-original", local_user_id(), "en-US")
    old = translation.claim_macro_translation(timeout=120)
    with connect() as db:
        db.execute("UPDATE macro_translation_jobs SET lease_until=? WHERE id=?",
                   ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), old["id"]))
    with connect(readonly=True) as db:
        source = db.execute("SELECT * FROM macro_interpretations WHERE id='macro-original'").fetchone()
        assert translation.macro_translation_status(db, source, "en-US")["status"] == "failed"
        assert db.execute("SELECT status FROM macro_translation_jobs").fetchone()["status"] == "running"
    assert not translation._finish_translation(old, payload=fake_translated(original))
    assert translation.claim_macro_translation(timeout=120) is None
    translation.request_macro_translation("macro-original", local_user_id(), "en-US", retry=True)
    new = translation.claim_macro_translation(timeout=120)
    assert new["claim_token"] != old["claim_token"]
    assert not translation._finish_translation(old, payload=fake_translated(original))
    assert translation._finish_translation(new, payload=fake_translated(original))


def test_ownership_and_deletion_cascade_keep_private_translation_data_separate():
    save_source(owner="alice")
    with pytest.raises(Exception) as error:
        translation.request_macro_translation("macro-original", "bob", "en-US")
    assert error.value.status_code == 404
    translation.request_macro_translation("macro-original", "alice", "en-US")
    with connect() as db:
        db.execute("DELETE FROM macro_interpretations WHERE id='macro-original'")
    with connect(readonly=True) as db:
        assert db.execute("SELECT count(*) AS n FROM macro_translation_jobs").fetchone()["n"] == 0
        assert db.execute("SELECT count(*) AS n FROM macro_interpretation_views").fetchone()["n"] == 0


@pytest.mark.parametrize("mutation", ["remove_literal", "add_number", "add_field", "change_key"])
def test_translation_format_cannot_mutate_literal_values_or_response_field_shape(mutation):
    source = source_result()
    supplied, literals = translation.translation_input(source, "zh-TW", "en-US")
    texts = copy.deepcopy(supplied["texts"])
    if mutation == "remove_literal":
        texts["outlook.summary"] = texts["outlook.summary"].replace("__MACRO_LITERAL_0000__", "4.5%")
    elif mutation == "add_number":
        texts["outlook.summary"] += " 99%"
    elif mutation == "add_field":
        texts["outlook.stance"] = "bullish"
    else:
        texts["made_up_key"] = texts.pop("watch_next.0")
    with pytest.raises(ValueError):
        translation.read_translation(json.dumps({"texts": texts}), source, literals)


@pytest.mark.parametrize("provider", ["codex", "chatgpt", "openai"])
def test_translation_transport_uses_saved_instructions_and_no_tools(monkeypatch, provider):
    original = save_source()
    translation.request_macro_translation("macro-original", local_user_id(), "en-US")
    job = translation.claim_macro_translation(timeout=120)
    supplied, _ = translation.translation_input(original, "zh-TW", "en-US")
    calls, closes = [], []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps({"texts": supplied["texts"]}), status="completed")

    session = SimpleNamespace(provider=provider, model="mock-model", analyze_codex=create,
                              client=SimpleNamespace(responses=SimpleNamespace(create=create)),
                              close=lambda: closes.append(True))
    monkeypatch.setattr(translation, "ModelSession", lambda **_: session)
    result, _ = translation._generate_translation(job, "immutable saved instructions", timeout=120)
    assert result == original and closes == [True]
    assert calls[0]["instructions"] == "immutable saved instructions" and calls[0]["tools"] == []
    assert 110 < calls[0]["timeout"] <= 120


@pytest.mark.parametrize("locale", ["zh-TW", "en-US"])
def test_translation_safe_failure_and_get_lease_projection_follow_target_locale(monkeypatch, locale):
    save_source()
    with connect() as db:
        db.execute("UPDATE macro_interpretations SET output_locale=? WHERE id='macro-original'",
                   ("en-US" if locale == "zh-TW" else "zh-TW",))
    translation.request_macro_translation("macro-original", local_user_id(), locale)
    monkeypatch.setattr(translation, "_generate_translation", lambda *_a, **_kw:
                        (_ for _ in ()).throw(TimeoutError("secret")))
    assert translation.run_macro_translation_once()
    failed = translation.request_macro_translation("macro-original", local_user_id(), locale)
    assert failed["error"]["code"] == "MACRO_TRANSLATION_TIMEOUT"
    assert ("timed out" in failed["error"]["message"]) == (locale == "en-US")
    translation.request_macro_translation("macro-original", local_user_id(), locale, retry=True)
    job = translation.claim_macro_translation(timeout=120)
    with connect() as db:
        db.execute("UPDATE macro_translation_jobs SET lease_until=NULL WHERE id=?", (job["id"],))
    with connect(readonly=True) as db:
        row = db.execute("SELECT * FROM macro_interpretations").fetchone()
        projected = translation.macro_translation_status(db, row, locale)
        assert db.execute("SELECT status FROM macro_translation_jobs").fetchone()["status"] == "running"
    assert projected["error"]["code"] == "MACRO_TRANSLATION_RESULT_UNKNOWN"
    assert ("interrupted" in projected["error"]["message"]) == (locale == "en-US")
