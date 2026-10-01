"""TypeSafe Jev adapter for licensed, sufficiently detailed news content.

This module does not promote a classification to strategy evidence. A separate
calibrated routing and citation stage is required before the agent sees it.
"""

import hashlib
import json
import math
import os
from collections.abc import Callable

import httpx

from .local_settings import integration_credentials
from .prompts import news_questions, task_prompt_version

API_URL = "https://api.typesafe.ai/v1/systemone"
QUESTION_VERSION = task_prompt_version("news_classification")
MODEL_ALIAS = "jev-latest"
QUESTIONS = news_questions()
# Compatibility exports reference the same centrally registered definitions.
TOPICS = QUESTIONS["topic"]["criteria"]
ASSETS = QUESTIONS["asset_scope"]["criteria"]
NATURES = QUESTIONS["event_nature"]["criteria"]


def _bounded(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Jev probability is not numeric")
    result = float(value)
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise ValueError("Jev probability is outside 0..1")
    return result


def _distribution(answer: dict, names: set[str]) -> None:
    values = answer.get("probabilities")
    if not isinstance(values, dict) or set(values) != names:
        raise ValueError("Jev choice distribution has unexpected labels")
    if not 0.95 <= sum(_bounded(value) for value in values.values()) <= 1.05:
        raise ValueError("Jev choice distribution does not sum to one")
    _bounded(answer.get("confidence"))


def validate_response(data: dict) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("model"), str):
        raise TypeError("Jev response has no model identifier")
    answers = data.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(QUESTIONS):
        raise ValueError("Jev response has missing or extra answers")
    for name, question in QUESTIONS.items():
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != question["type"]:
            raise ValueError("Jev answer type differs from the question")
        if question["type"] == "noul":
            _bounded(answer.get("noul"))
        elif question["type"] == "choice":
            names = set(question["criteria"])
            if answer.get("choice") not in names:
                raise ValueError("Jev chose an unsupported label")
            _distribution(answer, names)
        else:
            score = answer.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or (
                    not math.isfinite(float(score)) or not 0 <= score <= 3):
                raise ValueError("Jev score is invalid")
            _distribution(answer, {"0", "1", "2", "3"})
    usage = data.get("usage")
    if (not isinstance(usage, dict) or any(
            isinstance(usage.get(key), bool) or not isinstance(usage.get(key), int) or
            usage[key] < 0 for key in ("input_tokens", "output_tokens"))):
        raise ValueError("Jev usage is invalid")
    return {"model": data["model"], "answers": answers, "usage": usage}


def classification_input_hash(*, title: str, body: str, source: str) -> str:
    """Fingerprint exactly the text and source submitted to Jev."""
    state = {"title": " ".join(title.split()), "content": " ".join(body.split()),
             "source": source}
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def classify_article(article: dict, *, key: str | None = None,
                     post: Callable | None = None) -> dict:
    """Classify one permitted article; never silently classify headline-only text."""
    if not article.get("model_use_allowed"):
        return {"status": "blocked_rights"}
    title = " ".join(str(article.get("title") or "").split())
    body = " ".join(str(article.get("body") or "").split())
    if (article.get("content_quality") not in {"summary", "body"} or
            len(body) < 80 or body.casefold() == title.casefold()):
        return {"status": "insufficient_text"}
    if len(title) > 300 or len(body) > 6000:
        return {"status": "oversized_text"}
    secret = key if key is not None else (integration_credentials("jev") or {}).get("api_key")
    if not secret:
        return {"status": "disabled"}
    state = {"title": title, "content": body, "source": str(article.get("source") or "")}
    request = {"model": os.getenv("TYPESAFE_MODEL", MODEL_ALIAS),
               "state": state, "questions": QUESTIONS}
    sender = post or httpx.post
    response = sender(API_URL, json=request, headers={"Authorization": f"Bearer {secret}"},
                      timeout=10)
    response.raise_for_status()
    validated = validate_response(response.json())
    digest = classification_input_hash(title=title, body=body, source=state["source"])
    return {"status": "classified_unreviewed", "question_version": QUESTION_VERSION,
            "input_hash": digest, **validated}
