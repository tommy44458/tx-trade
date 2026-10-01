import os
from pathlib import Path


def data_dir() -> Path:
    configured = os.environ.get("APP_DATA_DIR")
    root = Path(configured) if configured else Path(__file__).resolve().parents[4] / "data"
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def local_user_id() -> str:
    return os.environ.get("APP_LOCAL_USER_ID", "local-demo")


def assert_local_mode() -> None:
    if os.environ.get("APP_MODE", "local") != "local":
        raise RuntimeError("Only loopback local development mode is implemented")
