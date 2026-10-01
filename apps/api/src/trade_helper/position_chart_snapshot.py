"""Read-only chart evidence from the exact archived analysis snapshot.

This endpoint never fetches newer market data or recalculates archived levels.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256

from fastapi import APIRouter, HTTPException, Response

from .config import local_user_id
from .current_candle import current_candle_context
from .db import connect
from .timeframes import ANALYSIS_TIMEFRAMES, FIXED_SECONDS

router = APIRouter(prefix="/api/v1/analyses", tags=["analysis chart"])
CHART_CANDLE_LIMIT = 300
VERSION = "analysis_chart_snapshot_v1"
CANDLE_FIELDS = ("open_time", "close_time", "open", "high", "low", "close", "volume")


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Snapshot timestamps require a timezone")
    return parsed


def _price(value: str) -> Decimal:
    parsed = Decimal(str(value))
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("Invalid snapshot price")
    return parsed


def _closed_candles(rows: list, timeframe: str, cutoff: datetime) -> list[dict]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("No archived candles")
    step = timedelta(seconds=FIXED_SECONDS[timeframe])
    previous = None
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("Invalid archived candle")
        opened, closed = _time(row["open_time"]), _time(row["close_time"])
        # Earlier local reports used an exact-hour close instead of Binance's
        # final millisecond. Both represent the same completed interval.
        if (opened >= closed or closed >= cutoff or
                abs(closed - opened - step) > timedelta(milliseconds=1) or
                opened.timestamp() % step.total_seconds() != 0 or
                (previous is not None and opened - previous != step)):
            raise ValueError("Archived candles do not match the analysis cutoff")
        op, high, low, close = (_price(row[key]) for key in ("open", "high", "low", "close"))
        if high < max(op, close, low) or low > min(op, close):
            raise ValueError("Invalid archived OHLC")
        if row.get("volume") is not None:
            volume = Decimal(str(row["volume"]))
            if not volume.is_finite() or volume < 0:
                raise ValueError("Invalid archived volume")
        normalized.append({key: row[key] for key in CANDLE_FIELDS if row.get(key) is not None}
                          | {"closed": True})
        previous = opened
    return normalized


def _unavailable(analysis_id: str, reason: str, **metadata) -> dict:
    return {"version": VERSION, "analysis_id": analysis_id, "status": "unavailable",
            "reason": reason, "chart_candles": [], **metadata}


def build_chart_snapshot(row: dict) -> dict:
    """Extract archive evidence without mutating reports or their snapshots."""
    analysis_id = row["id"]
    if row["status"] != "completed":
        return _unavailable(analysis_id, "ANALYSIS_NOT_COMPLETED")
    if not row.get("snapshot_json"):
        return _unavailable(analysis_id, "SNAPSHOT_NOT_SAVED")
    if not row.get("report_json"):
        return _unavailable(analysis_id, "REPORT_NOT_SAVED")
    try:
        snapshot = json.loads(row["snapshot_json"])
        report = json.loads(row["report_json"])
        request = json.loads(row["request_json"])
        if not all(isinstance(value, dict) for value in (snapshot, report, request)):
            raise ValueError("Invalid archive format")
        market_id, timeframe = request["market_id"], request["timeframe"]
        if timeframe not in ANALYSIS_TIMEFRAMES or not isinstance(market_id, str):
            raise ValueError("Unsupported archived market")
        quote = snapshot["quote"]
        report_quote = report["quote"]
        cutoff = _time(quote["observed_at"])
        quote_price = _price(quote["price"])
        fingerprint = sha256(row["snapshot_json"].encode()).hexdigest()
        if (report["market_id"] != market_id or report["timeframe"] != timeframe or
                _time(report_quote["observed_at"]) != cutoff or
                _price(report_quote["price"]) != quote_price or
                (report.get("market_snapshot_sha256") is not None and
                 report["market_snapshot_sha256"] != fingerprint)):
            return _unavailable(analysis_id, "SNAPSHOT_REPORT_MISMATCH")
        candles = _closed_candles(snapshot["candles"], timeframe, cutoff)
        # Only the unfinished candle captured before this quote can be included.
        # The original helper also checks its OHLCV and interval continuity.
        current = current_candle_context(candles, quote, timeframe)
        forming = current["candle"]
        chart_candles = candles[-CHART_CANDLE_LIMIT:]
        if forming is not None:
            chart_candles.append({key: forming[key] for key in CANDLE_FIELDS
                                  if forming.get(key) is not None} | {"closed": False})
        return {
            "version": VERSION, "analysis_id": analysis_id, "status": "ready",
            "market_id": market_id, "timeframe": timeframe, "as_of": quote["observed_at"],
            "quote": {"price": str(quote["price"]), "observed_at": quote["observed_at"]},
            "market_snapshot_sha256": fingerprint, "chart_candles": chart_candles,
            "closed_candle_count": min(len(candles), CHART_CANDLE_LIMIT),
            "available_closed_candle_count": len(candles),
            "forming_candle_status": "available" if forming is not None else "unavailable",
            "data_basis": "stored_analysis_snapshot",
        }
    except (KeyError, ValueError, TypeError, ArithmeticError, AttributeError):
        # Archive corruption is a chart availability issue. Never invalidate an
        # already completed report or expose a stored payload in the response.
        return _unavailable(analysis_id, "SNAPSHOT_INVALID")


@router.get("/{analysis_id}/chart-snapshot")
def get_chart_snapshot(analysis_id: str, response: Response):
    response.headers["Cache-Control"] = "no-store"
    with connect(readonly=True) as db:
        row = db.execute(
            "SELECT id,status,request_json,report_json,snapshot_json FROM analyses "
            "WHERE id=? AND user_id=?", (analysis_id, local_user_id()),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Analysis not found", headers={"Cache-Control": "no-store"})
    return build_chart_snapshot(row)
