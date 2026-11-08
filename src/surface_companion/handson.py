"""Helpers for the hands-on tests: ports, detachable keyboard, speaker tones and camera capture.

Everything here only reads the system (or plays a short tone / takes one photo you asked for). It never changes
settings and never detaches anything.
"""
from __future__ import annotations

import math
import re
import shutil
import struct
import wave

from . import sysfs
from .hw import book, pen


# ------------------------------------------------------------------ ports

def usb_devices() -> dict[str, dict]:
    out = {}
    for d in sysfs.glob("/sys/bus/usb/devices/*"):
        if ":" in d.name or d.name.startswith("usb"):          # interfaces and root hubs
            continue
        if not sysfs.p(d / "idVendor").exists():
            continue
        prod = (sysfs.read(d / "product", "") or "").strip()
        mfr = (sysfs.read(d / "manufacturer", "") or "").strip()
        vid, pid = sysfs.read(d / "idVendor", "") or "", sysfs.read(d / "idProduct", "") or ""
        out[d.name] = {"name": " ".join(x for x in (mfr, prod) if x) or f"USB device {vid}:{pid}",
                       "removable": sysfs.read(d / "removable", "unknown") or "unknown",
                       "speed": sysfs.read(d / "speed", "") or ""}
    return out


def typec_ports() -> dict[str, dict]:
    out = {}
    for d in sysfs.glob("/sys/class/typec/port*"):
        if "-" in d.name:                      # port0-partner, port0-cable
            continue
        out[d.name] = {"partner": sysfs.p(f"/sys/class/typec/{d.name}-partner").exists(),
                       "data_role": sysfs.read(d / "data_role", "") or "",
                       "power_role": sysfs.read(d / "power_role", "") or "",
                       "power_mode": sysfs.read(d / "power_operation_mode", "") or ""}
    return out


def displays() -> dict[str, bool]:
    out = {}
    for d in sysfs.glob("/sys/class/drm/card*-*"):
        name = d.name.split("-", 1)[1]
        if name.startswith(("eDP", "LVDS", "DSI")):     # the built-in screen is not a port
            continue
        out[name] = sysfs.read(d / "status", "") == "connected"
    return out


def jacks() -> dict[str, bool]:
    """Audio jack sense lines (headphone/mic plug detection), from the sound card's controls."""
    if not sysfs.have("amixer"):
        return {}
    txt = sysfs.run(["amixer", "-c", "0", "contents"], timeout=5).stdout
    out = {}
    for m in re.finditer(r"name='([^']*Jack[^']*)'.*?\n(?:.*\n)*?\s*: values=(on|off)", txt):
        if "Phantom" in m.group(1):          # always-on stand-ins for built-in speakers and mics, not real plugs
            continue
        out[m.group(1)] = m.group(2) == "on"
    return out


def ports_snapshot() -> dict:
    return {"usb": usb_devices(), "typec": typec_ports(), "displays": displays(), "jacks": jacks()}


def diff_snapshots(old: dict, new: dict) -> list[str]:
    ev = []
    for k, d in new["usb"].items():
        if k not in old["usb"]:
            ev.append(f"USB device plugged in: {d['name']}" + (f" ({d['speed']} Mb/s)" if d["speed"] else ""))
    for k, d in old["usb"].items():
        if k not in new["usb"]:
            ev.append(f"USB device removed: {d['name']}")
    for k, d in new["typec"].items():
        was = old["typec"].get(k, {}).get("partner", False)
        if d["partner"] and not was:
            ev.append(f"USB-C {k}: something connected" + (f" (power {d['power_mode']})" if d["power_mode"] else ""))
        elif was and not d["partner"]:
            ev.append(f"USB-C {k}: disconnected")
    for k, v in new["displays"].items():
        if v != old["displays"].get(k, False):
            ev.append(f"Display output {k}: " + ("a screen connected" if v else "disconnected"))
    for k, v in new["jacks"].items():
        if v != old["jacks"].get(k, False):
            ev.append(f"{k}: " + ("plugged in" if v else "unplugged"))
    return ev


def describe_ports(snap: dict) -> list[str]:
    lines = []
    ext = [d for d in snap["usb"].values() if d["removable"] != "fixed"]
    internal = len(snap["usb"]) - len(ext)
    lines.append(f"USB devices: {len(ext)} plugged in" + (f", {internal} built in" if internal else ""))
    for d in ext:
        lines.append(f"    {d['name']}")
    if snap["typec"]:
        for k, d in snap["typec"].items():
            lines.append(f"USB-C {k}: " + ("connected" if d["partner"] else "empty"))
    conn = [k for k, v in snap["displays"].items() if v]
    lines.append("Display outputs: " + (", ".join(conn) if conn else "none connected"))
    on = [k for k, v in snap["jacks"].items() if v and "HDMI" not in k and "DP" not in k]
    if snap["jacks"]:
        lines.append("Audio jack: " + (", ".join(on) if on else "nothing plugged in"))
    return lines


# ------------------------------------------------------------------ detachable keyboard (Type Cover / Book base)

def keyboard_state() -> dict:
    devs = pen._input_devices()
    kb = [d for d in devs if "kbd" in d["handlers"] and "leds" in d["handlers"]]
    tp = [d for d in devs if re.search(r"touchpad|trackpad", d["name"], re.I)]
    return {"keyboards": len(kb), "touchpads": len(tp)}


def keyboard_events(old: dict, new: dict) -> list[str]:
    ev = []
    if old["keyboards"] and not new["keyboards"]:
        ev.append("keyboard detached")
    if not old["keyboards"] and new["keyboards"]:
        ev.append("keyboard attached")
    if old["touchpads"] and not new["touchpads"]:
        ev.append("touchpad gone")
    if not old["touchpads"] and new["touchpads"]:
        ev.append("touchpad back")
    return ev


def detach_capable(family: str) -> bool:
    return family in ("book", "pro", "go", "surface3")


# ------------------------------------------------------------------ speakers

def tone_wav(path: str, channel: str = "both", seconds: float = 1.2, freq: float = 440.0, amp: float = 0.25) -> str:
    """Write a short stereo sine tone with a soft fade. `channel` is left, right or both."""
    rate = 44100
    n = int(rate * seconds)
    fade = int(rate * 0.05)
    frames = bytearray()
    for i in range(n):
        env = min(1.0, i / fade, (n - i) / fade)
        v = int(32767 * amp * env * math.sin(2 * math.pi * freq * i / rate))
        left = v if channel in ("left", "both") else 0
        right = v if channel in ("right", "both") else 0
        frames += struct.pack("<hh", left, right)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return path


def player_cmd(path: str) -> list[str] | None:
    for tool, args in (("pw-play", []), ("paplay", []), ("aplay", ["-q"])):
        if shutil.which(tool):
            return [tool, *args, path]
    return None


# ------------------------------------------------------------------ camera

def capture_nodes() -> list[tuple[str, str]]:
    """Video nodes that can deliver pictures (index 0 of each camera; the others carry metadata)."""
    out = []
    for d in sysfs.glob("/sys/class/video4linux/video*"):
        if (sysfs.read(d / "index", "0") or "0") == "0":
            out.append((f"/dev/{d.name}", sysfs.read(d / "name", d.name) or d.name))
    return out


def capture_cmd(node: str, out: str) -> list[str] | None:
    if shutil.which("ffmpeg"):
        # several frames so auto-exposure settles; -update 1 keeps only the last one
        return ["ffmpeg", "-y", "-loglevel", "error", "-f", "video4linux2", "-i", node, "-frames:v", "10",
                "-update", "1", out]
    if shutil.which("v4l2-ctl"):
        return ["v4l2-ctl", "-d", node, "--stream-mmap", "--stream-count=1", f"--stream-to={out}"]
    if shutil.which("gst-launch-1.0"):
        return ["gst-launch-1.0", "-q", "v4l2src", f"device={node}", "num-buffers=10", "!", "videoconvert", "!", "jpegenc",
                "!", "multifilesink", f"location={out}", "max-files=1"]
    return None


# ------------------------------------------------------------------ Surface Book detach (read-only)

def detach_state() -> dict:
    s = book.state()
    return {"dtx": s.get("dtx", False), "mode": s.get("mode"), "latch": s.get("latch"),
            "base_attached": s.get("base_attached")}
