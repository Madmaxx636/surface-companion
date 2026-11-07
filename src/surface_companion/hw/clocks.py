"""CPU, GPU and memory clock speeds, and memory use.

CPU and Intel/AMD GPU clocks are live. Linux does not expose a live RAM clock on these machines, so the memory
figure is the speed the firmware configured at boot (from the SMBIOS memory table, readable by the service only).
The NVIDIA dGPU is deliberately not polled: asking it for its clock would wake it up and drain the battery.
"""
from __future__ import annotations

import re
import time

from .. import sysfs

_MEM_TYPES = {0x18: "DDR3", 0x1A: "DDR4", 0x1B: "LPDDR", 0x1C: "LPDDR2", 0x1D: "LPDDR3", 0x1E: "LPDDR4",
              0x22: "DDR5", 0x23: "LPDDR5"}
_dmi_cache: tuple[float, dict] | None = None


def cpu() -> dict | None:
    cores = []
    for d in sysfs.glob("/sys/devices/system/cpu/cpu[0-9]*"):
        khz = sysfs.read_int(d / "cpufreq/scaling_cur_freq")
        if khz:
            cores.append(round(khz / 1000))
    if not cores:      # no cpufreq: fall back to /proc/cpuinfo
        cores = [round(float(m)) for m in re.findall(r"cpu MHz\s*:\s*([\d.]+)", sysfs.read("/proc/cpuinfo", "") or "")]
    if not cores:
        return None
    c0 = "/sys/devices/system/cpu/cpu0/cpufreq/"
    mx = sysfs.read_int(c0 + "cpuinfo_max_freq")
    cap = sysfs.read_int(c0 + "scaling_max_freq")
    return {
        "avg_mhz": round(sum(cores) / len(cores)), "max_mhz": max(cores), "min_mhz": min(cores), "cores": cores,
        "limit_mhz": round(mx / 1000) if mx else None,            # hardware maximum (turbo)
        "cap_mhz": round(cap / 1000) if cap else None,            # what the kernel currently allows
        "governor": sysfs.read(c0 + "scaling_governor"),
        "epp": sysfs.read(c0 + "energy_performance_preference"),
    }


def gpu() -> dict | None:
    for card in sysfs.glob("/sys/class/drm/card[0-9]"):
        # Intel i915
        cur = sysfs.read_int(card / "gt_cur_freq_mhz")
        if cur is not None:
            return {"name": "Integrated graphics", "mhz": cur,
                    "max_mhz": sysfs.read_int(card / "gt_RP0_freq_mhz") or sysfs.read_int(card / "gt_max_freq_mhz")}
        # Intel xe
        cur = sysfs.read_int(card / "device/tile0/gt0/freq0/cur_freq")
        if cur is not None:
            return {"name": "Integrated graphics", "mhz": cur,
                    "max_mhz": sysfs.read_int(card / "device/tile0/gt0/freq0/max_freq")}
        # AMD
        txt = sysfs.read(card / "device/pp_dpm_sclk")
        if txt:
            for ln in txt.splitlines():
                m = re.match(r"\s*\d+:\s*(\d+)\s*Mhz\s*\*", ln, re.I)
                if m:
                    allm = [int(x) for x in re.findall(r":\s*(\d+)\s*Mhz", txt, re.I)]
                    return {"name": "Integrated graphics", "mhz": int(m.group(1)), "max_mhz": max(allm)}
    return None


def memory_usage() -> dict:
    kv = {}
    for ln in (sysfs.read("/proc/meminfo", "") or "").splitlines():
        k, _, v = ln.partition(":")
        m = re.match(r"\s*(\d+)", v)
        if m:
            kv[k] = int(m.group(1))
    total, avail = kv.get("MemTotal", 0), kv.get("MemAvailable", 0)
    swap_t, swap_f = kv.get("SwapTotal", 0), kv.get("SwapFree", 0)
    return {"total_mb": total // 1024, "used_mb": (total - avail) // 1024,
            "swap_total_mb": swap_t // 1024, "swap_used_mb": (swap_t - swap_f) // 1024}


def parse_smbios_type17(raw: bytes) -> dict | None:
    """Pull speed and type out of one SMBIOS 'Memory Device' record."""
    if len(raw) < 0x15 or raw[0] != 17:
        return None
    size_field = int.from_bytes(raw[0x0C:0x0E], "little")
    if size_field in (0, 0xFFFF):          # empty slot / unknown
        return None
    mt = _MEM_TYPES.get(raw[0x12], None)
    speed = int.from_bytes(raw[0x15:0x17], "little") if len(raw) >= 0x17 else 0
    configured = int.from_bytes(raw[0x20:0x22], "little") if len(raw) >= 0x22 else 0
    return {"type": mt, "rated_mts": speed or None, "configured_mts": configured or speed or None}


def memory_speed() -> dict | None:
    """Static, from firmware. Cached because it never changes while running."""
    global _dmi_cache
    if _dmi_cache and time.monotonic() - _dmi_cache[0] < 3600:
        return _dmi_cache[1] or None
    found = None
    for d in sysfs.glob("/sys/firmware/dmi/entries/17-*"):
        try:
            raw = sysfs.p(d / "raw").read_bytes()
        except OSError:
            continue               # not root: the service can read this, the plain window cannot
        rec = parse_smbios_type17(raw)
        if rec and rec["configured_mts"]:
            found = rec
            break
    _dmi_cache = (time.monotonic(), found or {})
    return found


def state() -> dict:
    return {"cpu": cpu(), "gpu": gpu(), "memory": {**memory_usage(), "speed": memory_speed()}}
