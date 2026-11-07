"""Touchscreen and Surface Pen."""
from __future__ import annotations

import re

from .. import sysfs


def _input_devices() -> list[dict]:
    txt = sysfs.read("/proc/bus/input/devices", "") or ""
    out = []
    for block in txt.split("\n\n"):
        m = re.search(r'N: Name="(.*)"', block)
        if m:
            out.append({"name": m.group(1), "handlers": (re.search(r"H: Handlers=(.*)", block) or [None, ""])[1]})
    return out


def _units() -> list[str]:
    r = sysfs.run(["systemctl", "list-units", "--all", "--plain", "--no-legend", "iptsd*"])
    return [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]


def state() -> dict:
    devs = _input_devices()
    touch = [d["name"] for d in devs if re.search(r"ipts|ithc|touch", d["name"], re.I) and not re.search(r"stylus|pen", d["name"], re.I)]
    pen = [d["name"] for d in devs if re.search(r"stylus|pen\b|eraser", d["name"], re.I)]
    units = _units()
    active = [u for u in units if sysfs.run(["systemctl", "is-active", u]).stdout.strip() == "active"]
    return {
        "touch_devices": sorted(set(touch)),
        "pen_devices": sorted(set(pen)),
        "iptsd_installed": sysfs.have("iptsd") or sysfs.p("/usr/lib/iptsd").exists() or bool(units),
        "iptsd_units": units,
        "iptsd_active": bool(active),
        "libwacom_surface": any(sysfs.p(x).exists() for x in
                                ("/usr/share/libwacom/surface-pro-4.tablet", "/usr/share/libwacom/surface-book-2.tablet")),
    }


def restart_service() -> None:
    units = _units()
    if not units:
        raise RuntimeError("iptsd is not installed")
    r = sysfs.run(["systemctl", "restart", *units], timeout=30)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or "Could not restart touch service")
