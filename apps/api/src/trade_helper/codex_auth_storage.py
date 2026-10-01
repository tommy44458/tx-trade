"""SQLite persistence adapter for this application's official Codex login.

The official CLI's file driver is used only inside a private, temporary home.
Only app-owned credentials are staged here; shared CLI token files are never
read or copied by this adapter. CLI refreshes are saved back to SQLite, using a
revision check so a stale process cannot restore a logout or overwrite a later
login. Normal shutdown removes the temporary CLI files.
"""

import json
import os
import tempfile
from pathlib import Path

from .credential_store import (
    load_credentials_with_revision,
    save_credentials,
)

_RECORD = "codex"


def _valid_managed_auth(payload) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("tokens"), dict):
        return False
    # This adapter only stages browser-managed ChatGPT OAuth, whose refresh
    # lifecycle belongs to the official CLI. Do not replace valid authorization
    # with an empty or damaged but syntactically valid JSON file.
    return all(isinstance(payload["tokens"].get(name), str) and payload["tokens"][name]
               for name in ("access_token", "refresh_token"))


class LocalCodexAuth:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ath-codex-auth-")
        self.home = Path(self.directory.name)
        self.home.chmod(0o700)
        self.path = self.home / "auth.json"
        self.closed = False
        try:
            self.saved, self.revision = load_credentials_with_revision(_RECORD)
        except BaseException:
            self.directory.cleanup()
            raise
        if self.saved is not None:
            payload = self.saved.get("auth")
            if not _valid_managed_auth(payload):
                self.directory.cleanup()
                raise ValueError("Invalid app authorization")
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as file:
                json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))

    def persist(self) -> None:
        if self.closed:
            return
        if self.path.is_symlink():
            raise ValueError("Invalid app authorization file")
        if self.path.exists():
            if self.path.stat().st_size > 256_000:
                raise ValueError("Invalid app authorization file")
            self.path.chmod(0o600)
            payload = json.loads(self.path.read_text())
            if not _valid_managed_auth(payload):
                raise ValueError("Invalid app authorization file")
            value = {"auth": payload}
            if value == self.saved:
                return
            revision = save_credentials(_RECORD, value, expected_revision=self.revision)
            if revision:
                self.saved, self.revision = value, revision
        # A missing temporary file is not proof of logout. Only the explicit
        # logout route removes the latest persistent authorization record.

    def close(self) -> None:
        if self.closed:
            return
        try:
            self.persist()
        finally:
            self.closed = True
            self.directory.cleanup()
