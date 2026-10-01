"""Persist successful exchange snapshots in the same transaction as positions."""

import json

from .config import local_user_id
from .db import connect

ESTIMATE_FIELDS = (
    "exchange_liquidation_price", "mark_price", "unrealized_profit", "exchange_update_time",
)


def save_sync_state(db, provider: str, account_scope: str, summary: dict) -> None:
    db.execute("""INSERT INTO integration_sync_state(
        user_id,provider,account_scope,summary_json,last_success_at) VALUES (?,?,?,?,?)
        ON CONFLICT(user_id,provider,account_scope) DO UPDATE SET
        summary_json=excluded.summary_json,last_success_at=excluded.last_success_at""",
               (local_user_id(), provider, account_scope,
                json.dumps(summary, ensure_ascii=False), summary["synced_at"]))


def last_sync(provider: str, account_scope: str | None = "") -> dict | None:
    if account_scope is None:
        return None
    with connect(readonly=True) as db:
        row = db.execute("""SELECT summary_json FROM integration_sync_state
            WHERE user_id=? AND provider=? AND account_scope=?""",
                         (local_user_id(), provider, account_scope)).fetchone()
    return json.loads(row["summary_json"]) if row else None


def with_exchange_estimates(db, positions: list[dict]) -> list[dict]:
    """Copy estimates into a UI/analysis snapshot, never into core position facts."""
    result = []
    for position in positions:
        item = dict(position)
        row = db.execute("""SELECT e.payload_json,e.updated_at FROM position_exchange_estimates e
            JOIN positions p ON p.id=e.position_id
            WHERE p.id=? AND p.user_id=? AND e.provider=p.source
            AND e.account_scope=p.account_scope""",
                         (item["id"], local_user_id())).fetchone()
        if row:
            estimates = json.loads(row["payload_json"])
            item.update({key: estimates[key] for key in ESTIMATE_FIELDS if key in estimates})
            item["exchange_estimates_at"] = row["updated_at"]
        result.append(item)
    return result
