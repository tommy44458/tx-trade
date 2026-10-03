"""Process and file-lock differences between macOS/Linux and Windows, in one place."""

import os
import re
import shutil
import signal
import subprocess
from contextlib import contextmanager
from pathlib import Path

WINDOWS = os.name == "nt"
# Background programs must never open a console window on Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if WINDOWS else 0
# Files Windows runs directly from a found path; anything else is not a program.
WINDOWS_PROGRAM_SUFFIXES = (".exe", ".cmd", ".bat")

# npm installs a CLI on Windows as a .cmd shim that runs `node <script> %*`.
# Run that script with node directly: through cmd.exe, JSON arguments such as an
# MCP configuration would be re-parsed by cmd's quoting rules.
_NPM_SHIM_SCRIPT = re.compile(r'"%~?dp0%?\\([^"%]+?\.[cm]?js)"', re.IGNORECASE)


def launch_command(executable: str) -> list[str]:
    """The argv prefix that starts `executable` without a shell."""
    path = Path(executable)
    if not WINDOWS or path.suffix.lower() not in (".cmd", ".bat"):
        return [executable]
    try:
        match = _NPM_SHIM_SCRIPT.search(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        match = None
    if match:
        script = path.parent / match.group(1)
        bundled = path.parent / "node.exe"
        node = str(bundled) if bundled.is_file() else shutil.which("node")
        if script.is_file() and node:
            return [node, str(script)]
    return [executable]


def stop_process(process: subprocess.Popen, *, own_group: bool, timeout: float = 3) -> None:
    """Stop a launched CLI and everything it started."""
    if process.poll() is not None:
        return
    if WINDOWS:
        # terminate() ends only the launched program; a CLI's own children
        # (and the node behind an npm shim) would keep running.
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10, creationflags=NO_WINDOW,
                           check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)
        return
    try:
        if own_group:
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=timeout)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if own_group:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=timeout)


_job = None


def contain_descendants() -> bool:
    """On Windows, end every process this one starts when it exits, however it exits.

    Windows has no process groups to signal and does not reparent orphans, so a
    hard-stopped backend would otherwise leave its workers and model CLIs running.
    A job object that kills its members when its last handle closes covers a crash,
    a force-quit of the app and a normal shutdown alike. Returns whether it applies.
    """
    global _job
    if not WINDOWS or _job is not None:
        return _job is not None
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits),
                    ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kill_on_job_close, extended_limit_information = 0x2000, 9
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return False
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = kill_on_job_close
    if (not kernel32.SetInformationJobObject(job, extended_limit_information,
                                             ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess())):
        kernel32.CloseHandle(job)
        return False
    # Kept open for the life of this process; Windows closes it at exit.
    _job = job
    return True


@contextmanager
def exclusive_file_lock(path: Path):
    """Hold an exclusive lock on `path`, waiting for another process to release it."""
    with path.open("a+b") as handle:
        if WINDOWS:
            import msvcrt

            # One byte at offset 0; Windows allows locking past the end of a file.
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue  # LK_LOCK gives up after ~10 s; keep waiting like flock.
            try:
                yield handle
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield handle
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
