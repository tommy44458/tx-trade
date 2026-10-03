"""Stand-in model CLIs that the current operating system can start."""

import os
import sys
from pathlib import Path


def runnable_script(path: Path, source: str) -> Path:
    """A Python stand-in for a CLI at `path`; returns the path to start it by.

    macOS and Linux run a shebang script. Windows cannot, so there it is a .py
    file with a .cmd launcher beside it, which is also how many CLIs are installed.
    """
    if os.name != "nt":
        path.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
        path.chmod(0o700)
        return path
    script = path.with_name(f"{path.name}.py")
    script.write_text(source, encoding="utf-8")
    launcher = path.with_name(f"{path.name}.cmd")
    launcher.write_text(f'@"{sys.executable}" "%~dp0{script.name}" %*\r\n', encoding="utf-8")
    return launcher
