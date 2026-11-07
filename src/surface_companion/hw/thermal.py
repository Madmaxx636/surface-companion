"""Temperatures, fans and throttling.

Surface fans are driven by the embedded controller. Linux can read their speed only when a driver
exposes it (hwmon fan*_input); there is no interface to write a fan curve. The performance profile
is what changes the EC's cooling behaviour.
"""
from __future__ import annotations

import re

from .. import sysfs

# hwmon chip name -> how to show it
CHIP = {"coretemp": "CPU", "k10temp": "CPU", "acpitz": "Chassis", "nvme": "SSD", "amdgpu": "GPU",
        "nouveau": "GPU", "nvidia": "GPU", "iwlwifi_1": "Wi-Fi", "iwlwifi": "Wi-Fi", "BAT1": "Battery",
        "BAT0": "Battery"}
PRIMARY = ["CPU", "GPU", "Chassis", "SSD", "Wi-Fi", "Battery"]
PACKAGE = re.compile(r"package|tctl|tdie", re.I)


def _hwmon() -> tuple[list[dict], list[dict]]:
    temps, fans = [], []
    for h in sysfs.glob("/sys/class/hwmon/hwmon*"):
        chip = sysfs.read(h / "name", "") or ""
        for f in sysfs.glob(f"{h}/temp*_input"):
            n = f.name.split("_")[0]
            v = sysfs.read_int(f)
            if v is None or v <= -100000:
                continue
            temps.append({"chip": chip, "name": CHIP.get(chip, chip), "label": sysfs.read(h / f"{n}_label", "") or n,
                          "celsius": round(v / 1000, 1),
                          "crit": (sysfs.read_int(h / f"{n}_crit", None) or 0) / 1000 or None})
        for f in sysfs.glob(f"{h}/fan*_input"):
            rpm = sysfs.read_int(f)
            if rpm is not None:
                n = f.name.split("_")[0]
                fans.append({"label": sysfs.read(h / f"{n}_label") or ("Fan" if n == "fan1" else n.title()),
                             "rpm": rpm})
    return temps, fans


def _zones() -> list[dict]:
    out = []
    for z in sysfs.glob("/sys/class/thermal/thermal_zone*"):
        v = sysfs.read_int(z / "temp")
        if v is not None and v > -100000:
            out.append({"type": sysfs.read(z / "type", "") or z.name, "celsius": round(v / 1000, 1)})
    return out


def _cooling() -> dict:
    """Thermal throttle state: how far the kernel has had to pull the CPU/fans back."""
    cpu_cur = cpu_max = 0
    fans = []
    tcc = None
    for c in sysfs.glob("/sys/class/thermal/cooling_device*"):
        t = sysfs.read(c / "type", "") or ""
        cur, mx = sysfs.read_int(c / "cur_state"), sysfs.read_int(c / "max_state")
        if cur is None or not mx:
            continue
        if t == "Processor":
            if cur / mx > cpu_cur / (cpu_max or 1):
                cpu_cur, cpu_max = cur, mx
        elif t == "TCC Offset":
            tcc = {"offset": cur, "max": mx}
        elif "fan" in t.lower():
            fans.append({"label": t, "level": cur, "max": mx})
    return {"cpu_throttle": {"level": cpu_cur, "max": cpu_max or 10}, "tcc": tcc, "fan_levels": fans}


def state() -> dict:
    temps, fans = _hwmon()
    zones = _zones()
    primary: list[dict] = []
    for name in PRIMARY:
        group = [t for t in temps if t["name"] == name]
        if not group:
            continue
        pick = next((t for t in group if PACKAGE.search(t["label"])), None) or max(group, key=lambda t: t["celsius"])
        primary.append({"label": name, "celsius": pick["celsius"], "crit": pick["crit"]})
    if not any(p["label"] == "CPU" for p in primary):
        pkg = next((z for z in zones if z["type"] == "x86_pkg_temp"), None)
        if pkg:
            primary.insert(0, {"label": "CPU", "celsius": pkg["celsius"], "crit": None})
    cooling = _cooling()
    unit = sysfs.run(["systemctl", "is-active", "thermald"]).stdout.strip() if sysfs.have("systemctl") else ""
    return {
        "temps": primary,
        "all_sensors": sorted([{"label": f"{t['name']} · {t['label']}" if t["name"] != t["label"] else t["name"],
                                "celsius": t["celsius"]} for t in temps] +
                              [{"label": f"Zone · {z['type']}", "celsius": z["celsius"]} for z in zones],
                              key=lambda s: s["label"]),
        "fans": fans,
        "fan_levels": cooling["fan_levels"],
        "throttle": cooling["cpu_throttle"],
        "tcc": cooling["tcc"],
        "fan_control": False,
        "thermald": {"installed": sysfs.have("thermald"), "active": unit == "active"},
    }
