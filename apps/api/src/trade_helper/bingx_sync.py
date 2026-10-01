"""Persist complete BingX snapshots without touching manual positions."""

from .bingx import fetch_positions, normalize_positions
from .config import local_user_id
from .db import connect, new_id, utc_now
from .integration_sync_state import save_sync_state

EXCHANGE_FIELDS = (
    "market_id", "side", "leverage", "margin_mode", "entry_price", "quantity",
    "exchange_liquidation_price", "entry_time", "exchange_symbol", "contract_type",
)


def sync_positions() -> dict:
    # Fetch and validate both endpoints before changing the database. An API
    # failure must never make an existing position look closed.
    normalized = []
    skipped = 0
    for kind in ("perpetual", "standard"):
        rows, count = normalize_positions(kind, fetch_positions(kind))
        normalized.extend(rows)
        skipped += count
    now, user_id = utc_now(), local_user_id()
    with connect() as db:
        existing = {
            row["external_position_id"]: dict(row)
            for row in db.execute(
                "SELECT * FROM positions WHERE user_id=? AND source='bingx'", (user_id,)
            ).fetchall()
        }
        seen = set()
        created = updated = closed = 0
        for item in normalized:
            key = item["external_position_id"]
            seen.add(key)
            old = existing.get(key)
            if old is None:
                db.execute(
                    "INSERT INTO positions(id,user_id,market_id,version,side,leverage,margin_mode,entry_price,quantity,stop_loss,take_profit,entry_time,exchange_liquidation_price,notes,status,created_at,updated_at,source,external_position_id,exchange_symbol,contract_type,synced_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (new_id("pos"), user_id, item["market_id"], 1, item["side"], item["leverage"], item["margin_mode"], item["entry_price"], item["quantity"], None, None, item["entry_time"], item["exchange_liquidation_price"], None, "open", now, now, "bingx", key, item["exchange_symbol"], item["contract_type"], now),
                )
                created += 1
            elif old["status"] != "open" or any(old[field] != item[field] for field in EXCHANGE_FIELDS):
                db.execute(
                    "UPDATE positions SET market_id=?,side=?,leverage=?,margin_mode=?,entry_price=?,quantity=?,entry_time=?,exchange_liquidation_price=?,exchange_symbol=?,contract_type=?,status='open',version=version+1,updated_at=?,synced_at=? WHERE id=?",
                    (item["market_id"], item["side"], item["leverage"], item["margin_mode"], item["entry_price"], item["quantity"], item["entry_time"], item["exchange_liquidation_price"], item["exchange_symbol"], item["contract_type"], now, now, old["id"]),
                )
                updated += 1
            else:
                db.execute("UPDATE positions SET synced_at=? WHERE id=?", (now, old["id"]))
        for key, old in existing.items():
            if key not in seen and old["status"] == "open":
                db.execute(
                    "UPDATE positions SET status='closed',version=version+1,updated_at=?,synced_at=? WHERE id=?",
                    (now, now, old["id"]),
                )
                closed += 1
        summary = {"created": created, "updated": updated, "closed": closed,
                   "active": len(normalized),
                   "active_symbols": sorted({item["exchange_symbol"] for item in normalized}),
                   "unsupported": skipped, "synced_at": now}
        save_sync_state(db, "bingx", "", summary)
        db.commit()
    return summary
