"""Locate user-installed model CLIs, including from an app opened outside a shell.

Apps opened from Finder or the Start menu may not see the PATH a terminal has,
so the usual installer and package-manager locations are checked as well.
"""

import os
import shutil
from pathlib import Path

from .platform_process import WINDOWS, WINDOWS_PROGRAM_SUFFIXES


def _common_directories() -> list[Path]:
    home = Path.home()
    if WINDOWS:
        environ = os.environ
        roots = {key: Path(environ[key]) for key in ("APPDATA", "LOCALAPPDATA", "NVM_SYMLINK")
                 if environ.get(key)}
        directories = [home / ".local/bin"]  # Claude Code's native installer
        if "APPDATA" in roots:
            directories.append(roots["APPDATA"] / "npm")  # npm install -g
        if "LOCALAPPDATA" in roots:
            directories += [roots["LOCALAPPDATA"] / "Volta/bin",
                            roots["LOCALAPPDATA"] / "Microsoft/WinGet/Links"]
        if "NVM_SYMLINK" in roots:
            directories.append(roots["NVM_SYMLINK"])  # nvm-windows' active Node
        return directories + [home / ".bun/bin", home / "scoop/shims"]
    directories = [home / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
                   home / ".npm-global/bin", home / ".volta/bin", home / ".bun/bin"]
    # nvm keeps one global bin per Node version; prefer the newest.
    directories += sorted((home / ".nvm/versions/node").glob("*/bin"), reverse=True)
    return directories


def _names(name: str) -> list[str]:
    # A bare name is not runnable on Windows; prefer a native program over a shim.
    return [f"{name}{suffix}" for suffix in WINDOWS_PROGRAM_SUFFIXES] if WINDOWS else [name]


def _runnable(candidate: str) -> bool:
    path = Path(candidate)
    if not path.is_file():
        return False
    if WINDOWS:
        return path.suffix.lower() in WINDOWS_PROGRAM_SUFFIXES
    return os.access(candidate, os.X_OK)


def find_executable(name: str, override: str | None = None,
                    extra: tuple[str, ...] = ()) -> str | None:
    fallbacks = [str(Path(path).with_name(file)) if WINDOWS else path
                 for path in extra for file in _names(Path(path).name)]
    candidates = [override, shutil.which(name), *fallbacks,
                  *(str(directory / file) for directory in _common_directories()
                    for file in _names(name))]
    for candidate in candidates:
        if candidate and _runnable(candidate):
            return candidate
    return None
