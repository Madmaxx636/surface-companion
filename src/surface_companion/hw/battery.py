"""Battery health and charge limit."""
from __future__ import annotations

from .. import sysfs


def _bat():
    for d in sysfs.glob("/sys/class/power_supply/*"):
        if sysfs.read(d / "type") == "Battery" and sysfs.read(d / "scope") != "Device":
            return d
    return None


def state() -> dict:
    d = _bat()
    if d is None:
        return {"present": False}
    full = sysfs.read_int(d / "energy_full") or sysfs.read_int(d / "charge_full")
    design = sysfs.read_int(d / "energy_full_design") or sysfs.read_int(d / "charge_full_design")
    health = round(full * 100 / design) if full and design else None
    lim = sysfs.read_int(d / "charge_control_end_threshold")
    return {
        "present": True,
        "name": d.name,
        "percent": sysfs.read_int(d / "capacity"),
        "status": sysfs.read(d / "status"),
        "health_percent": health,
        "cycles": sysfs.read_int(d / "cycle_count"),
        "charge_limit": lim,   # None when the kernel offers no limit control
    }


def set_charge_limit(percent: int) -> None:
    d = _bat()
    if d is None or not sysfs.p(d / "charge_control_end_threshold").exists():
        raise RuntimeError("This battery does not support a charge limit")
    sysfs.write(d / "charge_control_end_threshold", max(50, min(100, int(percent))))
