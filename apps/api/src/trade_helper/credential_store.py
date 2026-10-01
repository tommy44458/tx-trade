"""App-owned credentials and their AES key live entirely in private SQLite.

This removes OS credential prompts. Encryption prevents accidental plaintext
in reports, logs or raw credential rows; possession of the complete database
also gives access to its local master key. Filesystem permissions and the
user's disk protection provide the actual boundary for a local-only app.
Retired encrypted records remain archived until explicitly cleared; reentering
a key never opens the old external store or overwrites archived ciphertext.
"""

import json
import os
import re
import threading
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .db import connect, database_path, init_db, utc_now

# Legacy keychain service name, kept so archived pre-rename references stay identifiable.
SERVICE = "ai-trade-helper"
_LOCK = threading.RLock()
_VERSION = 2
_ANY_REVISION = object()


class CredentialStoreError(RuntimeError):
    """Safe to display: contains no credential or backend exception details."""


def _name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,128}", name):
        raise ValueError("Invalid credential name")
    return name


def _aad(name: str, key_id: str, version: int = _VERSION) -> bytes:
    return json.dumps([SERVICE, "credential", version, name, key_id],
                      separators=(",", ":")).encode("utf-8")


def _private_database() -> None:
    init_db()
    if os.name == "posix":
        # Existing databases from early versions may have broader permissions.
        # Only this private file and its SQLite sidecars are touched.
        path = database_path()
        for candidate in (path, path.with_name(path.name + "-wal"),
                          path.with_name(path.name + "-shm")):
            if candidate.exists():
                candidate.chmod(0o600)


def credential_status(name: str) -> bool:
    """Check active record presence without decrypting anything."""
    _private_database()
    with connect(readonly=True) as db:
        return db.execute("SELECT 1 FROM credential_records WHERE name=?",
                          (_name(name),)).fetchone() is not None


def credential_needs_reentry(name: str) -> bool:
    """Old ciphertext is preserved but cannot be used by the new local store."""
    _private_database()
    with connect(readonly=True) as db:
        return db.execute("""SELECT 1 FROM credential_legacy_records WHERE name=?
            AND NOT EXISTS(SELECT 1 FROM credential_records WHERE name=?)""",
                          (_name(name), name)).fetchone() is not None


def load_credentials(name: str, *, allow_interaction: bool = False) -> dict | None:
    return load_credentials_with_revision(name)[0]


def load_credentials_with_revision(name: str) -> tuple[dict | None, str | None]:
    # Compatibility keyword intentionally has no effect: there is no external
    # store to access or interactive authorization to request.
    _name(name)
    _private_database()
    with _LOCK, connect(readonly=True) as db:
        row = db.execute("""SELECT r.*,k.master_key FROM credential_records AS r
            LEFT JOIN credential_local_keys AS k USING(key_id) WHERE r.name=?""", (name,)).fetchone()
        if row is None:
            return None, None
        try:
            if row["version"] != _VERSION or len(row["nonce"]) != 12 or len(row["master_key"]) != 32:
                raise ValueError("Invalid credential record")
            plaintext = AESGCM(row["master_key"]).decrypt(
                row["nonce"], row["ciphertext"], _aad(name, row["key_id"], row["version"]))
            result = json.loads(plaintext)
            if not isinstance(result, dict):
                raise TypeError("Invalid credential record")
            return result, row["updated_at"]
        except Exception as exc:
            raise CredentialStoreError(
                "無法讀取本地金鑰資料；請在設定清除此連線後重新輸入金鑰或重新登入。"
                "其他連線及舊資料不會被覆寫。"
            ) from exc


def save_credentials(name: str, value: dict, *, allow_interaction: bool = False,
                     expected_revision=_ANY_REVISION) -> str | bool:
    if not isinstance(value, dict):
        raise TypeError("Credential record must be an object")
    _name(name)
    plaintext = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        _private_database()
        with _LOCK, connect() as db:
            current = db.execute("SELECT updated_at FROM credential_records WHERE name=?", (name,)).fetchone()
            if expected_revision is not _ANY_REVISION and (
                    current["updated_at"] if current else None) != expected_revision:
                return False
            # BEGIN IMMEDIATE serializes initial key creation across processes,
            # and the key plus first ciphertext commit or roll back together.
            key_row = db.execute("SELECT * FROM credential_local_keys ORDER BY created_at LIMIT 1").fetchone()
            if key_row is None:
                if db.execute("SELECT 1 FROM credential_records LIMIT 1").fetchone():
                    raise CredentialStoreError("本地解密金鑰遺失；請重新設定受影響的連線。既有資料不會被覆寫。")
                key_id, key = uuid4().hex, AESGCM.generate_key(bit_length=256)
                db.execute("INSERT INTO credential_local_keys(key_id,master_key,created_at) VALUES (?,?,?)",
                           (key_id, key, utc_now()))
            else:
                key_id, key = key_row["key_id"], key_row["master_key"]
                if not isinstance(key, bytes) or len(key) != 32:
                    raise CredentialStoreError("本地解密金鑰損毀；請還原資料備份。既有資料不會被覆寫。")
            nonce = os.urandom(12)
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, _aad(name, key_id))
            revision = utc_now()
            db.execute("""INSERT INTO credential_records
                (name,key_id,nonce,ciphertext,version,updated_at) VALUES (?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET key_id=excluded.key_id,
                nonce=excluded.nonce,ciphertext=excluded.ciphertext,
                version=excluded.version,updated_at=excluded.updated_at""",
                       (name, key_id, nonce, ciphertext, _VERSION, revision))
            return revision
    except CredentialStoreError:
        raise
    except Exception as exc:
        raise CredentialStoreError("無法保存本地金鑰；請確認資料目錄可寫入，然後重試。") from exc


def delete_credentials(name: str, *, expected_revision=_ANY_REVISION) -> bool:
    """An explicit removal clears active and archived data for this connection."""
    _private_database()
    with _LOCK, connect() as db:
        current = db.execute("SELECT updated_at FROM credential_records WHERE name=?", (_name(name),)).fetchone()
        if expected_revision is not _ANY_REVISION and (
                current["updated_at"] if current else None) != expected_revision:
            return False
        db.execute("DELETE FROM credential_records WHERE name=?", (_name(name),))
        db.execute("DELETE FROM credential_legacy_records WHERE name=?", (name,))
        return True
