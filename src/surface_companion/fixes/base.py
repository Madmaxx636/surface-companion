"""Fix framework: every fix can be detected, applied and reverted, and only ever
touches files it created itself (marked with MARK)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .. import sysfs
from ..device import Device
from ..pkg.distro import Distro

MARK = "# Managed by Surface Companion. Remove with the app (Fixes page) or delete this file.\n"
Log = Callable[[str], None]


@dataclass
class Ctx:
    dev: Device
    distro: Distro


@dataclass
class Fix:
    id: str
    title: str
    summary: str
    category: str
    source: str                                  # URL of the linux-surface page it comes from
    level: str                                   # recommended | optional | advisory
    applies: Callable[[Ctx], bool]
    detect: Callable[[Ctx], str]                 # applied | available | not-needed
    apply: Callable[[Ctx, Log], None] | None = None
    revert: Callable[[Ctx, Log], None] | None = None
    scope: str = "system"                        # system (daemon) | user (UI process)
    reboot: bool = False
    steps: str = ""                              # advisory text / manual steps
    recommend: Callable[[Ctx], bool] | None = None   # promote optional -> recommended when true
    note: Callable[[Ctx], str] | None = None         # extra, situation-specific line shown with the fix


def is_managed(path: str) -> bool:
    t = sysfs.read(path)
    return t is not None and MARK.strip() in t


def write_managed(path: str, body: str, mode: int = 0o644) -> None:
    pp = sysfs.p(path)
    if pp.exists() and not is_managed(path):
        raise RuntimeError(f"{path} already exists and was not created by this app; not overwriting it")
    pp.parent.mkdir(parents=True, exist_ok=True)
    if body.startswith("#!"):                    # a script's shebang has to be its very first line
        first, _, rest = body.partition("\n")
        pp.write_text(first + "\n" + MARK + rest)
    else:
        pp.write_text(MARK + body)
    os.chmod(pp, mode)


def remove_managed(path: str) -> None:
    if sysfs.p(path).exists() and is_managed(path):
        sysfs.p(path).unlink()


def sh(argv: list[str], log: Log, timeout: float = 300) -> None:
    log("$ " + " ".join(argv))
    r = sysfs.run(argv, timeout=timeout)
    for ln in (r.stdout + r.stderr).splitlines():
        log(ln)
    if r.returncode:
        raise RuntimeError(f"{argv[0]} failed ({r.returncode})")
