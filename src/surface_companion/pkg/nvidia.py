"""NVIDIA proprietary driver for the discrete GPU (Surface Book 1/2/3, Laptop Studio).

The kernel ships only the open nouveau driver. Full speed and CUDA need NVIDIA's own driver, and because linux-surface
is a custom kernel it has to be the DKMS flavour so it is built for that kernel (linux-surface wiki, Nvidia-GPU).
Only distribution packages are used. Fedora needs a third-party repository (RPM Fusion), so the app explains instead
of adding one.
"""
from __future__ import annotations

import re
import shlex

from .. import sysfs
from ..device import Device
from ..hw import book
from . import distro as dist
from .planner import Plan, Step


def parse_ubuntu_drivers(text: str) -> str | None:
    """Pick the recommended proprietary driver from `ubuntu-drivers devices` output (e.g. 'nvidia-driver-580-open')."""
    best = None
    for ln in text.splitlines():
        m = re.match(r"\s*driver\s*:\s*(nvidia-driver-[\w.-]+)\s*-\s*(.*)", ln)
        if m:
            if "recommended" in m.group(2):
                return m.group(1)
            best = best or m.group(1)
    return best


def dkms_package_for(driver_pkg: str) -> str:
    """nvidia-driver-580-open -> nvidia-dkms-580-open (the wiki installs the DKMS package for custom kernels)."""
    return driver_pkg.replace("nvidia-driver-", "nvidia-dkms-", 1)


TURING_FIRST_DEVICE_ID = 0x1E00     # PCI ids below this are Pascal or older (GTX 10xx and earlier)
LEGACY_BRANCH = "580"               # last NVIDIA branch that still supports Pascal


def pre_turing(dev: Device, gpu: dict | None) -> bool:
    """Pascal and older cannot use NVIDIA's open kernel modules and were dropped after the 580 branch.
    The Surface Book 2's GTX 1050 / 1060 are Pascal, so the model is enough even if the PCI id is unreadable."""
    did = (gpu or {}).get("device_id")
    return (did is not None and did < TURING_FIRST_DEVICE_ID) or dev.model_in(("Surface Book 2",))


def _branch(version: str) -> int | None:
    m = re.match(r"(\d+)\.", version or "")
    return int(m.group(1)) if m else None


def info() -> dict:
    g = book.dgpu()
    mods = sysfs.read("/proc/modules", "") or ""
    loaded = "nvidia" if re.search(r"^nvidia\s", mods, re.M) else "nouveau" if re.search(r"^nouveau\s", mods, re.M) else None
    return {"present": g is not None, "driver": (g or {}).get("driver") or loaded, "loaded": loaded,
            "smi": sysfs.have("nvidia-smi")}


def secure_boot_on() -> bool:
    return "enabled" in (sysfs.run(["mokutil", "--sb-state"]).stdout or "")


def _apt_candidate(pkg: str) -> bool:
    out = sysfs.run(["apt-cache", "policy", pkg]).stdout
    m = re.search(r"Candidate:\s*(\S+)", out)
    return bool(m and m.group(1) != "(none)")


def plan(dev: Device, d: dist.Distro) -> dict:
    """What installing the NVIDIA driver would do here. `interactive` means it must run in a terminal."""
    p = Plan()
    notes = p.notes
    inf = info()
    if not inf["present"]:
        p.blockers.append("No NVIDIA GPU was found on this device.")
    elif inf["loaded"] == "nvidia":
        p.blockers.append("The NVIDIA driver is already loaded.")
    if d.immutable:
        p.blockers.append("Image-based systems layer the driver differently. Follow your distribution's NVIDIA guide.")
    sb = secure_boot_on()
    legacy = pre_turing(dev, book.dgpu())
    if legacy and not p.blockers:
        notes.append(f"Your GPU is from before NVIDIA's Turing generation (Pascal or older). NVIDIA's open kernel modules "
                     f"(the '-open' packages) don't support it, and branches after {LEGACY_BRANCH} dropped it, so the "
                     f"proprietary {LEGACY_BRANCH} driver is used even if your system recommends something newer.")
    if not p.blockers:
        if d.family == "debian" and d.id == "ubuntu" or (d.family == "debian" and "ubuntu" in d.name.lower()):
            drv = None
            if legacy:
                drv = f"nvidia-driver-{LEGACY_BRANCH}"                  # proprietary, not -open; ignores ubuntu-drivers
                if not _apt_candidate(drv) or not _apt_candidate(dkms_package_for(drv)):
                    p.blockers.append(f"{drv} isn't available from your apt sources, and it is the newest driver that "
                                      "supports this GPU.")
            elif sysfs.have("ubuntu-drivers"):
                drv = parse_ubuntu_drivers(sysfs.run(["ubuntu-drivers", "devices"], timeout=60).stdout)
            else:
                p.steps.append(Step("Install the driver finder", "cmd", argv=["apt-get", "install", "-y", "ubuntu-drivers-common"]))
                notes.append("The recommended driver version is chosen after ubuntu-drivers-common is installed; "
                             "the list of packages is shown again once it is known.")
            if drv and not p.blockers:
                p.steps.append(Step("Install the NVIDIA driver (DKMS build for your kernel)", "cmd",
                                    argv=["apt-get", "install", "-y", drv, dkms_package_for(drv)]))
                notes.append(f"{'Driver for your GPU' if legacy else 'Recommended driver for your GPU'}: {drv}.")
            elif sysfs.have("ubuntu-drivers") and not legacy and not p.blockers:
                p.blockers.append("ubuntu-drivers found no supported NVIDIA driver for this GPU.")
        elif d.family == "debian":
            cand = re.search(r"Candidate:\s*(\S+)", sysfs.run(["apt-cache", "policy", "nvidia-driver"]).stdout)
            newest = _branch(cand.group(1)) if cand else None
            if legacy and newest and newest > int(LEGACY_BRANCH):
                p.blockers.append(f"Debian's nvidia-driver is version {cand.group(1)}, which no longer supports this GPU "
                                  f"(the last branch that does is {LEGACY_BRANCH}). Look for your release's "
                                  f"'nvidia-legacy-{LEGACY_BRANCH}xx-driver' packages, or use the open nouveau driver.")
            elif _apt_candidate("nvidia-driver") and _apt_candidate("nvidia-kernel-dkms"):
                p.steps.append(Step("Install the NVIDIA driver (DKMS build for your kernel)", "cmd",
                                    argv=["apt-get", "install", "-y", "nvidia-driver", "nvidia-kernel-dkms"]))
            else:
                p.blockers.append("Debian's NVIDIA driver is in the 'non-free' section, which isn't enabled here. "
                                  "Enable contrib and non-free in your apt sources, run apt update, then try again.")
        elif d.family == "arch" and legacy:
            # nvidia-580xx-* live only in the AUR (not in the official repositories), so pacman cannot install them
            p.blockers.append(f"Arch only packages the newest NVIDIA driver, which no longer supports this GPU. The "
                              f"{LEGACY_BRANCH} branch is in the AUR: install nvidia-{LEGACY_BRANCH}xx-dkms and "
                              f"nvidia-{LEGACY_BRANCH}xx-utils with your AUR helper (for example "
                              f"yay -S nvidia-{LEGACY_BRANCH}xx-dkms nvidia-{LEGACY_BRANCH}xx-utils), then restart.")
        elif d.family == "arch":
            p.steps.append(Step("Install the NVIDIA driver (DKMS build for your kernel)", "cmd",
                                argv=["pacman", "-S", "--needed", "--noconfirm", "nvidia-dkms", "nvidia-utils"]))
        elif d.family == "fedora":
            p.blockers.append("Fedora's NVIDIA driver comes from RPM Fusion, a third-party repository this app won't add "
                              "for you. Follow rpmfusion.org/Howto/NVIDIA (install akmod-nvidia), then restart.")
        else:
            p.blockers.append("Automatic NVIDIA driver setup isn't available for this distribution.")
    p.reboot = True
    interactive = False
    if sb and not p.blockers:
        interactive = True
        notes.append("Secure Boot is on. The driver module has to be signed and your key enrolled, and the installer "
                     "asks you for a password during that. So it will run in a terminal where you can answer, "
                     "then you restart and complete the key enrolment screen (MokManager).")
    risks = ["The driver builds a kernel module for your kernel, which can take several minutes.",
             "It stops the open 'nouveau' driver from loading.",
             "Keep your previous kernel in the boot menu in case something goes wrong. On a Surface Book the screen "
             "is driven by the Intel GPU, so a failed NVIDIA module should not leave you without a display."]
    return {**p.to_dict(), "interactive": interactive, "secure_boot": sb, "risks": risks, "pre_turing": legacy,
            "notes": notes + ["Afterwards, run a program on the NVIDIA GPU with prime-run, or switcherooctl launch."]}


def terminal_script(steps: list[dict]) -> str:
    """Shell script that runs each step with sudo, stopping on the first failure."""
    lines = ["set -e"]
    for s in steps:
        if s["kind"] == "cmd":
            lines.append("sudo " + " ".join(shlex.quote(a) for a in s["argv"]))
    return "\n".join(lines)
