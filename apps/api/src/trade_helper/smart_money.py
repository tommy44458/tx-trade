"""Smart money: on-chain fund flows read from the txinTrade cloud.

The cloud watches Bitcoin and Ethereum around the clock and publishes the same data
to everyone, without sign-in. This router only relays it to the page, caches it for a
minute, and keeps the last good answer for when the cloud cannot be reached.
"""

import re
import threading
import time

import httpx
from fastapi import APIRouter, HTTPException, Query

from .cloud_account import CloudAccountError, cloud_origin

router = APIRouter(prefix="/api/v1/smart-money", tags=["smart money"])

CACHE_SECONDS = 60
MAX_BYTES = 2_000_000
WINDOWS = ("1d", "7d", "30d", "90d")
_ASSET = re.compile(r"^[A-Z0-9]{2,12}$")
_LOCK = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}


def _fetch(path: str) -> dict:
    """The cloud's answer for `path`, fresh within a minute, else the last good one marked stale."""
    now = time.monotonic()
    with _LOCK:
        cached = _cache.get(path)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        with httpx.Client(timeout=15) as client:
            response = client.get(f"{cloud_origin()}{path}", headers={"Accept": "application/json"})
        if response.status_code == 404:
            raise HTTPException(404, {"code": "asset_not_tracked"})
        response.raise_for_status()
        if len(response.content) > MAX_BYTES:
            raise ValueError("response too large")
        body = response.json()
        if not isinstance(body, dict):
            raise TypeError("unexpected response")
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, TypeError, CloudAccountError):
        if cached:
            return {**cached[1], "stale": True}
        raise HTTPException(503, {"code": "smart_money_unavailable"}) from None
    with _LOCK:
        _cache[path] = (now, body)
    return body


@router.get("/overview")
def overview() -> dict:
    return _fetch("/smart-money/overview")


@router.get("/assets")
def assets() -> dict:
    return _fetch("/smart-money/assets")


@router.get("/assets/{asset}")
def asset(asset: str, window: str = Query("1d")) -> dict:
    if not _ASSET.fullmatch(asset):
        raise HTTPException(404, {"code": "asset_not_tracked"})
    if window not in WINDOWS:
        raise HTTPException(422, {"code": "invalid_window"})
    return _fetch(f"/smart-money/assets/{asset}?window={window}")


def clear_cache() -> None:
    with _LOCK:
        _cache.clear()
