"""Import an explicitly requested complete Binance account snapshot atomically."""

import json
from datetime import datetime
from decimal import Decimal

from . import binance, local_settings
from .config import local_user_id
from .db import connect, new_id, utc_now
from .integration_sync_state import ESTIMATE_FIELDS, save_sync_state

EXCHANGE_FIELDS = (
    "market_id", "side", "leverage", "margin_mode", "entry_price", "quantity",
    "entry_time", "exchange_symbol", "contract_type",
)


class BinanceAccountChangedError(Exception):
    """An old network response must not write after changing the saved account."""


class BinanceSyncSupersededError(Exception):
    """A newer complete snapshot already committed while this read was in flight."""


def _decimal_text(value: str) -> str:
    # Decimal.normalize() can round to the current precision. Formatting first
    # removes redundant fractional zeros without changing any numeric digit.
    text = format(Decimal(value), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _same_facts(old: dict, item: dict) -> bool:
    return all(
        Decimal(old[field]) == Decimal(item[field])
        if field in {"entry_price", "quantity"} else old[field] == item[field]
        for field in EXCHANGE_FIELDS
    )


def sync_positions() -> dict:
    credentials, scope = local_settings.binance_sync_credentials()
    # No writer lock is held while fetching or normalizing the exchange data.
    # The adapter checks all required endpoints before returning a snapshot.
    snapshot = binance.fetch_snapshot(credentials=credentials)
    normalized = []
    seen = set()
    for position in snapshot["positions"]:
        item = dict(position)
        key = f"{scope}:{item['external_position_id']}"
        if key in seen:
            raise ValueError("Binance snapshot contains duplicate position identities")
        seen.add(key)
        item["external_position_id"] = key
        for field in ("entry_price", "quantity"):
            item[field] = _decimal_text(item[field])
        normalized.append(item)
    now, user_id = utc_now(), local_user_id()
    with connect() as db:
        if not local_settings.binance_connection_is_current(db, credentials, scope):
            raise BinanceAccountChangedError("Binance account changed; synchronize again")
        previous = db.execute("""SELECT summary_json FROM integration_sync_state
            WHERE user_id=? AND provider='binance' AND account_scope=?""",
                              (user_id, scope)).fetchone()
        if previous:
            previous_start = json.loads(previous["summary_json"]).get("started_at")
            if previous_start and (datetime.fromisoformat(snapshot["started_at"]) <
                                   datetime.fromisoformat(previous_start)):
                raise BinanceSyncSupersededError("A newer Binance synchronization has completed")
        existing = {
            row["external_position_id"]: dict(row)
            for row in db.execute("""SELECT * FROM positions
                WHERE user_id=? AND source='binance' AND account_scope=?""",
                                  (user_id, scope)).fetchall()
        }
        created = updated = closed = 0
        for item in normalized:
            key = item["external_position_id"]
            old = existing.get(key)
            if old is None:
                position_id = new_id("pos")
                db.execute("""INSERT INTO positions(
                    id,user_id,market_id,version,side,leverage,margin_mode,entry_price,quantity,
                    entry_time,status,created_at,updated_at,source,external_position_id,
                    exchange_symbol,contract_type,synced_at,account_scope)
                    VALUES(?,?,?,1,?,?,?,?,?,?,'open',?,?,'binance',?,?,?,?,?)""",
                           (position_id, user_id, item["market_id"], item["side"],
                            item["leverage"], item["margin_mode"], item["entry_price"],
                            item["quantity"], item["entry_time"], now, now, key,
                            item["exchange_symbol"], item["contract_type"], now, scope))
                created += 1
            else:
                position_id = old["id"]
                if old["status"] != "open" or not _same_facts(old, item):
                    db.execute("""UPDATE positions SET
                        market_id=?,side=?,leverage=?,margin_mode=?,entry_price=?,quantity=?,
                        entry_time=?,exchange_symbol=?,contract_type=?,status='open',
                        version=version+1,updated_at=?,synced_at=? WHERE id=?""",
                               tuple(item[field] for field in EXCHANGE_FIELDS) +
                               (now, now, position_id))
                    updated += 1
                else:
                    db.execute("UPDATE positions SET synced_at=? WHERE id=?", (now, position_id))
            estimates = {field: item.get(field) for field in ESTIMATE_FIELDS}
            db.execute("""INSERT INTO position_exchange_estimates(
                position_id,provider,account_scope,payload_json,updated_at)
                VALUES (?,'binance',?,?,?) ON CONFLICT(position_id) DO UPDATE SET
                payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                       (position_id, scope, json.dumps(estimates), now))
        closure_allowed = snapshot.get("closure_allowed") is True
        if closure_allowed:
            for key, old in existing.items():
                if key not in seen and old["status"] == "open":
                    db.execute("""UPDATE positions SET status='closed',version=version+1,
                        updated_at=?,synced_at=? WHERE id=?""", (now, now, old["id"]))
                    closed += 1
        summary = {
            "created": created, "updated": updated, "closed": closed,
            "active": len(normalized),
            "active_symbols": sorted({item["exchange_symbol"] for item in normalized}),
            "unsupported": snapshot.get("unsupported", 0),
            "skipped_symbols": snapshot.get("skipped_symbols", []),
            "closure_allowed": closure_allowed, "synced_at": now,
            "started_at": snapshot["started_at"], "completed_at": snapshot["completed_at"],
        }
        save_sync_state(db, "binance", scope, summary)
    return summary
