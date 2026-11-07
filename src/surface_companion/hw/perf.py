"""Performance / power profile.

Modern kernels expose the Surface Aggregator performance modes through the generic
ACPI platform_profile interface. Older linux-surface kernels expose a perf_mode attribute.
"""
from __future__ import annotations

from .. import sysfs

PLATFORM = "/sys/firmware/acpi/platform_profile"
CHOICES = "/sys/firmware/acpi/platform_profile_choices"
LEGACY_GLOB = "/sys/bus/surface_aggregator/devices/01:03:01:00:01/perf_mode"
LEGACY = {1: "balanced", 2: "low-power", 3: "balanced-performance", 4: "performance"}

LABELS = {
    "low-power": ("Battery Saver", "Quietest and coolest. Best battery life."),
    "quiet": ("Quiet", "Keeps the fan down."),
    "cool": ("Cool", "Keeps the chassis cooler."),
    "balanced": ("Recommended", "Good balance of speed, noise and battery."),
    "balanced-performance": ("Better Performance", "More speed, more heat and fan noise."),
    "performance": ("Best Performance", "Full speed. Fan will be audible. Mains power recommended."),
}
ORDER = ["low-power", "quiet", "cool", "balanced", "balanced-performance", "performance"]


def state() -> dict:
    cur = sysfs.read(PLATFORM)
    raw = sysfs.read(CHOICES)
    source = "platform_profile"
    if cur is None:
        legacy = sysfs.glob(LEGACY_GLOB)
        if legacy:
            n = sysfs.read_int(legacy[0])
            cur = LEGACY.get(n or 1)
            raw = " ".join(LEGACY.values())
            source = "legacy"
    if cur is None:
        return {"available": False}
    choices = sorted((raw or cur).split(), key=lambda c: ORDER.index(c) if c in ORDER else 99)
    return {
        "available": True,
        "source": source,
        "current": cur,
        "choices": [{"id": c, "label": LABELS.get(c, (c.title(), ""))[0], "hint": LABELS.get(c, ("", ""))[1]}
                    for c in choices],
    }


def set_profile(profile: str) -> None:
    st = state()
    if not st["available"]:
        raise RuntimeError("This kernel does not expose a performance profile")
    if profile not in [c["id"] for c in st["choices"]]:
        raise ValueError(f"Unsupported profile: {profile}")
    if st["source"] == "legacy":
        n = {v: k for k, v in LEGACY.items()}[profile]
        sysfs.write(sysfs.glob(LEGACY_GLOB)[0], n)
    else:
        sysfs.write(PLATFORM, profile)
