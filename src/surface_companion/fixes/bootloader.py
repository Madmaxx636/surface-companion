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


def _arg_in_text(arg: str, text: str) -> bool:
    for ln in text.splitlines():
        if ln.lstrip().startswith("#"):
            continue
        if re.search(rf'(?:^|[\s"\'=]){re.escape(arg)}(?=[\s"\']|$)', ln):
            return True
    return False


def in_config(arg: str) -> bool:
    """True if the argument is set in the bootloader configuration (whoever wrote it), not just on this boot."""
    m = method()
    if m == "kernelstub":
        return _arg_in_text(arg, sysfs.read("/etc/kernelstub/configuration", "") or "")
    if m == "grubby":
        return arg in (sysfs.run(["grubby", "--info=ALL"]).stdout or "").split()
    files = ["/etc/default/grub"] + [str(f) for f in sysfs.glob("/etc/default/grub.d/*.cfg")]
    return any(_arg_in_text(arg, sysfs.read(f, "") or "") for f in files)


def _dropin_args() -> list[str]:
    """Arguments held in our own drop-in, one GRUB_CMDLINE_LINUX_DEFAULT line each."""
    if not is_managed(GRUB_DROPIN):
        return []
    return re.findall(r'^GRUB_CMDLINE_LINUX_DEFAULT="\$GRUB_CMDLINE_LINUX_DEFAULT (.+)"$', sysfs.read(GRUB_DROPIN, "") or "",
                      re.M)


def _write_dropin(args: list[str]) -> None:
    if args:
        write_managed(GRUB_DROPIN, "".join(f'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT {a}"\n' for a in args))
    else:
        remove_managed(GRUB_DROPIN)


def add(arg: str, log: Log) -> None:
    m = method()
    if m == "kernelstub":
        sh(["kernelstub", "-a", arg], log)
    elif m == "grubby":
        sh(["grubby", "--update-kernel=ALL", f"--args={arg}"], log)
    elif m == "grub-dropin":
        if in_config(arg):
            log(f"{arg} is already set in the bootloader configuration; nothing to add")
            return
        args = _dropin_args()
        if arg not in args:
            args.append(arg)               # keep what other fixes put here (reboot=pci, ...)
        _write_dropin(args)
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
        args = _dropin_args()
        if arg not in args:
            if in_config(arg):
                raise RuntimeError(f"{arg} is set in a bootloader file you edited yourself (for example "
                                   "/etc/default/grub), not by this app. Remove it there.")
            return
        _write_dropin([a for a in args if a != arg])
        sh(["update-grub"], log)
    elif m == "grub-edit":
        path = "/etc/default/grub"
        txt = (sysfs.read(path) or "").replace(f" {arg}", "")
        sysfs.write(path, txt + "\n")
        sh(["grub-mkconfig", "-o", "/boot/grub/grub.cfg"], log)
