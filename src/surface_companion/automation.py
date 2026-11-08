"""Things the service does by itself, only when the user turned them on.

AutoProfile: pick a performance profile for each power source.
KbdTimeout: switch the keyboard backlight off after you stop typing, and back on when you return.
Both are inactive until configured; "System" means they are off and the app does nothing.
"""
from __future__ import annotations

import time
from typing import Callable

from . import sysfs
from .hw import display, perf
from .hw.idle import InputActivity


def on_ac() -> bool | None:
    """True when mains power is connected, False on battery, None if it can't tell."""
    seen = False
    for d in sysfs.glob("/sys/class/power_supply/*"):
        t = sysfs.read(d / "type")
        if t in ("Mains", "USB", "USB_C", "USB_PD", "USB_DCP", "USB_CDP", "USB_ACA"):
            seen = True
            if sysfs.read(d / "online") == "1":
                return True
    return False if seen else None


class AutoProfile:
    def __init__(self, settings, ac_fn: Callable[[], bool | None] = on_ac):
        self.settings, self.ac_fn = settings, ac_fn
        self._last: bool | None = None

    def config(self) -> dict | None:
        return self.settings.data.get("auto_profile")

    def configure(self, battery: str, ac: str) -> None:
        """Each value is a profile id or "system" (leave the profile alone for that source)."""
        valid = [c["id"] for c in perf.state().get("choices", [])]
        for v in (battery, ac):
            if v != "system" and v not in valid:
                raise ValueError(f"Unsupported profile: {v}")
        self.settings.set("auto_profile", {"battery": battery, "ac": ac})
        self.settings.clear("profile")          # automatic replaces a fixed choice
        self._last = None
        self.tick()

    def disable(self) -> None:
        self.settings.clear("auto_profile")
        self._last = None

    def tick(self) -> None:
        cfg = self.config()
        if not cfg:
            return
        ac = self.ac_fn()
        if ac is None or ac == self._last:
            return
        self._last = ac
        want = cfg["ac" if ac else "battery"]
        if want != "system":
            try:
                perf.set_profile(want)
            except Exception:  # noqa: BLE001 - e.g. profile not offered on this kernel; try again at next change
                self._last = None


class KbdTimeout:
    """Dims the keyboard backlight after `seconds` without input and restores it on the next input."""
    def __init__(self, settings, activity: InputActivity | None = None):
        self.settings = settings
        self.activity = activity or InputActivity()
        self._dimmed_from: int | None = None

    def seconds(self) -> int | None:
        return self.settings.data.get("kbd_timeout")

    def configure(self, seconds: int | None) -> None:
        if seconds in (None, 0):
            self.settings.clear("kbd_timeout")
            self._restore()
            self.activity.stop()
            return
        if not 1 <= int(seconds) <= 3600:
            raise ValueError("Timeout must be between 1 second and 1 hour")
        if not display.state()["keyboard"]:
            raise RuntimeError("This device has no keyboard backlight that Linux can control")
        self.settings.set("kbd_timeout", int(seconds))
        self.activity.start()

    def start_if_configured(self) -> None:
        if self.seconds() and display.state()["keyboard"]:
            self.activity.start()

    def _restore(self) -> None:
        if self._dimmed_from is not None:
            try:
                display.set_keyboard_level(self._dimmed_from)
            except Exception:  # noqa: BLE001
                pass
            self._dimmed_from = None

    def tick(self) -> None:
        secs = self.seconds()
        if not secs:
            return
        kb = display.state()["keyboard"]
        if not kb:
            return
        idle = self.activity.idle_seconds()
        if self._dimmed_from is None:
            if idle >= secs and kb["value"] > 0:
                self._dimmed_from = kb["value"]
                display.set_keyboard_level(0)
        elif kb["value"] != 0:
            self._dimmed_from = None            # the user (or the firmware) set a level themselves: stand down
        elif idle < secs:
            self._restore()
