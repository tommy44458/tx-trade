"""Explicitly queued language views of an immutable saved macro interpretation.

Translations replace natural-language fields only. The canonical economic
decision, evidence, references and literal values are never sent back through a
second direction decision. GET paths only inspect stored views and jobs.
"""

import copy
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from time import monotonic

from fastapi import HTTPException
from openai import APITimeoutError, OpenAI

from .db import connect, new_id, utc_now
from .model_providers import ModelSession, analysis_timeout_seconds
from .prompts.registry import resolve_prompt
from .prompts_artifacts import load_prompt_artifact, save_prompt_artifact
from .storage_codec import utc_text
from .task_locale_errors import localized_task_error

RENDER_VERSION = "macro_language_view_v1"
MAX_TIMEOUT_SECONDS = 120
INTERRUPTED_CODE = "MACRO_TRANSLATION_RESULT_UNKNOWN"
LITERAL = re.compile(r"https?://[^\s<>\"']+|[-+]?\d+(?:[.,]\d+)*(?:%|％)?")
PLACEHOLDER = re.compile(r"__MACRO_LITERAL_\d{4}__")


def _source_hash(row: dict) -> str:
    return hashlib.sha256(row["result_json"].encode()).hexdigest()


def _expired(row: dict) -> bool:
    return row["status"] == "running" and (not row["lease_until"] or row["lease_until"] <= utc_now())


def _view(db, row: dict, locale: str):
    return db.execute(
        "SELECT * FROM macro_interpretation_views WHERE interpretation_id=? AND user_id=? "
        "AND response_locale=? AND render_version=? AND source_result_sha256=?",
        (row["id"], row["user_id"], locale, RENDER_VERSION, _source_hash(row)),
    ).fetchone()


def localized_macro_result(db, row: dict, locale: str) -> tuple[dict, str]:
    """Fall back visibly to original text. Never authorizes a translation call."""
    source_locale = row.get("output_locale") or "zh-TW"
    view = _view(db, row, locale) if locale != source_locale else None
    if view:
        return json.loads(view["payload_json"]), locale
    return json.loads(row["result_json"]), source_locale


def macro_translation_status(db, row: dict | None, locale: str) -> dict:
    state = {"status": "missing", "target_locale": locale, "render_version": RENDER_VERSION,
             "interpretation_id": row["id"] if row else None, "job_id": None, "error": None}
    if not row or row["status"] != "succeeded" or not row.get("result_json"):
        return state
    if locale == (row.get("output_locale") or "zh-TW") or _view(db, row, locale):
        return state | {"status": "available"}
    job = db.execute(
        "SELECT * FROM macro_translation_jobs WHERE interpretation_id=? AND user_id=? "
        "AND response_locale=? AND render_version=? AND source_result_sha256=?",
        (row["id"], row["user_id"], locale, RENDER_VERSION, _source_hash(row)),
    ).fetchone()
    if not job:
        return state
    expired = _expired(job)
    code = INTERRUPTED_CODE if expired else job["error_code"]
    error = ({"code": code, "message": localized_task_error(code, locale, job["error_message"] or ""),
              "retryable": True if expired else bool(job["error_retryable"])}
             if expired or job["error_code"] else None)
    return state | {"status": "failed" if expired else job["status"], "job_id": job["id"], "error": error}


def _expire_jobs(db) -> None:
    now = utc_now()
    db.execute(
        "UPDATE macro_translation_jobs SET status='failed',claim_token=NULL,lease_until=NULL,"
        "completed_at=?,updated_at=?,error_code=?,error_message=?,error_retryable=1 "
        "WHERE status='running' AND (lease_until IS NULL OR lease_until<=?)",
        (now, now, INTERRUPTED_CODE, localized_task_error(INTERRUPTED_CODE, "en-US", ""), now),
    )


def request_macro_translation(interpretation_id: str, user_id: str, output_locale: str,
                              *, retry: bool = False) -> dict:
    """Reserve an explicit user action once, without opening credentials or models."""
    if output_locale not in {"zh-TW", "en-US"}:
        raise HTTPException(422, {"code": "UNSUPPORTED_LOCALE", "message": "Unsupported language."})
    with connect() as db:
        from .desktop_updates import require_task_start_allowed

        require_task_start_allowed(db)
        _expire_jobs(db)
        row = db.execute("SELECT * FROM macro_interpretations WHERE id=? AND user_id=?",
                         (interpretation_id, user_id)).fetchone()
        if not row:
            raise HTTPException(404, {"code": "MACRO_NOT_FOUND", "message": "Macro interpretation not found."})
        if row["status"] != "succeeded" or not row.get("result_json"):
            raise HTTPException(409, {"code": "MACRO_NOT_READY", "message": "The interpretation is not ready."})
        state = macro_translation_status(db, row, output_locale)
        if state["status"] == "available":
            return state
        existing = db.execute(
            "SELECT * FROM macro_translation_jobs WHERE interpretation_id=? AND response_locale=? AND render_version=?",
            (interpretation_id, output_locale, RENDER_VERSION),
        ).fetchone()
        if existing:
            if existing["source_result_sha256"] != _source_hash(row):
                raise HTTPException(409, {"code": "MACRO_TRANSLATION_SOURCE_CHANGED",
                                          "message": "The stored source changed; use a new interpretation version."})
            if existing["status"] == "failed" and retry and existing["error_retryable"]:
                db.execute("UPDATE macro_translation_jobs SET status='queued',completed_at=NULL,"
                           "claim_token=NULL,lease_until=NULL,error_code=NULL,error_message=NULL,"
                           "error_retryable=NULL,updated_at=? WHERE id=?", (utc_now(), existing["id"]))
            return macro_translation_status(db, row, output_locale)
        bundle = resolve_prompt("macro_translation", response_locale=output_locale)
        artifact = save_prompt_artifact(db, bundle)
        now = utc_now()
        db.execute(
            "INSERT INTO macro_translation_jobs(id,user_id,interpretation_id,response_locale,render_version,"
            "source_result_sha256,source_result_json,prompt_artifact_id,prompt_bundle_json,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?)",
            (new_id("macro_translation"), user_id, interpretation_id, output_locale, RENDER_VERSION,
             _source_hash(row), row["result_json"], artifact, json.dumps(bundle.metadata()), now, now),
        )
        return macro_translation_status(db, row, output_locale)


def claim_macro_translation(*, timeout: float) -> dict | None:
    with connect() as db:
        from .desktop_updates import register_desktop_execution, update_is_draining

        if update_is_draining(db):
            return None
        _expire_jobs(db)
        job = db.execute("SELECT * FROM macro_translation_jobs WHERE status='queued' ORDER BY created_at,id LIMIT 1").fetchone()
        if not job:
            return None
        token = new_id("translation_claim")
        until = utc_text(datetime.now(UTC) + timedelta(seconds=timeout + 60))
        db.execute("UPDATE macro_translation_jobs SET status='running',claim_token=?,lease_until=?,"
                   "attempts=attempts+1,updated_at=? WHERE id=? AND status='queued'",
                   (token, until, utc_now(), job["id"]))
        return job | {"status": "running", "claim_token": token, "lease_until": until,
                      "update_execution_id": register_desktop_execution(db, "macro_translations", job["id"])}


def _text_fields(source: dict) -> dict[str, str]:
    fields = {"outlook.summary": source["outlook"]["summary"]}
    for index, driver in enumerate(source.get("drivers", [])):
        for name in ("title", "explanation"):
            fields[f"drivers.{index}.{name}"] = driver[name]
    for name in ("uncertainties", "watch_next"):
        for index, text in enumerate(source.get(name, [])):
            fields[f"{name}.{index}"] = text
    if any(not isinstance(text, str) for text in fields.values()):
        raise ValueError("MACRO_TRANSLATION_SOURCE_FORMAT")
    return fields


def translation_input(source: dict, source_locale: str, target_locale: str) -> tuple[dict, dict]:
    literals, texts = {}, {}
    for path, text in _text_fields(source).items():
        mapping = {}

        def protect(match, literal_mapping=mapping):
            marker = f"__MACRO_LITERAL_{len(literal_mapping):04d}__"
            literal_mapping[marker] = match.group()
            return marker

        texts[path] = LITERAL.sub(protect, text)
        literals[path] = mapping
    return {"source_locale": source_locale, "target_locale": target_locale,
            "source_interpretation": source, "texts": texts}, literals


def _set_text(result: dict, path: str, value: str) -> None:
    parts = path.split(".")
    target = result
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    if isinstance(target, list):
        target[int(parts[-1])] = value
    else:
        target[parts[-1]] = value


def read_translation(raw: str, source: dict, literals: dict) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    value = json.loads(text)
    texts = value.get("texts") if isinstance(value, dict) else None
    if not isinstance(texts, dict) or set(texts) != set(literals):
        raise ValueError("MACRO_TRANSLATION_RESPONSE_FORMAT")
    result = copy.deepcopy(source)
    for path, translated in texts.items():
        mapping = literals[path]
        if (not isinstance(translated, str) or
                (_text_fields(source)[path].strip() and not translated.strip()) or
                set(PLACEHOLDER.findall(translated)) != set(mapping) or
                any(translated.count(marker) != 1 for marker in mapping)):
            raise ValueError("MACRO_TRANSLATION_LITERAL_FORMAT")
        without_markers = PLACEHOLDER.sub("", translated)
        if LITERAL.search(without_markers):
            raise ValueError("MACRO_TRANSLATION_ADDED_LITERAL")
        for marker, literal in mapping.items():
            translated = translated.replace(marker, literal)
        _set_text(result, path, translated)
    return result


def _generate_translation(job: dict, instructions: str, *, timeout: float) -> tuple[dict, dict]:
    source = json.loads(job["source_result_json"])
    with connect(readonly=True) as db:
        row = db.execute("SELECT output_locale FROM macro_interpretations WHERE id=? AND user_id=?",
                         (job["interpretation_id"], job["user_id"])).fetchone()
    if not row:
        raise ValueError("MACRO_TRANSLATION_SOURCE_DELETED")
    supplied, literals = translation_input(source, row["output_locale"], job["response_locale"])
    deadline = monotonic() + timeout
    session = ModelSession(openai_factory=OpenAI)
    try:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Translation setup exceeded its budget")
        context = json.dumps(supplied, ensure_ascii=False, separators=(",", ":"))
        if session.uses_local_agent:
            response = session.analyze_local_agent(instructions=instructions, context=context, tools=[],
                                             tool_handler=None, timeout=remaining)
        else:
            response = session.client.responses.create(
                instructions=instructions, model=session.model, input=[{"role": "user", "content": context}],
                tools=[], tool_choice="none", timeout=remaining, store=False,
            )
        if getattr(response, "status", "completed") not in {None, "completed"}:
            raise ValueError("MACRO_TRANSLATION_RESPONSE_FORMAT")
        result = read_translation(response.output_text, source, literals)
        usage = getattr(response, "usage", None)
        return result, {"provider": session.provider, "model": session.model, "model_requests": 1,
                        "input_tokens": getattr(usage, "input_tokens", None),
                        "output_tokens": getattr(usage, "output_tokens", None)}
    finally:
        session.close()


def _finish_translation(job: dict, *, payload: dict | None = None,
                        execution: dict | None = None, error: tuple[str, str] | None = None) -> bool:
    with connect() as db:
        active = db.execute("SELECT * FROM macro_translation_jobs WHERE id=? AND status='running' "
                            "AND claim_token=? AND lease_until>=?",
                            (job["id"], job["claim_token"], utc_now())).fetchone()
        source = db.execute("SELECT * FROM macro_interpretations WHERE id=? AND user_id=?",
                            (job["interpretation_id"], job["user_id"])).fetchone()
        if not active or not source or _source_hash(source) != job["source_result_sha256"]:
            return False
        now = utc_now()
        if not error:
            # Store only text replacements in a clone of the immutable source,
            # including any historical JSON fields this renderer does not know.
            saved_source = json.loads(job["source_result_json"])
            translated_fields = _text_fields(payload)
            protected_payload = copy.deepcopy(saved_source)
            for path in _text_fields(saved_source):
                _set_text(protected_payload, path, translated_fields[path])
            db.execute(
                "INSERT INTO macro_interpretation_views(id,interpretation_id,user_id,response_locale,render_version,"
                "source_result_sha256,payload_json,execution_json,prompt_artifact_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(interpretation_id,response_locale,render_version) DO NOTHING",
                (new_id("macro_view"), job["interpretation_id"], job["user_id"], job["response_locale"], RENDER_VERSION,
                 job["source_result_sha256"], json.dumps(protected_payload, ensure_ascii=False), json.dumps(execution),
                 job["prompt_artifact_id"], now),
            )
        db.execute("UPDATE macro_translation_jobs SET status=?,completed_at=?,updated_at=?,claim_token=NULL,"
                   "lease_until=NULL,error_code=?,error_message=?,error_retryable=? WHERE id=?",
                   ("failed" if error else "succeeded", now, now, error[0] if error else None,
                    error[1] if error else None, 1 if error else None, job["id"]))
        return True


def run_macro_translation_once() -> bool:
    timeout = min(MAX_TIMEOUT_SECONDS, analysis_timeout_seconds())
    job = claim_macro_translation(timeout=timeout)
    if not job:
        return False
    try:
        with connect(readonly=True) as db:
            bundle = load_prompt_artifact(db, job["prompt_artifact_id"])
        result, execution = _generate_translation(job, bundle.instructions, timeout=timeout)
        execution |= {"prompt_bundle": bundle.metadata(), "prompt_artifact_id": job["prompt_artifact_id"],
                      "output_locale": job["response_locale"], "translation_only": True}
        _finish_translation(job, payload=result, execution=execution)
    except Exception as exc:  # noqa: BLE001 -- never expose model payloads or account details
        code = "MACRO_TRANSLATION_TIMEOUT" if isinstance(exc, (TimeoutError, APITimeoutError)) else "MACRO_TRANSLATION_FAILED"
        if isinstance(exc, ValueError):
            code = "MACRO_TRANSLATION_FORMAT"
        _finish_translation(job, error=(code, localized_task_error(code, job["response_locale"], "")))
    finally:
        from .desktop_updates import finish_desktop_execution

        finish_desktop_execution(job.get("update_execution_id"))
    return True
