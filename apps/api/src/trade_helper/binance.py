"""Explicit, signed GET access to ordinary Binance USDT perpetual positions.

Account credentials are loaded only by an explicit invocation. No trading,
withdrawal, transfer, or user-data stream operation exists in this adapter.
"""

import hashlib
import hmac
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import httpx

from .local_settings import integration_credentials, integration_status
from .market_catalog import MarketCatalogUnavailable, get_catalog

FUTURES_BASE_URL = "https://fapi.binance.com"
SPOT_BASE_URL = "https://api.binance.com"
POSITION_ENDPOINT = "/fapi/v3/positionRisk"
CONFIG_ENDPOINT = "/fapi/v1/symbolConfig"
TIME_ENDPOINT = "/fapi/v1/time"
PERMISSIONS_ENDPOINT = "/sapi/v1/account/apiRestrictions"
ACCOUNT_ENDPOINT = "/sapi/v1/account/info"
_ENDPOINTS = {
    POSITION_ENDPOINT: (FUTURES_BASE_URL, True),
    CONFIG_ENDPOINT: (FUTURES_BASE_URL, True),
    TIME_ENDPOINT: (FUTURES_BASE_URL, False),
    PERMISSIONS_ENDPOINT: (SPOT_BASE_URL, True),
    ACCOUNT_ENDPOINT: (SPOT_BASE_URL, True),
}
WRITE_PERMISSION_FLAGS = (
    "enableWithdrawals", "enableInternalTransfer", "enableMargin", "enableFutures",
    "permitsUniversalTransfer", "enableVanillaOptions", "enableFixApiTrade",
    "enableSpotAndMarginTrading", "enablePortfolioMarginTrading",
)
_READ_PERMISSION_FLAGS = {"enableReading", "enableFixReadOnly"}
_ERROR_MESSAGES = {
    "NOT_CONFIGURED": "Configure the optional Binance API key and secret in Settings first.",
    "INVALID_CREDENTIALS": "The stored Binance credentials are incomplete or invalid.",
    "AUTHENTICATION_FAILED": (
        "Binance rejected account access. Check the key, IP restrictions, and reading permission."
    ),
    "TIMEOUT": "Binance did not respond in time. No positions were synchronized.",
    "NETWORK_ERROR": "Binance account access failed. Check the connection and retry later.",
    "RATE_LIMITED": "Binance limited requests. Wait before retrying account access.",
    "IP_BANNED": "Binance temporarily blocked requests from this IP. Retry after the restriction.",
    "TIMESTAMP_ERROR": "Binance rejected the request time after clock synchronization.",
    "SIGNATURE_ERROR": "Binance rejected the request signature. Check the API key and secret.",
    "UPSTREAM_ERROR": "Binance account access is temporarily unavailable.",
    "INVALID_RESPONSE": "Binance returned an invalid response. No positions were synchronized.",
    "INVALID_POSITION": (
        "Binance returned incomplete or inconsistent position data. No positions were synchronized."
    ),
    "INVALID_CONFIG": (
        "Binance returned incomplete or inconsistent contract settings. No positions were synchronized."
    ),
    "MARKET_CATALOG_UNAVAILABLE": (
        "The verified USDT perpetual market catalog is unavailable. No positions were synchronized."
    ),
    "UNSUPPORTED_ENDPOINT": "This Binance account operation is not supported.",
    "UNSUPPORTED_ACCOUNT_MODE": (
        "This account does not use supported ordinary USDⓈ-M futures. Portfolio Margin is not supported."
    ),
    "ACCOUNT_MODE_UNVERIFIED": (
        "Binance account mode could not be verified. No positions were synchronized."
    ),
}


class BinanceError(Exception):
    """Only fixed safe messages and numeric upstream metadata may leave the adapter."""

    def __init__(self, code: str, message: str | None = None, *,
                 exchange_code: int | None = None, retry_after: int | None = None):
        # An exchange's message can contain arbitrary data, including credentials.
        # Accept the familiar constructor shape, but never publish caller-supplied text.
        self.code = code if code in _ERROR_MESSAGES else "UPSTREAM_ERROR"
        self.message = _ERROR_MESSAGES[self.code]
        self.exchange_code = exchange_code if type(exchange_code) is int else None
        self.retry_after = retry_after if type(retry_after) is int and retry_after >= 0 else None
        super().__init__(self.message)

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "exchange_code": self.exchange_code, "retry_after": self.retry_after}


def configured() -> bool:
    """Reading configuration metadata must not decrypt a key."""
    return integration_status("binance").get("configured") is True


def _now() -> str:
    return datetime.now(UTC).isoformat()


def signed_query(secret: str, timestamp_ms: int) -> str:
    """Sign the complete fixed query, without logging or returning account information."""
    query = urlencode({"recvWindow": 5000, "timestamp": timestamp_ms})
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}"


def _credentials(supplied: dict | None) -> dict:
    value = integration_credentials("binance") if supplied is None else supplied
    if value is None:
        raise BinanceError("NOT_CONFIGURED")
    if (not isinstance(value, dict) or
            any(not isinstance(value.get(key), str) or not value[key].strip() or
                not value[key].isascii() or not value[key].isprintable() or
                value[key] != value[key].strip() or any(char.isspace() for char in value[key])
                for key in ("api_key", "api_secret"))):
        raise BinanceError("INVALID_CREDENTIALS")
    # Freeze the two strings instead of retaining a mutable settings dictionary.
    return {"api_key": value["api_key"], "api_secret": value["api_secret"]}


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After", "")
    return int(value) if value.isascii() and value.isdecimal() and len(value) <= 10 else None


def _exchange_error(response: httpx.Response, payload: object) -> BinanceError | None:
    candidate = payload.get("code") if isinstance(payload, dict) else None
    code = candidate if type(candidate) is int and candidate < 0 else None
    if response.status_code == 418:
        name = "IP_BANNED"
    elif response.status_code == 429 or code == -1003:
        name = "RATE_LIMITED"
    elif code == -1021:
        name = "TIMESTAMP_ERROR"
    elif code == -1022:
        name = "SIGNATURE_ERROR"
    elif response.status_code in (401, 403) or code in (-1002, -2014, -2015):
        name = "AUTHENTICATION_FAILED"
    elif response.status_code == 408:
        name = "TIMEOUT"
    elif response.status_code < 200 or response.status_code >= 300 or code is not None:
        name = "UPSTREAM_ERROR"
    else:
        return None
    return BinanceError(name, exchange_code=code, retry_after=_retry_after(response))


class _ReadOnlySession:
    """A single explicit operation shares one clock correction and fixed endpoint allowlist."""

    def __init__(self, credentials: dict | None):
        self.credentials = _credentials(credentials)
        self.offset_ms = 0
        self.clock_calibrated = False

    def _request(self, endpoint: str) -> object:
        endpoint_config = _ENDPOINTS.get(endpoint)
        if endpoint_config is None:
            raise BinanceError("UNSUPPORTED_ENDPOINT")
        host, signed = endpoint_config
        url = f"{host}{endpoint}"
        headers = {"Accept": "application/json"}
        if signed:
            query = signed_query(self.credentials["api_secret"],
                                 int(time.time() * 1000) + self.offset_ms)
            url = f"{url}?{query}"
            headers["X-MBX-APIKEY"] = self.credentials["api_key"]
        try:
            response = httpx.get(url, headers=headers, timeout=12, follow_redirects=False)
        except httpx.TimeoutException:
            raise BinanceError("TIMEOUT") from None
        except httpx.HTTPError:
            raise BinanceError("NETWORK_ERROR") from None
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            error = _exchange_error(response, None)
            raise error or BinanceError("INVALID_RESPONSE") from None
        error = _exchange_error(response, payload)
        if error is not None:
            raise error
        return payload

    def _calibrate_clock(self) -> None:
        self.clock_calibrated = True
        before = int(time.time() * 1000)
        result = self._request(TIME_ENDPOINT)
        after = int(time.time() * 1000)
        server_time = result.get("serverTime") if isinstance(result, dict) else None
        if type(server_time) is not int or server_time <= 0 or server_time > 253402300799999:
            raise BinanceError("INVALID_RESPONSE")
        self.offset_ms = server_time - (before + after) // 2

    def get(self, endpoint: str) -> object:
        try:
            return self._request(endpoint)
        except BinanceError as error:
            if error.code != "TIMESTAMP_ERROR" or self.clock_calibrated:
                raise
        self._calibrate_clock()
        return self._request(endpoint)


def _decimal(value: object, code: str, *, positive: bool = False,
             nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise BinanceError(code)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise BinanceError(code) from None
    if (not number.is_finite() or positive and number <= 0 or
            nonnegative and number < 0):
        raise BinanceError(code)
    return number


def _symbol(value: object, code: str) -> str:
    if (not isinstance(value, str) or not 2 <= len(value) <= 64 or
            not all(character.isalnum() or character == "_" for character in value)):
        raise BinanceError(code)
    return value


def _optional_decimal(row: dict, field: str, *, positive: bool = False,
                      nonnegative: bool = False) -> str | None:
    if row.get(field) in (None, ""):
        return None
    number = _decimal(row[field], "INVALID_POSITION", positive=positive, nonnegative=nonnegative)
    return str(number)


def _exchange_time(value: object) -> str | None:
    if value is None or value == "":
        return None
    milliseconds = _decimal(value, "INVALID_POSITION", nonnegative=True)
    if milliseconds != milliseconds.to_integral_value() or milliseconds > 253402300799999:
        raise BinanceError("INVALID_POSITION")
    if milliseconds == 0:
        return None
    try:
        return (datetime(1970, 1, 1, tzinfo=UTC) +
                timedelta(milliseconds=int(milliseconds))).isoformat()
    except OverflowError:
        raise BinanceError("INVALID_POSITION") from None


def normalize_positions(rows: object, configs: object) -> tuple[list[dict], int, list[str]]:
    """Validate the complete snapshot before callers can write or close any position."""
    if not isinstance(rows, list):
        raise BinanceError("INVALID_RESPONSE")
    if not isinstance(configs, list):
        raise BinanceError("INVALID_CONFIG")
    settings = {}
    for config in configs:
        if not isinstance(config, dict):
            raise BinanceError("INVALID_CONFIG")
        symbol = _symbol(config.get("symbol"), "INVALID_CONFIG")
        if symbol in settings:
            raise BinanceError("INVALID_CONFIG")
        settings[symbol] = config
    try:
        catalog = get_catalog()
    except MarketCatalogUnavailable:
        raise BinanceError("MARKET_CATALOG_UNAVAILABLE") from None
    if catalog.status != "fresh":
        raise BinanceError("MARKET_CATALOG_UNAVAILABLE")
    markets = {market["binance_symbol"]: market for market in catalog.markets}
    result, skipped, seen = [], [], set()
    symbol_modes = {}
    for row in rows:
        if not isinstance(row, dict):
            raise BinanceError("INVALID_POSITION")
        symbol = _symbol(row.get("symbol"), "INVALID_POSITION")
        position_side = row.get("positionSide")
        if position_side not in ("BOTH", "LONG", "SHORT"):
            raise BinanceError("INVALID_POSITION")
        amount = _decimal(row.get("positionAmt"), "INVALID_POSITION")
        identity = f"{symbol}:{position_side}"
        if identity in seen:
            raise BinanceError("INVALID_POSITION")
        seen.add(identity)
        previous_mode = symbol_modes.setdefault(symbol, position_side == "BOTH")
        if previous_mode != (position_side == "BOTH"):
            raise BinanceError("INVALID_POSITION")
        # V3 also returns symbols with only open orders. They are not positions.
        if amount == 0:
            continue
        if ((position_side == "LONG" and amount < 0) or
                (position_side == "SHORT" and amount > 0)):
            raise BinanceError("INVALID_POSITION")
        market = markets.get(symbol)
        if market is None:
            skipped.append(symbol)
            continue
        if row.get("marginAsset", "USDT") != "USDT":
            raise BinanceError("INVALID_POSITION")
        config = settings.get(symbol)
        if config is None:
            raise BinanceError("INVALID_CONFIG")
        leverage = _decimal(config.get("leverage"), "INVALID_CONFIG", positive=True)
        if leverage != leverage.to_integral_value() or leverage > 125:
            raise BinanceError("INVALID_CONFIG")
        margin_type = config.get("marginType")
        if not isinstance(margin_type, str):
            raise BinanceError("INVALID_CONFIG")
        margin_mode = {"CROSSED": "cross", "ISOLATED": "isolated"}.get(margin_type)
        if margin_mode is None:
            raise BinanceError("INVALID_CONFIG")
        liquidation = _optional_decimal(row, "liquidationPrice", nonnegative=True)
        if liquidation is not None and Decimal(liquidation) == 0:
            liquidation = None
        result.append({
            "external_position_id": identity, "market_id": market["id"],
            "exchange_symbol": symbol, "contract_type": "perpetual",
            "side": "long" if amount > 0 else "short", "quantity": str(abs(amount)),
            "entry_price": str(_decimal(row.get("entryPrice"), "INVALID_POSITION", positive=True)),
            "leverage": int(leverage), "margin_mode": margin_mode,
            "exchange_liquidation_price": liquidation, "entry_time": None,
            "mark_price": _optional_decimal(row, "markPrice", positive=True),
            "unrealized_profit": _optional_decimal(row, "unRealizedProfit"),
            "exchange_update_time": _exchange_time(row.get("updateTime")),
        })
    return result, len(skipped), list(dict.fromkeys(skipped))


def _fetch_snapshot(session: _ReadOnlySession) -> dict:
    started_at = _now()
    account = session.get(ACCOUNT_ENDPOINT)
    if (not isinstance(account, dict) or
            any(type(account.get(flag)) is not bool for flag in
                ("isFutureEnabled", "isPortfolioMarginRetailEnabled"))):
        raise BinanceError("ACCOUNT_MODE_UNVERIFIED")
    if account["isPortfolioMarginRetailEnabled"] or not account["isFutureEnabled"]:
        raise BinanceError("UNSUPPORTED_ACCOUNT_MODE")
    rows = session.get(POSITION_ENDPOINT)
    configs = session.get(CONFIG_ENDPOINT)
    positions, unsupported, skipped_symbols = normalize_positions(rows, configs)
    return {"positions": positions, "unsupported": unsupported,
            "skipped_symbols": skipped_symbols, "closure_allowed": unsupported == 0,
            "started_at": started_at, "completed_at": _now()}


def fetch_snapshot(*, credentials: dict | None = None) -> dict:
    """Verify account mode and read both endpoints before returning a complete snapshot."""
    return _fetch_snapshot(_ReadOnlySession(credentials))


def _query_permissions(session: _ReadOnlySession) -> dict:
    payload = session.get(PERMISSIONS_ENDPOINT)
    if not isinstance(payload, dict):
        raise BinanceError("INVALID_RESPONSE")
    reading = payload.get("enableReading")
    reading = reading if type(reading) is bool else None
    writes = [flag for flag in WRITE_PERMISSION_FLAGS if payload.get(flag) is True]
    complete = reading is not None and all(type(payload.get(flag)) is bool
                                          for flag in WRITE_PERMISSION_FLAGS)
    known = set(WRITE_PERMISSION_FLAGS) | _READ_PERMISSION_FLAGS
    unknown_enabled = any(
        isinstance(key, str) and key.startswith(("enable", "permit")) and
        key not in known and value is True for key, value in payload.items()
    )
    verified = complete and not unknown_enabled
    read_only = False if writes or reading is False else True if verified else None
    return {"verified": verified, "read_only": read_only, "reading": reading,
            "write_permissions": writes}


def query_permissions(*, credentials: dict | None = None) -> dict:
    """Checking account access does not establish a key is limited to reading."""
    return _query_permissions(_ReadOnlySession(credentials))


def test_connection(*, credentials: dict | None = None) -> dict:
    """Explicit account test; permission lookup failure cannot invent a read-only verdict."""
    session = _ReadOnlySession(credentials)
    snapshot = _fetch_snapshot(session)
    try:
        permissions = _query_permissions(session)
    except BinanceError as error:
        permissions = {"verified": False, "read_only": None, "reading": None,
                       "write_permissions": [], "error_code": error.code}
    return {"readable": True, "active": len(snapshot["positions"]),
            "active_symbols": sorted({row["exchange_symbol"] for row in snapshot["positions"]}),
            "unsupported": snapshot["unsupported"],
            "started_at": snapshot["started_at"], "completed_at": snapshot["completed_at"],
            "permissions": permissions}
