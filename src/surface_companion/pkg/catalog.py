"""What can be installed, per distro family, and how.

Package names and repository details come from the linux-surface wiki
(Installation-and-Setup, Camera-Support, Utilities-and-Packages).
"""
from __future__ import annotations

from dataclasses import dataclass, field

KEY_URL = "https://raw.githubusercontent.com/linux-surface/linux-surface/master/pkg/keys/surface.asc"
KEY_FPR_SUFFIX = "56C464BAAC421453"           # wiki: pacman-key --finger 56C464BAAC421453
APT_LINE = "deb [arch=amd64 signed-by=/usr/share/keyrings/linux-surface.gpg] https://pkg.surfacelinux.com/debian release main"
APT_LIST = "/etc/apt/sources.list.d/linux-surface.list"
APT_KEYRING = "/usr/share/keyrings/linux-surface.gpg"
DNF_REPO = "https://pkg.surfacelinux.com/fedora/linux-surface.repo"
SUSE_REPO = "https://download.opensuse.org/repositories/home:/TaivasJumala:/Surface/openSUSE_Tumbleweed/home:TaivasJumala:Surface.repo"
SUSE_REPO_ALIAS = "Linux Surface (openSUSE_Tumbleweed)"
SILVERBLUE_RPM = "https://github.com/linux-surface/linux-surface/releases/download/silverblue-20201215-1/kernel-20201215-1.x86_64.rpm"
PACMAN_BLOCK = "\n[linux-surface]\nServer = https://pkg.surfacelinux.com/arch/\n"


@dataclass
class Component:
    id: str
    title: str
    why: str
    packages: dict[str, list[str]]            # family -> package names
    needs_repo: bool = False                  # comes from the linux-surface repository
    only_book: bool = False
    only_intel: bool = False
    reboot: bool = False
    check_pkg: dict[str, str] = field(default_factory=dict)   # family -> package queried for "installed"


COMPONENTS: list[Component] = [
    Component(
        "kernel", "linux-surface kernel",
        "Drivers and patches for Surface hardware that are not in your distribution's kernel yet.",
        {"debian": ["linux-surface", "linux-headers-surface"],
         "fedora": ["kernel-surface"],
         "arch": ["linux-surface", "linux-surface-headers"],
         "suse": ["kernel-default"]},
        needs_repo=True, reboot=True,
        check_pkg={"debian": "linux-image-surface", "fedora": "kernel-surface", "arch": "linux-surface"}),
    Component(
        "iptsd", "Touch and pen daemon (iptsd)",
        "Processes touchscreen and Surface Pen data. Without it multitouch and the pen do not work.",
        {"debian": ["iptsd"], "fedora": ["iptsd"], "arch": ["iptsd"], "suse": ["iptsd"]},
        check_pkg={"debian": "iptsd", "fedora": "iptsd", "arch": "iptsd", "suse": "iptsd"}),
    Component(
        "libwacom", "Pen and touch metadata (libwacom-surface)",
        "Lets GNOME and others recognise the touchscreen and pen so they rotate with the screen.",
        {"debian": ["libwacom-surface"], "fedora": ["libwacom-surface"]},
        needs_repo=True,
        check_pkg={"debian": "libwacom-surface", "fedora": "libwacom-surface"}),
    Component(
        "secureboot", "Secure Boot key",
        "Lets Secure Boot accept the linux-surface kernel. You will enrol it on the next boot "
        "(password: surface).",
        {"debian": ["linux-surface-secureboot-mok"], "fedora": ["surface-secureboot"],
         "arch": ["linux-surface-secureboot-mok"]},
        needs_repo=True, reboot=True,
        check_pkg={"debian": "linux-surface-secureboot-mok", "fedora": "surface-secureboot",
                   "arch": "linux-surface-secureboot-mok"}),
    Component(
        "control", "Surface control (surface command)",
        "Command-line access to performance mode and the Surface Book clipboard latch.",
        {"debian": ["surface-control"], "fedora": ["surface-control"], "arch": ["surface-control"]},
        needs_repo=True,
        check_pkg={"debian": "surface-control", "fedora": "surface-control", "arch": "surface-control"}),
    Component(
        "dtx", "Clipboard detach daemon (surface-dtx-daemon)",
        "Makes detaching the Surface Book screen fast and safe: runs your unmount script first and "
        "shows a notification.",
        {"debian": ["surface-dtx-daemon"], "fedora": ["surface-dtx-daemon"], "arch": ["surface-dtx-daemon"]},
        needs_repo=True, only_book=True,
        check_pkg={"debian": "surface-dtx-daemon", "fedora": "surface-dtx-daemon", "arch": "surface-dtx-daemon"}),
    Component(
        "thermald", "Thermal daemon (thermald)",
        "Throttles gently before the hardware throttles hard, avoiding long drops to 400 MHz.",
        {"debian": ["thermald"], "fedora": ["thermald"], "arch": ["thermald"], "suse": ["thermald"]},
        only_intel=True,
        check_pkg={"debian": "thermald", "fedora": "thermald", "arch": "thermald", "suse": "thermald"}),
    Component(
        "camera", "Camera support (libcamera)",
        "Userspace needed for Surface cameras that sit behind Intel's image processor.",
        {"debian": ["libcamera-ipa", "gstreamer1.0-libcamera", "pipewire-libcamera", "libcamera-tools"],
         "fedora": ["libcamera", "libcamera-tools", "libcamera-gstreamer", "libcamera-ipa",
                    "pipewire-plugin-libcamera"],
         "arch": ["libcamera", "libcamera-tools", "gst-plugin-libcamera"]},
        check_pkg={"debian": "libcamera-ipa", "fedora": "libcamera", "arch": "libcamera"}),
]


def by_id(cid: str) -> Component:
    for c in COMPONENTS:
        if c.id == cid:
            return c
    raise KeyError(cid)


# Arch packages needed for particular models (wiki: Arch section).
ARCH_EXTRA_MARVELL = "linux-firmware-marvell"
ARCH_EXTRA_INTEL = "linux-firmware-intel"
