"""Fund flows for analyses and follow-up questions.

Analyses get a compact summary of the cloud's on-chain monitor: BTC, ETH and
stablecoins always, and the analyzed pair's coin when the cloud tracks it. It is
built by code, not by a model, so it adds no model call and only a cached request.
A follow-up conversation on the fund-flows page is anchored on a snapshot of the
same data, frozen when the conversation starts.
"""

import json
import os
import re
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .config import local_user_id
from .db import connect, new_id, utc_now
from .smart_money import WINDOWS, fetch_cloud

VERSION = "fund_flows_context_v1"
SOURCE = "txinTrade cloud on-chain monitor (mempool.space, public Ethereum nodes, Blockscout labels, DefiLlama)"
REFERENCE_ASSETS = ("BTC", "ETH")
# The monitor runs every five minutes; thirty quiet minutes means it is behind.
STALE_AFTER = timedelta(minutes=30)
EVENTS_IN_ANALYSIS = 5
EVENTS_IN_SNAPSHOT = 30
router = APIRouter(prefix="/api/v1/smart-money/snapshots", tags=["smart money"])


def asset_for_market(market_id: str) -> str:
    """The coin behind a market: binance:perp:1000PEPEUSDT trades PEPE."""
    symbol = market_id.rsplit(":", 1)[-1]
    return re.sub(r"^1000+(?=[A-Z])", "", re.sub(r"(USDT|USDC)$", "", symbol))


def _iso(ms: int | None) -> str | None:
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat() if ms else None


def _usd(value) -> int:
    return round(float(value or 0))


def _amount(value) -> float:
    value = float(value or 0)
    return round(value, 2 if abs(value) >= 1 else 6)


def _totals(t: dict) -> dict:
    return {"inflow": _amount(t["inflow"]), "outflow": _amount(t["outflow"]),
            "net": _amount(t["inflow"] - t["outflow"]),
            "inflow_usd": _usd(t["inflow_usd"]), "outflow_usd": _usd(t["outflow_usd"]),
            "net_usd": _usd(t["inflow_usd"] - t["outflow_usd"]),
            "whale_count": int(t.get("whale_count") or 0), "whale_usd": _usd(t.get("whale_usd"))}


def _daily_net_usd(series: dict) -> list[dict]:
    """Hourly points folded into UTC days, oldest first: the multi-day trend."""
    days: dict[str, int] = {}
    for point in series.get("points", []):
        day = datetime.fromtimestamp(point["t"] / 1000, UTC).date().isoformat()
        days[day] = days.get(day, 0) + _usd(point["inflow_usd"]) - _usd(point["outflow_usd"])
    return [{"date": day, "net_usd": value} for day, value in sorted(days.items())]


def _events(events: list[dict], limit: int) -> list[dict]:
    largest = sorted(events, key=lambda e: e.get("usd") or 0, reverse=True)[:limit]
    return [{"kind": e["kind"], "amount": _amount(e["amount"]), "usd": _usd(e["usd"]),
             "from": e.get("from_entity") or "unlabelled wallet",
             "to": e.get("to_entity") or "unlabelled wallet", "at": _iso(e["ts"])}
            for e in largest]


def _last_day(series: dict, events: list[dict], generated_at: int) -> dict:
    """Twenty-four hours from a seven-day answer: the last 24 hourly points."""
    points = series.get("points", [])[-24:]
    since = generated_at - 86_400_000
    whales = [e for e in events if e["kind"] == "whale" and e["ts"] >= since]
    return _totals({
        "inflow": sum(p["inflow"] for p in points), "outflow": sum(p["outflow"] for p in points),
        "inflow_usd": sum(p["inflow_usd"] for p in points),
        "outflow_usd": sum(p["outflow_usd"] for p in points),
        "whale_count": len(whales), "whale_usd": sum(e["usd"] for e in whales),
    })


def _sync_status(overview: dict) -> tuple[str, str | None]:
    times = [s.get("updated_at") for s in (overview.get("sync") or {}).values() if s]
    latest = max((t for t in times if t), default=None)
    if overview.get("stale") or latest is None:
        return "stale", _iso(latest)
    behind = datetime.now(UTC) - datetime.fromtimestamp(latest / 1000, UTC) > STALE_AFTER
    return ("stale" if behind else "available"), _iso(latest)


def _stablecoins(overview: dict) -> dict:
    stable = overview.get("stablecoins") or {}
    exchange = stable.get("exchange") or {}
    return {"total_supply_usd": _usd(stable.get("total_usd")),
            "supply_change_1d_usd": _usd(stable.get("change_1d")),
            "supply_change_7d_usd": _usd(stable.get("change_7d")),
            "exchange_net_24h_usd": _usd((exchange.get("1d") or {}).get("net_usd")),
            "exchange_net_7d_usd": _usd((exchange.get("7d") or {}).get("net_usd"))}


def _reference(summary: dict, events_limit: int) -> dict:
    return {"coverage": summary["coverage"], "24h": _totals(summary["1d"]), "7d": _totals(summary["7d"]),
            "daily_net_usd": _daily_net_usd(summary["series"]),
            "largest_24h": _events(summary.get("events") or [], events_limit)}


def _asset_detail(detail: dict, events_limit: int) -> dict:
    events = detail.get("events") or []
    return {"coverage": detail["coverage"],
            "24h": _last_day(detail["series"], events, detail["generated_at"]),
            "7d": _totals(detail["totals"]), "daily_net_usd": _daily_net_usd(detail["series"]),
            "largest_7d": _events(events, events_limit)}


def unavailable_fund_flows(market_id: str) -> dict:
    return {"version": VERSION, "source": SOURCE, "status": "unavailable",
            "pair_asset": {"asset": asset_for_market(market_id), "status": "unavailable"}}


def fund_flows_context(market_id: str) -> dict:
    """What the analysis sees; never raises, so a cloud outage only marks the data unavailable."""
    pair = asset_for_market(market_id)
    if os.getenv("TRADE_FUND_FLOWS_ENABLED", "1") == "0":
        return unavailable_fund_flows(market_id)
    try:
        overview = fetch_cloud("/smart-money/overview")
        status, synced_at = _sync_status(overview)
        context = {
            "version": VERSION, "source": SOURCE, "status": status,
            "as_of": _iso(overview["generated_at"]), "synced_at": synced_at,
            "units": "inflow/outflow/net in the coin's own units; *_usd in US dollars at recording time",
            "assets": {s["asset"]: _reference(s, EVENTS_IN_ANALYSIS) for s in overview["assets"]
                       if s["asset"] in REFERENCE_ASSETS},
            "stablecoins": _stablecoins(overview),
        }
    except (HTTPException, KeyError, TypeError, ValueError):
        return unavailable_fund_flows(market_id)
    if pair in REFERENCE_ASSETS:
        context["pair_asset"] = {"asset": pair, "status": "same_as_reference"}
        return context
    try:
        detail = fetch_cloud(f"/smart-money/assets/{pair}?window=7d")
        context["pair_asset"] = {"asset": pair, "status": "tracked", **_asset_detail(detail, EVENTS_IN_ANALYSIS)}
    except HTTPException as exc:
        missing = exc.status_code == 404
        context["pair_asset"] = {"asset": pair, "status": "not_tracked" if missing else "unavailable"}
    except (KeyError, TypeError, ValueError):
        context["pair_asset"] = {"asset": pair, "status": "unavailable"}
    return context


# ---- Snapshots for follow-up questions ---------------------------------------------

class SnapshotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset: str = Field(pattern=r"^[A-Z0-9]{2,12}$")
    window: str = Field(pattern="^(" + "|".join(WINDOWS) + ")$")


def _public(row) -> dict:
    return {"id": row["id"], "asset": row["asset"], "window": row["flow_window"],
            "as_of": row["generated_at"], "created_at": row["created_at"]}


@router.post("", status_code=201)
def create_snapshot(body: SnapshotInput) -> dict:
    """Freeze the fund flows on screen as the basis of a new conversation."""
    overview = fetch_cloud("/smart-money/overview")
    detail = fetch_cloud(f"/smart-money/assets/{body.asset}?window={body.window}")
    status, synced_at = _sync_status(overview)
    payload = {
        "version": VERSION, "source": SOURCE, "status": status,
        "as_of": _iso(overview["generated_at"]), "synced_at": synced_at,
        "units": "inflow/outflow/net in the coin's own units; *_usd in US dollars at recording time",
        "overview": {s["asset"]: _reference(s, 10) for s in overview["assets"]},
        "stablecoins": _stablecoins(overview),
        "focus": {"asset": body.asset, "window": body.window, "coverage": detail["coverage"],
                  "totals": _totals(detail["totals"]), "daily_net_usd": _daily_net_usd(detail["series"]),
                  "largest_movements": _events(detail.get("events") or [], EVENTS_IN_SNAPSHOT)},
    }
    row_id, now = new_id("flow"), utc_now()
    with connect() as db:
        db.execute(
            "INSERT INTO fund_flow_snapshots(id,user_id,asset,flow_window,generated_at,payload_json,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (row_id, local_user_id(), body.asset, body.window, payload["as_of"],
             json.dumps(payload, ensure_ascii=False), now),
        )
        row = db.execute("SELECT * FROM fund_flow_snapshots WHERE id=?", (row_id,)).fetchone()
    return _public(row)


@router.get("/latest")
def latest_snapshot() -> dict:
    """The conversation to show on the page: the most recently started one."""
    with connect(readonly=True) as db:
        row = db.execute(
            "SELECT * FROM fund_flow_snapshots WHERE user_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
            (local_user_id(),),
        ).fetchone()
    return {"snapshot": _public(row) if row else None}
