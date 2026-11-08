"""Knows when you last touched the keyboard, touchpad, mouse or touchscreen.

This works the same on every desktop (X11 or Wayland) because it listens at the input-device level. It is only
started when a feature that needs it is turned on (the keyboard backlight timeout). It throws every event away
unread: the only thing kept is the time of the most recent one. Nothing is recorded or stored.
"""
from __future__ import annotations

import os
import re
import selectors
import threading
import time

from .. import sysfs

EVENT_SIZE = 24            # struct input_event on 64-bit Linux
_SKIP = re.compile(r"power button|sleep button|lid switch|video bus|hdmi|speaker|headphone|headset|mic\b|jack|"
                   r"intel hid|button array|sensor|accel|gyro|als\b|tablet mode|wmi|consumer control|"
                   r"system control|pc speaker", re.I)


def _bits(path: str) -> int:
    txt = sysfs.read(path, "") or ""
    total = 0
    for word in txt.split():
        try:
            total += bin(int(word, 16)).count("1")
        except ValueError:
            pass
    return total


def input_devices() -> list[str]:
    """Event nodes that represent a person using the machine."""
    out = []
    for ev in sysfs.glob("/sys/class/input/event*"):
        base = f"/sys/class/input/{ev.name}/device"
        name = sysfs.read(f"{base}/name", "") or ""
        if _SKIP.search(name):
            continue
        keys, rel, absn = _bits(f"{base}/capabilities/key"), _bits(f"{base}/capabilities/rel"), _bits(f"{base}/capabilities/abs")
        if keys >= 30 or rel or absn:
            out.append(f"/dev/input/{ev.name}")
    return out


class InputActivity:
    def __init__(self, clock=time.monotonic, rescan_seconds: float = 30.0):
        self.clock = clock
        self.rescan_seconds = rescan_seconds
        self.last = clock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def idle_seconds(self) -> float:
        return self.clock() - self.last

    def poke(self) -> None:
        self.last = self.clock()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.poke()
        self._thread = threading.Thread(target=self._loop, name="input-activity", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        sel = selectors.DefaultSelector()
        opened: dict[str, int] = {}
        next_scan = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_scan:
                    next_scan = now + self.rescan_seconds
                    wanted = set(input_devices())
                    for path in wanted - set(opened):
                        try:
                            fd = os.open(sysfs.p(path), os.O_RDONLY | os.O_NONBLOCK)
                        except OSError:
                            continue
                        opened[path] = fd
                        sel.register(fd, selectors.EVENT_READ, path)
                    for path in set(opened) - wanted:
                        self._drop(sel, opened, path)
                for key, _ in sel.select(timeout=0.5):
                    try:
                        data = os.read(key.fd, EVENT_SIZE * 64)        # read and discard; only the timing matters
                    except BlockingIOError:
                        continue
                    except OSError:
                        self._drop(sel, opened, key.data)             # device unplugged
                        continue
                    if data:
                        self.poke()
        finally:
            for path in list(opened):
                self._drop(sel, opened, path)
            sel.close()

    @staticmethod
    def _drop(sel, opened, path):
        fd = opened.pop(path, None)
        if fd is not None:
            try:
                sel.unregister(fd)
            except (KeyError, ValueError):
                pass
            try:
                os.close(fd)
            except OSError:
                pass
