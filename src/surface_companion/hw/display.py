"""Screen brightness and keyboard backlight."""
from __future__ import annotations

from .. import sysfs

_PRIORITY = {"firmware": 0, "platform": 1, "raw": 2}


def _backlights():
    out = []
    for d in sysfs.glob("/sys/class/backlight/*"):
        mx = sysfs.read_int(d / "max_brightness", 0) or 0
        if mx <= 0:
            continue
        out.append((_PRIORITY.get(sysfs.read(d / "type", "raw"), 3), d))
    return [d for _, d in sorted(out, key=lambda t: (t[0], t[1].name))]


def _kbd_leds():
    return [d for d in sysfs.glob("/sys/class/leds/*")
            if "kbd_backlight" in d.name and (sysfs.read_int(d / "max_brightness", 0) or 0) > 0]


def state() -> dict:
    bl = _backlights()
    screen = None
    if bl:
        d = bl[0]
        mx = sysfs.read_int(d / "max_brightness", 1) or 1
        cur = sysfs.read_int(d / "brightness", 0) or 0
        screen = {"name": d.name, "value": cur, "max": mx, "percent": round(cur * 100 / mx)}
    kb = None
    leds = _kbd_leds()
    if leds:
        d = leds[0]
        kb = {"name": d.name, "value": sysfs.read_int(d / "brightness", 0), "max": sysfs.read_int(d / "max_brightness", 1)}
    return {"screen": screen, "keyboard": kb}


def set_screen_percent(percent: int) -> None:
    bl = _backlights()
    if not bl:
        raise RuntimeError("No controllable screen backlight found")
    d = bl[0]
    mx = sysfs.read_int(d / "max_brightness", 1) or 1
    percent = max(1, min(100, int(percent)))   # never allow a fully black screen
    sysfs.write(d / "brightness", max(1, round(mx * percent / 100)))


def set_keyboard_level(level: int) -> None:
    leds = _kbd_leds()
    if not leds:
        raise RuntimeError("No keyboard backlight found")
    d = leds[0]
    mx = sysfs.read_int(d / "max_brightness", 1) or 1
    sysfs.write(d / "brightness", max(0, min(mx, int(level))))
