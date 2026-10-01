"""Nonsecret account status for passive UI requests.

This is a display cache, never authorization. Provider sessions must validate
their secure credentials after an explicit login, check or analysis action.
"""

import json
import re

from .db import connect, utc_now
from .local_settings import _ensure_database, _nonsecret_object, data_directory


def _valid_provider(provider: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,79}", provider):
        raise ValueError("Invalid authentication provider")


def _legacy_metadata(provider: str) -> dict:
    try:
        value = json.loads((data_directory() / f"auth-{provider}.json").read_text())
        return _nonsecret_object(value) if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def read_metadata(provider: str) -> dict:
    _valid_provider(provider)
    _ensure_database()
    with connect(readonly=True) as db:
        row = db.execute("SELECT value_json FROM auth_metadata WHERE provider=?", (provider,)).fetchone()
    if row is not None:
        value = json.loads(row["value_json"])
        return value if isinstance(value, dict) else {}
    # Empty metadata is also persisted: a later sign-out must never re-import
    # an old authenticated display cache left as the user's migration backup.
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO auth_metadata(provider,value_json,updated_at) VALUES (?,?,?)",
                   (provider, json.dumps(_legacy_metadata(provider), ensure_ascii=False), utc_now()))
        row = db.execute("SELECT value_json FROM auth_metadata WHERE provider=?", (provider,)).fetchone()
        value = json.loads(row["value_json"])
        return value if isinstance(value, dict) else {}


def write_metadata(provider: str, value: dict) -> None:
    _valid_provider(provider)
    if not isinstance(value, dict):
        raise TypeError("Authentication metadata must be an object")
    _ensure_database()
    with connect() as db:
        db.execute("""INSERT INTO auth_metadata(provider,value_json,updated_at) VALUES (?,?,?)
                      ON CONFLICT(provider) DO UPDATE SET value_json=excluded.value_json,
                      updated_at=excluded.updated_at""",
                   (provider, json.dumps(_nonsecret_object(value), ensure_ascii=False), utc_now()))
