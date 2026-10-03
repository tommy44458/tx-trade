import io
import multiprocessing
import time

from trade_helper import cli_paths, platform_process
from trade_helper.desktop_runtime import watch_lifeline

# The shim npm writes for `npm install -g @anthropic-ai/claude-code` on Windows.
NPM_SHIM = r'''@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@anthropic-ai\claude-code\cli.js" %*
'''


def test_npm_shim_runs_its_script_with_node_and_no_shell(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_process, "WINDOWS", True)
    shim = tmp_path / "claude.cmd"
    shim.write_text(NPM_SHIM, encoding="utf-8")
    # The shim's backslashes are a Windows path; build the same file here.
    script = tmp_path / "node_modules\\@anthropic-ai\\claude-code\\cli.js"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(platform_process.shutil, "which", lambda name: "C:/node/node.exe")
    assert platform_process.launch_command(str(shim)) == ["C:/node/node.exe", str(script)]

    # A node.exe next to the shim wins over the one on PATH, as in the shim itself.
    (tmp_path / "node.exe").write_text("", encoding="utf-8")
    assert platform_process.launch_command(str(shim))[0] == str(tmp_path / "node.exe")


def test_unrecognised_shims_and_native_programs_run_as_they_are(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_process, "WINDOWS", True)
    other = tmp_path / "codex.cmd"
    other.write_text("@echo off\r\nsomething.exe %*\r\n", encoding="utf-8")
    assert platform_process.launch_command(str(other)) == [str(other)]
    assert platform_process.launch_command(str(tmp_path / "claude.exe")) == [str(tmp_path / "claude.exe")]
    monkeypatch.setattr(platform_process, "WINDOWS", False)
    shim = tmp_path / "claude.cmd"
    shim.write_text(NPM_SHIM, encoding="utf-8")
    assert platform_process.launch_command(str(shim)) == [str(shim)]


def test_windows_finds_programs_by_extension_in_install_locations(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_paths, "WINDOWS", True)
    monkeypatch.setattr(cli_paths.shutil, "which", lambda name: None)
    appdata = tmp_path / "AppData/Roaming"
    (appdata / "npm").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("NVM_SYMLINK", raising=False)
    monkeypatch.setattr(cli_paths.Path, "home", lambda: tmp_path)
    # A bare file without a program extension is never treated as runnable.
    (appdata / "npm/codex").write_text("#!/bin/sh", encoding="utf-8")
    assert cli_paths.find_executable("codex") is None
    (appdata / "npm/codex.cmd").write_text("", encoding="utf-8")
    assert cli_paths.find_executable("codex") == str(appdata / "npm/codex.cmd")
    # A native program in Claude Code's own install location is preferred to a shim.
    (tmp_path / ".local/bin").mkdir(parents=True)
    (tmp_path / ".local/bin/codex.exe").write_text("", encoding="utf-8")
    assert cli_paths.find_executable("codex") == str(tmp_path / ".local/bin/codex.exe")
    # Extra fallbacks gain the extension too.
    (tmp_path / ".claude/local").mkdir(parents=True)
    (tmp_path / ".claude/local/claude.exe").write_text("", encoding="utf-8")
    assert cli_paths.find_executable("claude", extra=(str(tmp_path / ".claude/local/claude"),)) \
        == str(tmp_path / ".claude/local/claude.exe")


def _hold(path, ready, release):
    with platform_process.exclusive_file_lock(path):
        ready.set()
        release.wait(5)


def test_exclusive_file_lock_waits_for_another_process(tmp_path):
    path = tmp_path / "worker.lock"
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    holder = context.Process(target=_hold, args=(path, ready, release))
    holder.start()
    try:
        assert ready.wait(10)
        started = time.monotonic()
        release_later = context.Process(target=_release_after, args=(release, 0.4))
        release_later.start()
        with platform_process.exclusive_file_lock(path):
            waited = time.monotonic() - started
        release_later.join(5)
        assert waited >= 0.3
    finally:
        release.set()
        holder.join(5)


def _release_after(release, seconds):
    time.sleep(seconds)
    release.set()


def test_lifeline_stops_at_end_of_file():
    stopped = []
    watch_lifeline(io.BytesIO(b"anything the app might write"), lambda: stopped.append(True))
    assert stopped == [True]

    class Broken:
        def read(self, size):
            raise OSError("pipe closed")

    watch_lifeline(Broken(), lambda: stopped.append(True))
    assert stopped == [True, True]


def test_contain_descendants_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(platform_process, "WINDOWS", False)
    assert platform_process.contain_descendants() is False
