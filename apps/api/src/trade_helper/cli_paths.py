"""Locate user-installed model CLIs, including from a Finder-launched app.

Apps opened from Finder inherit a minimal PATH, so the usual installer and
package-manager locations are checked as well.
"""

import os
import shutil
from pathlib import Path


def _common_directories() -> list[Path]:
    home = Path.home()
    directories = [home / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
                   home / ".npm-global/bin", home / ".volta/bin", home / ".bun/bin"]
    # nvm keeps one global bin per Node version; prefer the newest.
    directories += sorted((home / ".nvm/versions/node").glob("*/bin"), reverse=True)
    return directories


def find_executable(name: str, override: str | None = None,
                    extra: tuple[str, ...] = ()) -> str | None:
    candidates = [override, shutil.which(name), *extra,
                  *(str(directory / name) for directory in _common_directories())]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None
