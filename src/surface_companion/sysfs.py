"""Small helpers for reading and writing sysfs/procfs.

Everything goes through ROOT so tests can point SC_SYSFS_ROOT at a fake tree.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(os.environ.get("SC_SYSFS_ROOT", "/"))


def p(path: str | Path) -> Path:
    """Map an absolute path into ROOT."""
    return ROOT / str(path).lstrip("/")


def read(path: str | Path, default: str | None = None) -> str | None:
    try:
        return p(path).read_text(errors="replace").strip()
    except OSError:
        return default


def read_int(path: str | Path, default: int | None = None) -> int | None:
    v = read(path)
    if v is None:
        return default
    try:
        return int(v, 0)
    except ValueError:
        return default


def write(path: str | Path, value: str | int) -> None:
    with open(p(path), "w") as f:
        f.write(str(value))


def glob(pattern: str) -> list[Path]:
    """Glob inside ROOT; results are returned as logical (un-rooted) absolute paths."""
    found = ROOT.glob(pattern.lstrip("/"))
    return sorted(Path("/") / f.relative_to(ROOT) for f in found)


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def run(argv: list[str], timeout: float = 10, **kw) -> subprocess.CompletedProcess:
    """Run a command, never raising on failure or a missing binary."""
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(argv, 127, "", str(e))
