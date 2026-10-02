"""Optional txinTrade cloud account, signed in from the desktop with Google.

The cloud Worker performs Google sign-in; this app only brings its own PKCE
pair, receives a single-use code on a 127.0.0.1 callback, and redeems it for a
product session. The session token is stored with the local encrypted
credentials and never reaches the renderer. Local analysis does not depend on
being signed in.
"""

import base64
import hashlib
import os
import re
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from fastapi import APIRouter, HTTPException

from .auth_metadata import read_metadata, write_metadata
from .credential_store import (
    CredentialStoreError,
    delete_credentials,
    load_credentials,
    save_credentials,
)

DEFAULT_ORIGIN = "https://api.txintrade.com"
_RECORD = "txintrade_cloud"
_METADATA = "txintrade_cloud"
_LOGIN_SECONDS = 600
_SESSION_TOKEN = re.compile(r"^st_[a-f0-9]{64}$")
_LOCK = threading.RLock()
_pending: "SignIn | None" = None
_last_error: str | None = None
router = APIRouter(prefix="/api/v1/cloud-account", tags=["txinTrade cloud account"])
# The callback page is shown in the system browser, outside the app's language setting.
_PAGE = {
    "signed_in": ("ok", "已登入 txinTrade", "可以關閉此分頁，txinTrade 會自動回到前景。",
                  "Signed in to txinTrade", "You can close this tab; txinTrade will come back to the front."),
    "failed": ("error", "登入未完成", "請回到 txinTrade 查看原因並重試。",
               "Sign-in did not finish", "Return to txinTrade for details and try again."),
    "expired": ("error", "登入連結已失效", "請回到 txinTrade 重新登入。",
                "This sign-in link has expired", "Return to txinTrade and sign in again."),
}
_PAGE_STYLE = (
    ":root{color-scheme:light dark;--bg:#f5f5f7;--card:#fff;--text:#1d1d1f;--muted:#6e6e73;--ok:#1f9d55;--error:#d93025}"
    "@media(prefers-color-scheme:dark){:root{--bg:#151517;--card:#1f1f22;--text:#f5f5f7;--muted:#a1a1a6}}"
    "*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:16px;"
    "background:var(--bg);color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,system-ui,sans-serif}"
    "main{width:min(420px,100%);padding:40px 32px;border-radius:20px;background:var(--card);text-align:center;"
    "box-shadow:0 12px 40px rgba(0,0,0,.08)}"
    ".brand{margin:0 0 28px;font-size:13px;font-weight:600;letter-spacing:.02em;color:var(--muted)}"
    ".mark{width:56px;height:56px;margin:0 auto 20px;border-radius:50%;display:grid;place-items:center;"
    "font-size:28px;font-weight:600;color:#fff}.ok .mark{background:var(--ok)}.error .mark{background:var(--error)}"
    "h1{margin:0 0 6px;font-size:21px;font-weight:600}p{margin:0;color:var(--muted)}"
    ".en{margin-top:24px;padding-top:20px;border-top:1px solid rgba(128,128,128,.2)}.en h1{font-size:15px}"
)


def _page(kind: str) -> str:
    tone, title, body, title_en, body_en = _PAGE[kind]
    return (f"<main class='{tone}'><p class='brand'>txinTrade</p>"
            f"<div class='mark' aria-hidden='true'>{'✓' if tone == 'ok' else '!'}</div>"
            f"<h1>{title}</h1><p>{body}</p>"
            f"<div class='en' lang='en'><h1>{title_en}</h1><p>{body_en}</p></div></main>")


class CloudAccountError(RuntimeError):
    """A stable error code for the interface to translate; never tokens or response bodies."""


def cloud_origin() -> str:
    """Production uses HTTPS; a loopback origin is accepted only for local development."""
    origin = os.getenv("TXINTRADE_CLOUD_ORIGIN", DEFAULT_ORIGIN).rstrip("/")
    parsed = urlsplit(origin)
    loopback = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1"}
    if (parsed.scheme != "https" and not loopback) or parsed.path or parsed.query or parsed.username:
        raise CloudAccountError("misconfigured")
    return origin


@dataclass
class SignIn:
    state: str
    verifier: str
    origin: str
    deadline: float
    server: HTTPServer
    timer: threading.Timer | None = None
    finished: bool = False


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _stop(attempt: SignIn) -> None:
    attempt.finished = True
    if attempt.timer:
        attempt.timer.cancel()
    # shutdown() waits for serve_forever, so it must not run on the server thread.
    threading.Thread(target=attempt.server.shutdown, daemon=True).start()


def cancel_sign_in(message: str | None = None) -> None:
    global _pending, _last_error
    with _LOCK:
        if _pending is not None:
            _stop(_pending)
            _pending = None
        _last_error = message


def _expire(attempt: SignIn) -> None:
    with _LOCK:
        if _pending is attempt:
            cancel_sign_in("timeout")


def session_record() -> dict | None:
    record = load_credentials(_RECORD, allow_interaction=True)
    if not record or not _SESSION_TOKEN.match(str(record.get("session_token", ""))):
        return None
    if not isinstance(record.get("expires_at"), int) or record["expires_at"] <= time.time() * 1000:
        return None
    # A session belongs to the cloud it was issued by; after the app is pointed at
    # another cloud (for example from development to production), sign in again.
    try:
        if record.get("origin") != cloud_origin():
            return None
    except CloudAccountError:
        return None
    return record


def status() -> dict:
    with _LOCK:
        pending = _pending is not None and not _pending.finished
        error = _last_error
    try:
        origin = cloud_origin()
    except CloudAccountError as exc:
        return {"configured": False, "signed_in": False, "pending": False, "error": str(exc), "profile": None,
                "expires_at": None}
    metadata = read_metadata(_METADATA)
    signed_in = bool(metadata.get("signed_in")) and isinstance(metadata.get("expires_at"), int) \
        and metadata["expires_at"] > time.time() * 1000 and metadata.get("origin") == origin
    profile = {key: metadata.get(key) for key in ("email", "display_name", "picture_url")} if signed_in else None
    return {"configured": True, "origin": origin, "signed_in": signed_in, "pending": pending,
            "error": error, "profile": profile,
            "expires_at": metadata.get("expires_at") if signed_in else None}


def signed_in_profile() -> dict | None:
    """Display-only account for the sidebar; never authorization."""
    try:
        return status().get("profile")
    except (CredentialStoreError, ValueError):
        return None


def _redeem(attempt: SignIn, code: str) -> None:
    with httpx.Client(timeout=15) as client:
        response = client.post(f"{attempt.origin}/auth/desktop/token",
                               json={"code": code, "code_verifier": attempt.verifier})
        if response.status_code != 201:
            raise CloudAccountError("rejected")
        session = response.json()
        token, expires_at = session.get("session_token"), session.get("expires_at")
        if not isinstance(token, str) or not _SESSION_TOKEN.match(token) or not isinstance(expires_at, int):
            raise CloudAccountError("invalid_response")
        me = client.get(f"{attempt.origin}/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        if me.status_code != 200:
            raise CloudAccountError("invalid_response")
        account = me.json()
    profile = account.get("profile") if isinstance(account.get("profile"), dict) else {}
    text = lambda value, size: value[:size] if isinstance(value, str) else None
    picture = text(profile.get("picture_url"), 2048)
    save_credentials(_RECORD, {"session_token": token, "expires_at": expires_at,
                               "account_id": text(account.get("account_id"), 200), "origin": attempt.origin},
                     allow_interaction=True)
    write_metadata(_METADATA, {
        "signed_in": True, "expires_at": expires_at, "origin": attempt.origin,
        "email": text(profile.get("email"), 320), "display_name": text(profile.get("display_name"), 200),
        "picture_url": picture if picture and picture.startswith("https://") else None,
    })


def _callback(query: dict[str, list[str]]) -> tuple[int, str]:
    global _pending, _last_error
    value = lambda key: (query.get(key) or [""])[0]
    with _LOCK:
        attempt = _pending
        # Only the pending attempt's state, compared in constant time, may finish sign-in.
        if attempt is None or attempt.finished or not secrets.compare_digest(value("state"), attempt.state):
            return 400, _page("expired")
        attempt.finished = True
    try:
        if value("error"):
            raise CloudAccountError("cancelled")
        code = value("code")
        if not re.fullmatch(r"[a-f0-9]{64}", code):
            raise CloudAccountError("invalid_response")
        _redeem(attempt, code)
        with _LOCK:
            if _pending is attempt:
                _pending, _last_error = None, None
                _stop(attempt)
        # A remote-access link waiting for sign-in reconnects now, not on its next hourly check.
        from .cloud_connector import connector
        connector.wake()
        return 200, _page("signed_in")
    except (CloudAccountError, CredentialStoreError, httpx.HTTPError, ValueError) as exc:
        code = str(exc) if isinstance(exc, CloudAccountError) else \
            "storage_failed" if isinstance(exc, CredentialStoreError) else "service_unavailable"
        with _LOCK:
            if _pending is attempt:
                cancel_sign_in(code)
        return 400, _page("failed")


class _CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        # The single-use code and state must not appear in access logs.
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        code, message = _callback(parse_qs(parsed.query, keep_blank_values=True))
        document = ("<!doctype html><html lang='zh-Hant'><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                    f"<title>txinTrade</title><style>{_PAGE_STYLE}</style><body>{message}</body></html>").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(document)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(document)


def start_sign_in() -> dict:
    global _pending, _last_error
    origin = cloud_origin()
    with _LOCK:
        if _pending is not None:
            cancel_sign_in()
        server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        verifier = secrets.token_urlsafe(64)
        attempt = SignIn(state=secrets.token_urlsafe(32), verifier=verifier, origin=origin,
                         deadline=time.monotonic() + _LOGIN_SECONDS, server=server)
        _pending, _last_error = attempt, None
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        attempt.timer = threading.Timer(_LOGIN_SECONDS, _expire, args=(attempt,))
        attempt.timer.daemon = True
        attempt.timer.start()
        query = urlencode({"client": "desktop", "code_challenge": _challenge(verifier),
                           "state": attempt.state, "port": server.server_port})
        return {"start_url": f"{origin}/auth/google/start?{query}", "status": status()}


def sign_out() -> dict:
    cancel_sign_in()
    record = None
    try:
        record = session_record()
    except CredentialStoreError:
        pass
    # Remote access belongs to this account: stop it and revoke this device first.
    from .cloud_connector import forget_device

    forget_device(record)
    if record:
        try:
            # Best effort: the cloud revokes the session; local removal happens regardless.
            httpx.post(f"{record.get('origin') or cloud_origin()}/auth/logout",
                       headers={"Authorization": f"Bearer {record['session_token']}"}, timeout=10)
        except (httpx.HTTPError, CloudAccountError):
            pass
    delete_credentials(_RECORD)
    write_metadata(_METADATA, {"signed_in": False})
    return status()


def _http_error(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


@router.get("")
def read_status() -> dict:
    return status()


@router.post("/sign-in")
def begin() -> dict:
    try:
        return start_sign_in()
    except (CloudAccountError, OSError) as exc:
        raise _http_error(exc if isinstance(exc, CloudAccountError)
                          else CloudAccountError("start_failed")) from None


@router.post("/sign-in/cancel")
def cancel() -> dict:
    cancel_sign_in()
    return status()


@router.post("/sign-out")
def end() -> dict:
    try:
        return sign_out()
    except CredentialStoreError:
        raise _http_error(CloudAccountError("storage_failed")) from None
