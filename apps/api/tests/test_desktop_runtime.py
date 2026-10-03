import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from trade_helper import desktop_runtime
from trade_helper.desktop_runtime import monitor_services, mount_web, service_command


def test_desktop_frontend_does_not_swallow_unknown_api_routes(tmp_path: Path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<title>Local application</title>")
    app = FastAPI()
    mount_web(app, tmp_path)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert "Local application" in client.get("/history").text
        assert client.get("/api/v1/missing").status_code == 404
        assert client.get("/api").status_code == 404


def test_desktop_service_command_handles_packaged_runtime(monkeypatch):
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", "/local/backend")
    assert service_command("worker") == ["/local/backend", "--service", "worker"]


def test_dead_analysis_worker_fails_supervisor_instead_of_leaving_jobs_queued():
    server = SimpleNamespace(should_exit=False)
    failed = threading.Event()
    monitor_services([SimpleNamespace(poll=lambda: 1)], server, threading.Event(), failed)
    assert failed.is_set()
    assert server.should_exit


def test_backend_exits_when_the_app_that_started_it_is_gone(monkeypatch):
    # Force-quitting the app reparents this process; it must not linger holding locks.
    server, stopped, failed = SimpleNamespace(should_exit=False), threading.Event(), threading.Event()
    monkeypatch.setattr(desktop_runtime.os, "getppid", lambda: 1)
    monitor_services([SimpleNamespace(poll=lambda: None)], server, stopped, failed, parent=4242)
    assert server.should_exit is True
    assert stopped.is_set()
    assert not failed.is_set()


def test_normal_shutdown_does_not_become_a_worker_failure():
    stopped = threading.Event()
    stopped.set()
    server = SimpleNamespace(should_exit=False)
    failed = threading.Event()
    monitor_services([SimpleNamespace(poll=lambda: 0)], server, stopped, failed)
    assert not failed.is_set()
    assert not server.should_exit


def test_offline_runtime_smoke_starts_only_private_api_without_workers(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DESKTOP", "1")
    monkeypatch.setenv("APP_DESKTOP_TOKEN", "isolated-offline-package-token")
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<title>Offline verification</title>")
    runs = []
    mounts = []
    monkeypatch.setattr(desktop_runtime, "mount_web", lambda _app, path: mounts.append(path))
    monkeypatch.setattr(desktop_runtime.subprocess, "Popen",
                        lambda *_args, **_kwargs: pytest.fail("Offline smoke must not launch workers"))
    monkeypatch.setattr(desktop_runtime, "monitor_services",
                        lambda *_args: pytest.fail("There are no worker processes to monitor"))
    monkeypatch.setattr(desktop_runtime.signal, "signal", lambda *_args: None)

    def server(config):
        assert config.host == "127.0.0.1"
        assert config.port == 18741
        return SimpleNamespace(run=lambda: runs.append(config.app), should_exit=False)

    monkeypatch.setattr(desktop_runtime.uvicorn, "Server", server)
    desktop_runtime.main(["--port", "18741", "--web-dir", str(tmp_path), "--no-workers"])
    assert len(runs) == 1
    assert mounts == [tmp_path]


def test_a_database_from_a_newer_version_exits_with_its_own_code(monkeypatch, tmp_path):
    from trade_helper import db

    monkeypatch.setenv("APP_DESKTOP", "1")
    monkeypatch.setenv("APP_DESKTOP_TOKEN", "isolated-offline-package-token")
    db.init_db()
    with db.connect() as database:
        database.execute(f"PRAGMA user_version={db.SCHEMA_VERSION + 1}")
    monkeypatch.setattr(desktop_runtime.uvicorn, "Server",
                        lambda _config: pytest.fail("A newer database must stop before serving"))
    with pytest.raises(SystemExit) as stopped:
        desktop_runtime.main(["--port", "18741", "--web-dir", str(tmp_path), "--no-workers"])
    # The desktop app reads this code to offer the update instead of a generic failure.
    assert stopped.value.code == desktop_runtime.DATABASE_FROM_NEWER_VERSION_EXIT == 65


def test_offline_smoke_cannot_be_combined_with_a_background_service(monkeypatch):
    monkeypatch.setenv("APP_DESKTOP", "1")
    monkeypatch.setenv("APP_DESKTOP_TOKEN", "isolated-offline-package-token")
    with pytest.raises(SystemExit) as error:
        desktop_runtime.main(["--service", "worker", "--no-workers"])
    assert error.value.code == 2


def test_the_packaged_app_serves_no_api_documentation(monkeypatch):
    import importlib

    from trade_helper import api

    monkeypatch.setenv("APP_DESKTOP", "1")
    packaged = importlib.reload(api)
    try:
        assert (packaged.app.docs_url, packaged.app.redoc_url, packaged.app.openapi_url) == (None, None, None)
    finally:
        monkeypatch.delenv("APP_DESKTOP")
        importlib.reload(api)
