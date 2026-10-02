"""Optional background link from this computer to the txinTrade cloud relay.

When the user enables remote access while signed in, this thread registers the
computer as a device, keeps an outbound WebSocket to the relay and runs only
allowlisted commands (see cloud_commands). No port is opened on this computer,
and the local API token is never involved. Disabled or signed out, it is idle.
"""

import contextlib
import json
import random
import socket
import threading
import time

import httpx
from fastapi import APIRouter
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.sync.client import connect

from .auth_metadata import read_metadata, write_metadata
from .cloud_account import CloudAccountError, cloud_origin, session_record
from .cloud_commands import CommandFailed, run_command
from .credential_store import (
    CredentialStoreError,
    delete_credentials,
    load_credentials,
    save_credentials,
)

_DEVICE = "txintrade_device"
_SETTING = "txintrade_remote"
PING_SECONDS = 30
MAX_BACKOFF_SECONDS = 60
WAIT_SECONDS = {"subscription_required": 300, "device_limit": 300, "device_already_connected": 30}
MAX_FRAME_BYTES = 262_144
router = APIRouter(prefix="/api/v1/cloud-account/remote", tags=["txinTrade remote access"])


class _Wait(Exception):
    """Pause with a visible state before retrying."""

    def __init__(self, state: str, seconds: float, error: str | None = None):
        super().__init__(state)
        self.state, self.seconds, self.error = state, seconds, error


def remote_enabled() -> bool:
    return read_metadata(_SETTING).get("enabled") is True


def _device_label() -> str:
    host = socket.gethostname().removesuffix(".local").strip() or "computer"
    return f"txinTrade · {host}"[:80]


def _relay_url(origin: str, device_id: str) -> str:
    scheme = "wss" if origin.startswith("https://") else "ws"
    return f"{scheme}://{origin.split('://', 1)[1]}/api/v1/devices/{device_id}/connect"


class Connector:
    def __init__(self):
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket = None
        self._state = {"state": "disabled", "error": None, "device_id": None, "connected_since": None}

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="cloud-connector", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.wake()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=10)

    def wake(self) -> None:
        """Re-evaluate now: close any open link so settings changes apply at once."""
        self._wake.set()
        with self._lock:
            ws = self._socket
        if ws is not None:
            # Closing an already-broken link is fine.
            with contextlib.suppress(Exception):
                ws.close()

    def status(self) -> dict:
        with self._lock:
            return {"enabled": remote_enabled(), **self._state}

    def _set(self, state: str, error: str | None = None, **values) -> None:
        with self._lock:
            self._state = {"state": state, "error": error, "device_id": values.get("device_id"),
                           "connected_since": values.get("connected_since")}

    def _pause(self, seconds: float) -> None:
        self._wake.wait(seconds)
        self._wake.clear()

    # ---- main loop -------------------------------------------------------
    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                if not remote_enabled():
                    self._set("disabled")
                    self._pause(3600)
                    continue
                session = session_record()
                if not session:
                    raise _Wait("signed_out", 3600)
                device = self._device(session)
                if not self._link(session, device):
                    raise ConnectionError("relay closed before it was ready")
                # A normal end (relay lifetime or settings change): reconnect promptly.
                failures = 0
                self._pause(1)
            except _Wait as wait:
                self._set(wait.state, wait.error)
                self._pause(wait.seconds)
            except (CloudAccountError, CredentialStoreError) as exc:
                self._set("error", str(exc) if isinstance(exc, CloudAccountError) else "storage_failed")
                self._pause(MAX_BACKOFF_SECONDS)
            except Exception:  # noqa: BLE001 - network faults retry with backoff
                failures += 1
                self._set("reconnecting", "service_unavailable")
                self._pause(min(MAX_BACKOFF_SECONDS, 2 ** min(failures, 6)) * random.uniform(0.5, 1.0))

    def _device(self, session: dict) -> dict:
        origin = session.get("origin") or cloud_origin()
        record = load_credentials(_DEVICE, allow_interaction=True)
        if record and record.get("origin") == origin and record.get("expires_at", 0) > time.time() * 1000 + 3_600_000:
            return record
        self._set("registering")
        response = httpx.post(f"{origin}/api/v1/devices", json={"label": _device_label()},
                              headers={"Authorization": f"Bearer {session['session_token']}"}, timeout=15)
        if response.status_code in {401, 403, 409}:
            code = (response.json().get("error") or {}).get("code") if response.headers.get(
                "content-type", "").startswith("application/json") else None
            if response.status_code == 401:
                raise _Wait("signed_out", 3600, "session_expired")
            raise _Wait(code if code in WAIT_SECONDS else "error", WAIT_SECONDS.get(code, 300), code)
        response.raise_for_status()
        body = response.json()
        record = {"device_id": body["device"]["id"], "device_token": body["device_token"],
                  "expires_at": body["device"]["token_expires_at"], "origin": origin}
        save_credentials(_DEVICE, record, allow_interaction=True)
        return record

    def _link(self, session: dict, device: dict) -> bool:
        """Hold one relay connection; return True when it was established."""
        with self._lock:
            # The relay ends links every 15 minutes; a prompt reconnect stays "connected" on screen.
            quiet = self._state["state"] == "connected" and self._state["device_id"] == device["device_id"]
        if not quiet:
            self._set("connecting", device_id=device["device_id"])
        try:
            with connect(_relay_url(device["origin"], device["device_id"]),
                         additional_headers={"Authorization": f"Bearer {device['device_token']}"},
                         open_timeout=15, close_timeout=5, ping_interval=None,
                         max_size=MAX_FRAME_BYTES) as ws:
                return self._hold(ws, device)
        except InvalidStatus as exc:
            status = exc.response.status_code
            if status == 401:
                # The device token was revoked or expired: register again.
                delete_credentials(_DEVICE)
                raise _Wait("registering", 1) from None
            if status == 403:
                raise _Wait("subscription_required", WAIT_SECONDS["subscription_required"],
                            "subscription_required") from None
            if status == 409:
                raise _Wait("device_already_connected", WAIT_SECONDS["device_already_connected"]) from None
            raise

    def _hold(self, ws, device: dict) -> bool:
        with self._lock:
            self._socket = ws
        established = False
        try:
            last_ping = time.monotonic()
            while not self._stop.is_set() and remote_enabled():
                if time.monotonic() - last_ping >= PING_SECONDS:
                    ws.send("ping")
                    last_ping = time.monotonic()
                try:
                    message = ws.recv(timeout=1)
                except TimeoutError:
                    continue
                if message == "pong" or not isinstance(message, str):
                    continue
                event = json.loads(message)
                if event.get("type") == "relay.ready":
                    established = True
                    self._set("connected", device_id=device["device_id"], connected_since=int(time.time() * 1000))
                elif event.get("type") == "job.dispatch":
                    self._handle(ws, event)
        except (ConnectionClosed, WebSocketException, OSError, ValueError):
            pass  # The relay closes links at least every 15 minutes; reconnect.
        finally:
            with self._lock:
                self._socket = None
        return established

    @staticmethod
    def _handle(ws, event: dict) -> None:
        job = event.get("job") or {}
        job_id = job.get("id")
        if not isinstance(job_id, str):
            return
        ws.send(json.dumps({"v": 1, "type": "job.accepted", "job_id": job_id}))
        try:
            result = run_command(job.get("command") or {}, str(event.get("local_idempotency_key", "")))
            ws.send(json.dumps({"v": 1, "type": "job.completed", "job_id": job_id, "result": result},
                               ensure_ascii=False))
        except CommandFailed as failed:
            ws.send(json.dumps({"v": 1, "type": "job.failed", "job_id": job_id, "code": failed.code}))


connector = Connector()


def set_remote_enabled(enabled: bool) -> dict:
    write_metadata(_SETTING, {"enabled": enabled})
    # Report the new intent at once instead of the state from before the switch.
    connector._set("connecting" if enabled else "disabled")
    connector.start()
    connector.wake()
    return connector.status()


def forget_device(session: dict | None) -> None:
    """On sign-out: stop remote access, revoke this device in the cloud and remove its token."""
    write_metadata(_SETTING, {"enabled": False})
    connector.wake()
    try:
        record = load_credentials(_DEVICE, allow_interaction=True)
    except CredentialStoreError:
        record = None
    if record and session:
        try:
            httpx.delete(f"{record['origin']}/api/v1/devices/{record['device_id']}",
                         headers={"Authorization": f"Bearer {session['session_token']}"}, timeout=10)
        except httpx.HTTPError:
            pass
    delete_credentials(_DEVICE)


@router.get("")
def read_remote() -> dict:
    return connector.status()


@router.post("/enable")
def enable_remote() -> dict:
    return set_remote_enabled(True)


@router.post("/disable")
def disable_remote() -> dict:
    return set_remote_enabled(False)
