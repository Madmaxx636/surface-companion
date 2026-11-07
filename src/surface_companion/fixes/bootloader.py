"""Add or remove a kernel command-line argument on common bootloaders."""
from __future__ import annotations

import re

from .. import sysfs
from .base import Log, sh, write_managed, remove_managed, is_managed

GRUB_DROPIN = "/etc/default/grub.d/50-surface-companion.cfg"


def in_use(arg: str) -> bool:
    return arg in (sysfs.read("/proc/cmdline", "") or "").split()


def method() -> str:
    if sysfs.have("kernelstub"):
        return "kernelstub"
    if sysfs.have("grubby"):
        return "grubby"
    if sysfs.p("/etc/default/grub.d").is_dir() and sysfs.have("update-grub"):
        return "grub-dropin"
    if sysfs.p("/etc/default/grub").exists() and sysfs.have("grub-mkconfig"):
        return "grub-edit"
    return ""


def can_manage() -> bool:
    return bool(method())


def add(arg: str, log: Log) -> None:
    m = method()
    if m == "kernelstub":
        sh(["kernelstub", "-a", arg], log)
    elif m == "grubby":
        sh(["grubby", "--update-kernel=ALL", f"--args={arg}"], log)
    elif m == "grub-dropin":
        write_managed(GRUB_DROPIN, f'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT {arg}"\n')
        sh(["update-grub"], log)
    elif m == "grub-edit":
        path = "/etc/default/grub"
        txt = sysfs.read(path) or ""
        if arg not in txt:
            new, n = re.subn(r'^(GRUB_CMDLINE_LINUX_DEFAULT=")([^"]*)(")', rf'\1\2 {arg}\3', txt, flags=re.M)
            if not n:
                raise RuntimeError("GRUB_CMDLINE_LINUX_DEFAULT not found in /etc/default/grub")
            sysfs.write(path, new + "\n")
        sh(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"], log)
    else:
        raise RuntimeError("Unsupported bootloader. Add this to your kernel command line manually: " + arg)


def remove(arg: str, log: Log) -> None:
    m = method()
    if m == "kernelstub":
        sh(["kernelstub", "-d", arg], log)
    elif m == "grubby":
        sh(["grubby", "--update-kernel=ALL", f"--remove-args={arg}"], log)
    elif m == "grub-dropin":
        remove_managed(GRUB_DROPIN)
        sh(["update-grub"], log)
    elif m == "grub-edit":
        path = "/etc/default/grub"
        txt = (sysfs.read(path) or "").replace(f" {arg}", "")
        sysfs.write(path, txt + "\n")
        sh(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"], log)
