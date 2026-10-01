import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_normal_shutdown_does_not_become_a_worker_failure():
    stopped = threading.Event()
    stopped.set()
    server = SimpleNamespace(should_exit=False)
    failed = threading.Event()
    monitor_services([SimpleNamespace(poll=lambda: 0)], server, stopped, failed)
    assert not failed.is_set()
    assert not server.should_exit
