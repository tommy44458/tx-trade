"""Explicit SQLite codecs for structured values and UTC observation times."""

import json
from datetime import UTC, datetime


def utc_text(value: datetime) -> str:
    """Fixed-width UTC timestamps retain chronological order in TEXT indexes."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Storage timestamp must include a timezone")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def to_datetime(value: str | datetime) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Invalid stored UTC timestamp")
    return parsed.astimezone(UTC)


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def load_json(value: str | dict | list | None):
    return json.loads(value) if isinstance(value, str) else value


def decode_row(row, *, json_fields=(), time_fields=()) -> dict:
    result = dict(row)
    for field in json_fields:
        if field in result:
            result[field] = load_json(result[field])
    for field in time_fields:
        if field in result and result[field] is not None:
            result[field] = to_datetime(result[field])
    return result
