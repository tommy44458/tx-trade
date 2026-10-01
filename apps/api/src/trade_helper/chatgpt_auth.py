"""App-specific ChatGPT plan OAuth. Codex credentials are never read or copied.

The loopback callback runs independently of FastAPI's desktop-session header.
Only an unexpired, one-time state/PKCE transaction may redeem an OAuth code.
"""

import base64
import hashlib
import os
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import jwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .auth_metadata import read_metadata, write_metadata
from .credential_store import CredentialStoreError, load_credentials, save_credentials

ISSUER = "https://auth.openai.com"
AUTHORIZE_ENDPOINT = ISSUER + "/api/accounts/authorize"
TOKEN_ENDPOINT = ISSUER + "/api/accounts/oauth/token"
JWKS_ENDPOINT = ISSUER + "/.well-known/jwks.json"
RESOURCE = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
_RECORD = "chatgpt"
_LOGIN_SECONDS = 600
_STATE_LOCK = threading.RLock()
_STORAGE_LOCK = threading.RLock()
_pending = None
_last_error: str | None = None
_jwks_cache: tuple[float, dict] | None = None
router = APIRouter(prefix="/api/v1/auth/chatgpt", tags=["ChatGPT authorization"])


class ChatGPTAuthError(RuntimeError):
    """Safe, actionable messages; never provider response bodies or tokens."""

    def __init__(self, message: str, *, reauthorize: bool = False):
        super().__init__(message)
        self.reauthorize = reauthorize


@dataclass
class LoginAttempt:
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    client_id: str
    host_id: str
    account_id: str | None
    deadline: float
    server: HTTPServer
    timer: threading.Timer | None = None
    consumed: bool = False
    cancelled: bool = False


def _lock_path() -> Path:
    root = os.environ.get("APP_DATA_DIR") or str(
        Path.home() / "Library" / "Application Support" / "txTrade")
    path = Path(root) / "locks" / "chatgpt.lock"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


@contextmanager
def _storage_transaction():
    # The API and analysis worker share rotating refresh tokens. Serialize both.
    with _STORAGE_LOCK:
        descriptor = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _registry(*, allow_interaction: bool = False) -> dict:
    record = load_credentials(_RECORD, allow_interaction=allow_interaction)
    if record is None:
        return {"host_id": "urn:uuid:" + str(uuid4()), "accounts": {}, "active_account_id": None}
    if not isinstance(record.get("accounts"), dict) or not isinstance(record.get("host_id"), str):
        raise ChatGPTAuthError("ChatGPT 登入設定無法讀取；請重新授權。")
    return record


def _active(record: dict) -> dict | None:
    return record["accounts"].get(record.get("active_account_id"))


def _plan_enabled(account: dict | None) -> bool:
    return bool(account and {"chatgpt.tokens.use.direct", "resource.invoke"}.issubset(
        set(account.get("scopes", []))))


def _authenticated(account: dict | None) -> bool:
    return bool(account and account.get("access_token") and (
        account.get("expires_at", 0) > time.time() or account.get("refresh_token")))


def _cache_registry(record: dict) -> None:
    # Persist only display metadata. Access, refresh and identity tokens remain
    # exclusively in the credential store, as do the OAuth registration grants.
    previous = read_metadata("chatgpt")
    write_metadata("chatgpt", {
        "status_known": True,
        "active_account_id": record.get("active_account_id"),
        "accounts": [{
            "id": identifier, "email": saved.get("email"),
            "label": (saved.get("email") or "ChatGPT") + " · " + identifier[:8],
            "authenticated": _authenticated(saved),
            "plan_usage_enabled": _plan_enabled(saved),
            "expires_at": saved.get("expires_at"),
            "can_refresh": bool(saved.get("refresh_token")),
        } for identifier, saved in record["accounts"].items()],
        "models": previous.get("models", []) if previous.get("active_account_id")
        == record.get("active_account_id") else [],
    })


def _save_registry(record: dict) -> None:
    save_credentials(_RECORD, record)
    _cache_registry(record)


def status() -> dict:
    """Return local display metadata without opening the OS credential store."""
    metadata = read_metadata("chatgpt")
    accounts = []
    for saved in metadata.get("accounts", []):
        authenticated = bool(saved.get("authenticated") and (
            saved.get("can_refresh") or (saved.get("expires_at") or 0) > time.time()))
        accounts.append({key: saved.get(key) for key in
                         ("id", "email", "label", "plan_usage_enabled", "expires_at")} |
                        {"authenticated": authenticated})
    account = next((item for item in accounts
                    if item["id"] == metadata.get("active_account_id")), None)
    return {"authenticated": bool(account and account["authenticated"]),
            "status_known": bool(metadata.get("status_known")),
            "email": account.get("email") if account else None,
            "plan_usage_enabled": bool(account and account["plan_usage_enabled"]),
            "expires_at": account.get("expires_at") if account else None,
            "error": _last_error, "login_pending": _pending is not None,
            "accounts": accounts, "active_account_id": metadata.get("active_account_id")}


def _http_json(method: str, url: str, **kwargs) -> dict:
    try:
        response = httpx.request(method, url, timeout=20, follow_redirects=False, **kwargs)
        if url == TOKEN_ENDPOINT and response.status_code == 400:
            try:
                problem = response.json()
                if isinstance(problem, dict) and problem.get("error") == "invalid_grant":
                    raise ChatGPTAuthError("ChatGPT 授權已失效；請重新登入。", reauthorize=True)
            except (ValueError, TypeError):
                pass
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict):
            raise TypeError("Expected object")
        return result
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ChatGPTAuthError("ChatGPT 授權服務暫時無法連線；請重試登入。") from exc


def _validate_identity(raw_token: str, client_id: str, nonce: str | None) -> dict:
    global _jwks_cache
    try:
        header = jwt.get_unverified_header(raw_token)
        if header.get("alg") not in {"RS256", "ES256"} or not header.get("kid"):
            raise ValueError("Unsupported signature")
        cached = _jwks_cache
        if cached is None or time.monotonic() - cached[0] >= 300:
            cached = (time.monotonic(), _http_json("GET", JWKS_ENDPOINT))
            _jwks_cache = cached
        matches = [key for key in cached[1].get("keys", []) if key.get("kid") == header["kid"]]
        if not matches:
            _jwks_cache = (time.monotonic(), _http_json("GET", JWKS_ENDPOINT))
            matches = [key for key in _jwks_cache[1].get("keys", [])
                       if key.get("kid") == header["kid"]]
        if len(matches) != 1:
            raise ValueError("Unknown signing key")
        key = jwt.PyJWK.from_dict(matches[0], algorithm=header["alg"]).key
        identity = jwt.decode(raw_token, key, algorithms=[header["alg"]], issuer=ISSUER,
                              audience=client_id, leeway=5,
                              options={"require": ["sub", "exp", "iat"]})
        if not isinstance(identity.get("sub"), str) or not identity["sub"]:
            raise ValueError("Missing subject")
        if nonce is not None and not secrets.compare_digest(str(identity.get("nonce", "")), nonce):
            raise ValueError("Nonce mismatch")
        return identity
    except ChatGPTAuthError:
        raise
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise ChatGPTAuthError("無法確認 ChatGPT 登入身分；請重新登入。") from exc


def _read_tokens(response: dict, previous: dict | None = None) -> dict:
    previous = previous or {}
    if not isinstance(response.get("access_token"), str) or not response["access_token"]:
        raise ChatGPTAuthError("ChatGPT 授權未回傳可使用的憑證；請重新登入。")
    if str(response.get("token_type", "")).lower() != "bearer":
        raise ChatGPTAuthError("ChatGPT 授權類型不符；請重新登入。")
    try:
        lifetime = int(response["expires_in"])
        if lifetime <= 0:
            raise ValueError("Expired token")
    except (KeyError, TypeError, ValueError) as exc:
        raise ChatGPTAuthError("ChatGPT 授權有效時間不符；請重新登入。") from exc
    scope = response.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise ChatGPTAuthError("ChatGPT 授權範圍無法確認；請重新登入。")
    result = {"access_token": response["access_token"], "token_type": "Bearer",
              "expires_at": time.time() + lifetime,
              "scopes": scope.split() if scope is not None else previous.get("scopes", []),
              "refresh_token": response.get("refresh_token", previous.get("refresh_token")),
              "id_token": response.get("id_token", previous.get("id_token"))}
    for key in ("refresh_token", "id_token"):
        if result[key] is not None and not isinstance(result[key], str):
            raise ChatGPTAuthError("ChatGPT 授權資料不完整；請重新登入。")
    return result


def _stop_listener(attempt: LoginAttempt) -> None:
    if attempt.timer:
        attempt.timer.cancel()
    def stop():
        attempt.server.shutdown()
        attempt.server.server_close()
    threading.Thread(target=stop, daemon=True).start()


def cancel_login(error: str | None = None) -> dict:
    global _pending, _last_error
    with _STATE_LOCK:
        attempt, _pending = _pending, None
        _last_error = error
        if attempt:
            attempt.cancelled = True
            _stop_listener(attempt)
    return status()


def _expire(attempt: LoginAttempt) -> None:
    with _STATE_LOCK:
        if _pending is attempt:
            cancel_login("登入已逾時；請再次按下 Continue with ChatGPT。")


def _callback(query: dict[str, list[str]]) -> tuple[int, str]:
    global _pending, _last_error
    with _STATE_LOCK:
        attempt = _pending
        if attempt is None or attempt.consumed or attempt.cancelled:
            return 400, "此登入請求已結束，請回到應用程式重新登入。"
        returned_state = query.get("state", [""])[0]
        if len(query.get("state", [])) != 1 or not secrets.compare_digest(
                returned_state, attempt.state):
            return 400, "登入請求無法確認。"
        if time.monotonic() >= attempt.deadline:
            cancel_login("登入已逾時；請重新登入。")
            return 400, "登入已逾時。"
        attempt.consumed = True
    try:
        if query.get("error"):
            raise ChatGPTAuthError("ChatGPT 授權未完成；請重新登入並允許使用方案額度。")
        code = query.get("code", [""])[0]
        if len(query.get("code", [])) != 1 or not code:
            raise ChatGPTAuthError("登入未回傳授權碼；請重新登入。")
        supplied_client = query.get("client_id", [None])[0]
        if len(query.get("client_id", [])) > 1:
            raise ChatGPTAuthError("ChatGPT 帳號註冊不符；請重新登入。")
        if attempt.client_id == "dynamic_agent_client":
            if not isinstance(supplied_client, str) or not supplied_client.startswith("oaiapp_"):
                raise ChatGPTAuthError("ChatGPT 註冊尚未完成；請重新登入。")
            issued_client = supplied_client
        else:
            if supplied_client is not None and supplied_client != attempt.client_id:
                raise ChatGPTAuthError("ChatGPT 帳號註冊不符；請重新登入。")
            issued_client = attempt.client_id
        # Save the issued registration BEFORE exchange, so invalid_grant can be recovered.
        with _STATE_LOCK:
            if _pending is not attempt or attempt.cancelled:
                raise ChatGPTAuthError("此登入請求已取消；請重新登入。")
            with _storage_transaction():
                record = _registry(allow_interaction=True)
                record["pending_client_id"] = issued_client
                _save_registry(record)
        response = _http_json("POST", TOKEN_ENDPOINT, data={
            "grant_type": "authorization_code", "client_id": issued_client,
            "code": code, "code_verifier": attempt.verifier,
            "redirect_uri": attempt.redirect_uri, "resource": RESOURCE,
        })
        raw_id_token = response.get("id_token")
        if not isinstance(raw_id_token, str):
            raise ChatGPTAuthError("ChatGPT 未回傳身分資料；請重新登入。")
        identity = _validate_identity(raw_id_token, issued_client, attempt.nonce)
        tokens = _read_tokens(response)
        with _STATE_LOCK:
            if _pending is not attempt or attempt.cancelled or time.monotonic() >= attempt.deadline:
                raise ChatGPTAuthError("此登入請求已取消或逾時；請重新登入。")
            with _storage_transaction():
                record = _registry(allow_interaction=True)
                previous = record["accounts"].get(attempt.account_id)
                if previous and (previous["subject"] != identity["sub"]
                                 or previous["client_id"] != issued_client):
                    raise ChatGPTAuthError("登入帳號與選取的帳號不同；請新增帳號或重新登入。")
                account_id = hashlib.sha256((issued_client + "\0" + identity["sub"]).encode()).hexdigest()
                record["accounts"][account_id] = {
                    "issuer": ISSUER, "subject": identity["sub"], "client_id": issued_client,
                    "email": identity.get("email") if isinstance(identity.get("email"), str) else None,
                    **tokens,
                }
                record["active_account_id"] = account_id
                record.pop("pending_client_id", None)
                _save_registry(record)
            _pending = None
            _last_error = None if _plan_enabled(tokens) else "已登入，但尚未允許使用 ChatGPT 方案額度；請重新授權。"
            _stop_listener(attempt)
        return 200, "ChatGPT 授權完成。可以關閉此視窗並返回 txTrade。"
    except (ChatGPTAuthError, CredentialStoreError) as exc:
        with _STATE_LOCK:
            if _pending is attempt:
                cancel_login(str(exc))
        return 400, str(exc)


class _CallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        # Authorization codes and state must not appear in access logs.
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path != "/auth/callback":
            self.send_error(404)
            return
        code, message = _callback(parse_qs(parsed.query, keep_blank_values=True))
        document = ("<!doctype html><html lang='zh-Hant'><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                    "<title>txTrade</title><body style='font:17px system-ui;padding:48px'>"
                    f"<p>{message}</p></body></html>").encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(document)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(document)


def start_login(account_id: str | None = None, add_account: bool = False) -> dict:
    global _pending, _last_error
    with _STATE_LOCK:
        if _pending is not None:
            cancel_login()
        with _storage_transaction():
            record = _registry(allow_interaction=True)
            _save_registry(record)  # Persist the opaque host ID before browser sign-in.
        selected_id = None if add_account else account_id or record.get("active_account_id")
        account = record["accounts"].get(selected_id)
        if account_id and not account:
            raise ChatGPTAuthError("找不到選取的 ChatGPT 帳號。")
        client_id = account["client_id"] if account else (
            "dynamic_agent_client" if add_account else record.get("pending_client_id", "dynamic_agent_client"))
        server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
        server.timeout = 1
        verifier = secrets.token_urlsafe(64)
        attempt = LoginAttempt(
            state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32), verifier=verifier,
            redirect_uri=f"http://127.0.0.1:{server.server_port}/auth/callback",
            client_id=client_id, host_id=record["host_id"], account_id=selected_id,
            deadline=time.monotonic() + _LOGIN_SECONDS, server=server)
        parameters = {
            "client_id": client_id, "ext_agent_host_id": attempt.host_id,
            "response_type": "code", "redirect_uri": attempt.redirect_uri,
            "scope": SCOPES, "resource": RESOURCE, "state": attempt.state,
            "nonce": attempt.nonce, "code_challenge_method": "S256",
            "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="),
        }
        if client_id == "dynamic_agent_client":
            parameters["agent_name_hint"] = "txTrade"
        elif account:
            if account.get("id_token"):
                parameters["id_token_hint"] = account["id_token"]
            if account.get("email"):
                parameters["login_hint"] = account["email"]
        _pending, _last_error = attempt, None
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        attempt.timer = threading.Timer(_LOGIN_SECONDS, _expire, args=(attempt,))
        attempt.timer.daemon = True
        attempt.timer.start()
        return {"auth_url": AUTHORIZE_ENDPOINT + "?" + urlencode(parameters), "status": status()}


def select_account(account_id: str) -> dict:
    cancel_login()
    with _storage_transaction():
        record = _registry(allow_interaction=True)
        if account_id not in record["accounts"]:
            raise ChatGPTAuthError("找不到選取的 ChatGPT 帳號。")
        record["active_account_id"] = account_id
        _save_registry(record)
    return status()


def get_access_token(*, force_refresh: bool = False) -> str:
    with _storage_transaction():
        record = _registry(allow_interaction=True)
        account = _active(record)
        _cache_registry(record)
        if not _authenticated(account):
            raise ChatGPTAuthError("請先使用 Continue with ChatGPT 登入並授權策略分析。")
        if not _plan_enabled(account):
            raise ChatGPTAuthError("此帳號尚未允許使用 ChatGPT 方案額度；請重新授權。")
        if not force_refresh and account.get("expires_at", 0) > time.time() + 60:
            return account["access_token"]
        if not account.get("refresh_token"):
            raise ChatGPTAuthError("ChatGPT 授權已到期；請重新登入。")
        try:
            response = _http_json("POST", TOKEN_ENDPOINT, data={
                "grant_type": "refresh_token", "client_id": account["client_id"],
                "refresh_token": account["refresh_token"], "resource": RESOURCE,
            })
        except ChatGPTAuthError as exc:
            if exc.reauthorize:
                for key in ("access_token", "refresh_token", "id_token", "expires_at", "scopes"):
                    account.pop(key, None)
                _save_registry(record)
            raise
        if response.get("id_token"):
            identity = _validate_identity(response["id_token"], account["client_id"], None)
            if identity["sub"] != account["subject"]:
                raise ChatGPTAuthError("ChatGPT 更新授權的帳號不符；請重新登入。")
        replacement = {**account, **_read_tokens(response, account)}
        if not _plan_enabled(replacement):
            raise ChatGPTAuthError("ChatGPT 方案授權已變更；請重新授權。")
        record["accounts"][record["active_account_id"]] = replacement
        _save_registry(record)
        return replacement["access_token"]


def logout() -> dict:
    global _last_error
    cancel_login()
    warning = None
    with _storage_transaction():
        record = _registry(allow_interaction=True)
        account = _active(record)
        if account and account.get("refresh_token"):
            try:
                discovery = _http_json("GET", ISSUER + "/.well-known/openid-configuration")
                endpoint = discovery.get("revocation_endpoint", "")
                parsed = urlsplit(endpoint)
                if parsed.scheme != "https" or parsed.netloc != "auth.openai.com":
                    raise ChatGPTAuthError("無法確認遠端登出端點。")
                revoked = False
                for attempt_number in range(2):
                    try:
                        response = httpx.request("POST", endpoint, timeout=10, follow_redirects=False,
                                                 data={"token": account["refresh_token"],
                                                       "token_type_hint": "refresh_token",
                                                       "client_id": account["client_id"]})
                        if response.status_code == 200:
                            revoked = True
                            break
                        if response.status_code < 500:
                            break
                    except httpx.HTTPError:
                        pass
                    if attempt_number == 0:
                        time.sleep(0.25)
                if not revoked:
                    raise ChatGPTAuthError("遠端登出未確認。")
            except (httpx.HTTPError, ChatGPTAuthError):
                warning = "已在本機登出；遠端撤銷未確認，可到 ChatGPT 設定解除此應用程式授權。"
        if account:
            for key in ("access_token", "refresh_token", "id_token", "expires_at", "scopes"):
                account.pop(key, None)
            _save_registry(record)
    _last_error = warning
    return status()


def list_models() -> list[dict]:
    payload = _http_json("GET", RESOURCE + "/models",
                         headers={"Authorization": "Bearer " + get_access_token()})
    models = [{"id": item["slug"], "name": item.get("display_name") or item["slug"]}
              for item in payload.get("models", [])
              if isinstance(item, dict) and item.get("visibility") == "list"
              and isinstance(item.get("slug"), str)]
    write_metadata("chatgpt", {**read_metadata("chatgpt"), "models": models})
    return models


class LoginInput(BaseModel):
    account_id: str | None = None
    add_account: bool = False


class AccountInput(BaseModel):
    account_id: str


def _route_error(exc: Exception):
    raise HTTPException(503, str(exc)) from exc


@router.get("/status")
def auth_status():
    return status()


@router.post("/check")
def auth_check():
    """User-triggered readiness check; cached GET status is never permission."""
    try:
        get_access_token()
        return status()
    except (ChatGPTAuthError, CredentialStoreError) as exc:
        _route_error(exc)


@router.post("/login")
def auth_login(body: LoginInput | None = None):
    try:
        selected = body or LoginInput()
        return start_login(selected.account_id, selected.add_account)
    except (ChatGPTAuthError, CredentialStoreError, OSError) as exc:
        if isinstance(exc, OSError):
            raise HTTPException(503, "無法啟動本機登入回呼；請重試。") from exc
        _route_error(exc)


@router.post("/select")
def auth_select(body: AccountInput):
    try:
        return select_account(body.account_id)
    except (ChatGPTAuthError, CredentialStoreError) as exc:
        _route_error(exc)


@router.post("/cancel")
def auth_cancel():
    return cancel_login()


@router.post("/logout")
def auth_logout():
    try:
        return logout()
    except (ChatGPTAuthError, CredentialStoreError) as exc:
        _route_error(exc)


@router.get("/models")
def auth_models():
    return read_metadata("chatgpt").get("models", [])


@router.post("/models")
def auth_refresh_models():
    try:
        return list_models()
    except (ChatGPTAuthError, CredentialStoreError) as exc:
        _route_error(exc)
