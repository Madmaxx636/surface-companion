"""Community-maintained drivers: unofficial, unreviewed by linux-surface, installed at your own risk.

Every driver is pinned to one exact commit. We download that commit's tarball (GitHub serves a commit by
its hash, so the content cannot change under us), record its SHA-256, and refuse path tricks while extracting.
Nothing is ever fetched from a moving branch.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import sysfs
from ..device import Device

Log = Callable[[str], None]
MAX_BYTES = 60 * 1024 * 1024


@dataclass
class Driver:
    id: str
    title: str
    summary: str
    models: tuple[str, ...]
    repo: str                         # owner/name on GitHub
    commit: str                       # exact commit that was read when this entry was written
    mode: str                         # "terminal": runs the author's installer as you; "dkms": service builds modules
    risks: list[str]
    license: str
    language: str = "English"
    # terminal mode
    script: str = ""
    undo_args: list[str] = field(default_factory=list)
    installed_probe: str = ""         # path whose presence means "installed" (terminal mode)
    # dkms mode
    dkms: list[str] = field(default_factory=list)      # package names, version 1.0
    dkms_dir: str = "dkms"
    not_included: str = ""

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/tree/{self.commit}"

    @property
    def tarball(self) -> str:
        return f"https://github.com/{self.repo}/archive/{self.commit}.tar.gz"


DRIVERS: list[Driver] = [
    Driver(
        id="sp5-cameras",
        title="Surface Pro 5 cameras (front and rear)",
        summary="Patched ov8865 and ov5693 camera sensor drivers plus a colour tuning file. Fixes cameras that show "
                "one frame and then freeze, using the author's installer.",
        models=("Surface Pro 5", "Surface Pro (5th Gen)"),
        repo="EberhartLeberhart/surface-kamera-treiber",
        commit="8a01ad20f5aba974b847bd2fcefaafec730864c7",
        mode="terminal",
        script="fix4-installer.sh",
        undo_args=["--rueckgaengig"],
        installed_probe="/opt/surface-kamera",
        license="None stated",
        language="German (docs and installer messages)",
        risks=[
            "One person's hobby project: 0 stars, no licence, and its README says it is “not announced, not finished”.",
            "It replaces camera driver files inside /lib/modules for your running kernel and needs a fresh run after "
            "every kernel update.",
            "It downloads more code while it runs (kernel sources from the linux-surface repository) and uses sudo "
            "itself, so you will see it ask for your password in a terminal.",
            "It adds your user to the “video” group and installs build tools.",
            "Known open problems: very dark front camera, wrong colours in OBS, only 1280x720 in PipeWire.",
            "Undo restores the original modules only if the backup it made still exists.",
        ]),
    Driver(
        id="sp7plus-cameras",
        title="Surface Pro 7+ camera drivers (kernel part only)",
        summary="DKMS builds of patched ov5693 (front), ov7251 (IR) and int3472 (sensor power) kernel drivers.",
        models=("Surface Pro 7+",),
        repo="dmanresa-saes/surface-ipu6-cameras",
        commit="7a03f7c88f7122c790fdb34a4ac440c2ca4f612a",
        mode="dkms",
        dkms=["ov5693-surface", "ov7251-surface", "int3472-surface"],
        license="Mixed: GPL-2.0 (kernel parts), MIT, LGPL-2.1; docs CC-BY-4.0",
        language="English README, Spanish playbooks",
        not_included="This installs only the three kernel modules. The rest of the working camera pipeline (the IPU6 "
                     "hardware ISP module, bridges, Intel's userspace HAL and libcamera patches) is a manual "
                     "playbook in that project's docs and is NOT installed here. Expect partial results.",
        risks=[
            "A 5-star research project. The author says their test machine has been donated, so there will be no "
            "further testing on real hardware.",
            "It replaces in-kernel modules (including int3472, which handles sensor power) with patched copies. If "
            "your kernel already has the same fixes upstream, the build can fail or duplicate logic; its own docs say "
            "to check that first.",
            "The kernel headers from linux-surface lack a file the build needs, so a generated stand-in "
            "(autoconf.h) is written into /usr/src. Do this again after kernel updates.",
            "It builds and loads kernel modules as root. A bad module can stop the camera, Wi-Fi power control or "
            "the boot from working. Keep your normal kernel available in the boot menu.",
            "Secure Boot may refuse to load DKMS modules unless they are signed with a key you enrolled.",
        ]),
]


def by_id(did: str) -> Driver:
    for d in DRIVERS:
        if d.id == did:
            return d
    raise KeyError(did)


def for_device(dev: Device) -> list[Driver]:
    return [d for d in DRIVERS if any(dev.model == m or dev.model.startswith(m + " ") for m in d.models)]


def installed(d: Driver) -> bool:
    if d.mode == "terminal":
        return sysfs.p(d.installed_probe).exists()
    out = sysfs.run(["dkms", "status"]).stdout if sysfs.have("dkms") else ""
    return all(any(ln.startswith(p + "/") or ln.startswith(p + ",") for ln in out.splitlines()) for p in d.dkms)


# ------------------------------------------------------------------ download

def _safe_extract(tf: tarfile.TarFile, dest: Path) -> str:
    dest = dest.resolve()
    top = ""
    for m in tf.getmembers():
        target = (dest / m.name).resolve()
        if not str(target).startswith(str(dest) + os.sep) and target != dest:
            raise RuntimeError(f"Unsafe path in archive: {m.name}")
        if m.isdev() or m.isfifo():
            raise RuntimeError(f"Unexpected special file in archive: {m.name}")
        if m.issym() or m.islnk():
            link = (target.parent / m.linkname).resolve() if m.issym() else (dest / m.linkname).resolve()
            if not str(link).startswith(str(dest) + os.sep):
                raise RuntimeError(f"Unsafe link in archive: {m.name}")
        top = top or m.name.split("/")[0]
    tf.extractall(dest, members=[m for m in tf.getmembers() if not m.isdev()])
    return top


def fetch(d: Driver, cache_root: Path, log: Log) -> Path:
    """Download and unpack the pinned commit; returns the source directory."""
    dest = cache_root / d.id / d.commit
    src = dest / "src"
    if src.is_dir():
        log(f"Using the copy already downloaded at {src}")
        return src
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    log(f"Downloading {d.tarball}")
    req = urllib.request.Request(d.tarball, headers={"User-Agent": "surface-companion"})
    h = hashlib.sha256()
    tgz = dest / "snapshot.tar.gz"
    size = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(tgz, "wb") as f:
        while chunk := r.read(65536):
            size += len(chunk)
            if size > MAX_BYTES:
                raise RuntimeError("Download is unexpectedly large; stopping")
            h.update(chunk)
            f.write(chunk)
    log(f"Downloaded {size // 1024} KiB, sha256 {h.hexdigest()}")
    with tarfile.open(tgz) as tf:
        top = _safe_extract(tf, dest / "unpack")
    (dest / "unpack" / top).rename(src)
    shutil.rmtree(dest / "unpack", ignore_errors=True)
    (dest / "SHA256").write_text(h.hexdigest() + "\n")
    return src


# ------------------------------------------------------------------ terminal mode (runs as the user)

TERMINALS = [
    ("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("kgx", ["--"]), ("ptyxis", ["--"]),
    ("konsole", ["-e"]), ("xfce4-terminal", ["-x"]), ("mate-terminal", ["-x"]), ("lxterminal", ["-e"]),
    ("tilix", ["-e"]), ("kitty", []), ("alacritty", ["-e"]), ("foot", []), ("wezterm", ["start", "--"]),
    ("xterm", ["-e"]),
]


def terminal_command(script_argv: list[str]) -> list[str] | None:
    """Wrap a command so it runs in whatever terminal this desktop has, and waits before closing."""
    inner = ["bash", "-c", '"$@"; rc=$?; echo; read -rp "Finished (exit code $rc). Press Enter to close. " _',
             "_", *script_argv]
    env = os.environ.get("TERMINAL")
    candidates = ([(env, ["-e"])] if env else []) + TERMINALS
    for name, flag in candidates:
        if shutil.which(name):
            return [name, *flag, *inner]
    return None


def script_argv(d: Driver, src: Path, undo: bool = False) -> list[str]:
    return ["bash", str(src / d.script), *(d.undo_args if undo else [])]


# ------------------------------------------------------------------ dkms mode (runs in the service as root)

def dkms_install(d: Driver, dev: Device, src: Path, distro_family: str, log: Log, run) -> None:
    if not dev.surface_kernel:
        raise RuntimeError("Boot the linux-surface kernel first: DKMS builds modules for the kernel that is running.")
    prereq = {
        "debian": ["apt-get", "install", "-y", "dkms", "linux-headers-surface", "build-essential", "python3"],
        "fedora": ["dnf", "install", "-y", "dkms", "kernel-surface-devel", "gcc", "make", "python3"],
        "arch": ["pacman", "-S", "--needed", "--noconfirm", "dkms", "linux-surface-headers", "base-devel", "python"],
    }.get(distro_family)
    if not prereq:
        raise RuntimeError("Build prerequisites are only known for Debian, Fedora and Arch families")
    run(prereq, log)
    kver = dev.kernel
    hdr = Path(f"/usr/src/linux-headers-{kver}")
    if not hdr.is_dir():
        hdr = Path(f"/usr/src/kernels/{kver}")
    autoconf = hdr / "include/generated/autoconf.h"
    if not autoconf.exists():
        log("Generating the missing include/generated/autoconf.h (as the project's README describes)")
        run(["python3", str(src / "tools/gen_autoconf.py"), str(hdr / "include/config/auto.conf"), str(autoconf)], log)
        (hdr / "include/generated/rustc_cfg").touch()
    for pkg in d.dkms:
        log(f"== {pkg}")
        run(["bash", str(src / d.dkms_dir / pkg / "dkms_install.sh")], log, cwd=str(src / d.dkms_dir / pkg))


def dkms_remove(d: Driver, log: Log, run) -> None:
    for pkg in d.dkms:
        log(f"== removing {pkg}")
        run(["dkms", "remove", f"{pkg}/1.0", "--all"], log, check=False)
        shutil.rmtree(f"/usr/src/{pkg}-1.0", ignore_errors=True)
