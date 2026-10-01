"""Persist immutable compiled prompts for queued analyses and conversations.

Private SQLite records contain policy instructions, not model credentials or
market evidence. Reading an old artifact does not consult the current registry.
"""

import json

from .db import new_id, utc_now
from .prompts import PromptBundle
from .prompts.contracts import canonical_json


def save_prompt_artifact(db, bundle: PromptBundle) -> str:
    bundle = PromptBundle.from_dict(bundle.to_dict())
    identifier = new_id("prompt")
    db.execute(
        """INSERT INTO prompt_artifacts(
            id,rendered_sha256,task,prompt_locale,response_locale,policy_version,
            translation_version,schema_version,template_sha256,instructions,metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(rendered_sha256) DO NOTHING""",
        (identifier, bundle.rendered_sha256, bundle.task, bundle.prompt_locale,
         bundle.response_locale, bundle.policy_version, bundle.translation_version,
         bundle.schema_version, bundle.template_sha256, bundle.instructions,
         canonical_json(bundle.metadata()), utc_now()),
    )
    row = db.execute("SELECT id FROM prompt_artifacts WHERE rendered_sha256=?",
                     (bundle.rendered_sha256,)).fetchone()
    if not row:
        raise ValueError("Compiled prompt could not be saved")
    # Reusing a hash must not mask an incomplete or modified stored artifact.
    if load_prompt_artifact(db, row["id"]).to_dict() != bundle.to_dict():
        raise ValueError("Compiled prompt identity is inconsistent")
    return row["id"]


def load_prompt_artifact(db, identifier: str) -> PromptBundle:
    row = db.execute("SELECT * FROM prompt_artifacts WHERE id=?", (identifier,)).fetchone()
    if not row:
        raise ValueError("Compiled prompt artifact is missing")
    try:
        metadata = json.loads(row["metadata_json"])
        bundle = PromptBundle.from_dict(metadata | {"instructions": row["instructions"]})
    except (ValueError, TypeError) as exc:
        raise ValueError("Compiled prompt artifact is unreadable") from exc
    for key in (
        "rendered_sha256", "task", "prompt_locale", "response_locale", "policy_version",
        "translation_version", "schema_version", "template_sha256",
    ):
        if row[key] != getattr(bundle, key):
            raise ValueError("Compiled prompt artifact metadata is inconsistent")
    return bundle
