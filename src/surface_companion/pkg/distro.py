"""Distribution detection and package-manager abstraction."""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from .. import sysfs


@dataclass
class Distro:
    id: str = ""
    name: str = ""
    version: str = ""
    family: str = "unknown"   # debian | fedora | arch | suse | unknown
    immutable: bool = False   # ostree/rpm-ostree style systems
    codename: str = ""
    initramfs: str = ""       # initramfs-tools | dracut | mkinitcpio | ""
    guessed: bool = False     # family inferred from installed tools, not from /etc/os-release
    tumbleweed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def parse_os_release(text: str) -> dict:
    out = {}
    for ln in text.splitlines():
        m = re.match(r'^([A-Z_]+)=(.*)$', ln.strip())
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def detect_initramfs() -> str:
    """The tool that really builds this system's initramfs, not merely one that happens to be installed.

    Ubuntu can have dracut installed next to initramfs-tools; its images live in /boot/initrd.img-* and are built
    by update-initramfs. Dracut-based systems keep /boot/initramfs-*.img (or kernel-install layouts).
    """
    if sysfs.have("update-initramfs") and sysfs.glob("/boot/initrd.img-*"):
        return "initramfs-tools"
    if sysfs.have("dracut") and (sysfs.glob("/boot/initramfs-*.img") or sysfs.glob("/boot/efi/*/*/initrd")
                                 or not sysfs.have("update-initramfs")):
        return "dracut"
    if sysfs.have("mkinitcpio"):
        return "mkinitcpio"
    if sysfs.have("update-initramfs"):
        return "initramfs-tools"
    return ""


def detect() -> Distro:
    kv = parse_os_release(sysfs.read("/etc/os-release", "") or "")
    ids = [kv.get("ID", "")] + kv.get("ID_LIKE", "").split()
    d = Distro(id=kv.get("ID", ""), name=kv.get("PRETTY_NAME", kv.get("NAME", "")),
               version=kv.get("VERSION_ID", ""), codename=kv.get("VERSION_CODENAME", ""))
    for i in ids:
        if i in ("debian", "ubuntu"):
            d.family = "debian"
        elif i in ("fedora", "rhel", "centos"):
            d.family = "fedora"
        elif i in ("arch", "manjaro", "endeavouros"):
            d.family = "arch"
        elif i in ("suse", "opensuse", "opensuse-tumbleweed", "opensuse-leap"):
            d.family = "suse"
        if d.family != "unknown":
            break
    if d.family == "unknown":
        for tool, fam in (("apt-get", "debian"), ("dnf", "fedora"), ("pacman", "arch"), ("zypper", "suse")):
            if sysfs.have(tool):
                d.family, d.guessed = fam, True
                break
    d.tumbleweed = d.family == "suse" and "tumbleweed" in (d.id + " " + d.name).lower()
    d.immutable = sysfs.p("/run/ostree-booted").exists()
    d.initramfs = detect_initramfs()
    return d


def libwacom_surface_ok(d: Distro) -> bool:
    """Wiki: on Ubuntu 26.04 and Debian testing/sid, do not install libwacom-surface (issue #2076)."""
    if d.id == "ubuntu":
        try:
            return tuple(int(x) for x in d.version.split(".")[:2]) < (26, 4)
        except ValueError:
            return True
    if d.id == "debian" and not d.version:   # testing / sid carry no VERSION_ID
        return False
    return True


def installed_version(d: Distro, pkg: str) -> str | None:
    if d.family == "debian":
        r = sysfs.run(["dpkg-query", "-W", "-f=${Status}|${Version}", pkg])
        if r.returncode == 0 and r.stdout.startswith("install ok installed|"):
            return r.stdout.split("|", 1)[1]
    elif d.family in ("fedora", "suse"):
        r = sysfs.run(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}", pkg])
        if r.returncode == 0:
            return r.stdout.strip()
    elif d.family == "arch":
        r = sysfs.run(["pacman", "-Q", pkg])
        if r.returncode == 0:
            return r.stdout.split()[1]
    return None
