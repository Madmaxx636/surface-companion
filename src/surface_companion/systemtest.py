"""System test: read-only health checks for a Surface running Linux, plus a short CPU stress test.

Each check returns {"id","title","status","detail","hint","goto"} where status is ok | warn | fail | skip.
Checks never change anything. "skip" means the check does not apply to this device or could not be read.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import Callable

from . import sysfs
from .device import Device
from .hw import battery, camera, display, pen, perf, sleep, thermal
from .pkg import distro as dist, planner


@dataclass
class Ctx:
    dev: Device
    distro: dist.Distro


def _r(id_, title, status, detail="", hint="", goto=None):
    return {"id": id_, "title": title, "status": status, "detail": detail, "hint": hint, "goto": goto}


def _kernel_log() -> str | None:
    """Kernel messages for this boot. Needs root (or the adm group); returns None when unreadable."""
    for argv in (["dmesg", "--notime"], ["journalctl", "-k", "-b", "--no-pager", "-o", "cat"]):
        r = sysfs.run(argv, timeout=20)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    return None


# ---------------------------------------------------------------- checks

def c_kernel(x: Ctx):
    t = "Surface kernel"
    if not x.dev.is_surface:
        return _r("kernel", t, "skip", "This doesn't look like a Surface device.")
    if x.dev.surface_kernel:
        return _r("kernel", t, "ok", f"Running linux-surface {x.dev.kernel}.")
    return _r("kernel", t, "warn", f"Running the standard kernel {x.dev.kernel}. Many Surface features work, but some "
                                    "(touch, newer models, cameras) may be limited.",
              "Install the linux-surface kernel from Software & Updates.", "software")


def c_sam(x: Ctx):
    t = "Surface Aggregator (keyboard, battery, performance mode)"
    if not x.dev.is_surface:
        return _r("sam", t, "skip")
    devs = sysfs.glob("/sys/bus/surface_aggregator/devices/*")
    mods = sysfs.read("/proc/modules", "") or ""
    if devs:
        return _r("sam", t, "ok", f"Loaded, {len(devs)} devices attached.")
    if "surface_aggregator" in mods:
        return _r("sam", t, "warn", "The driver is loaded but no devices attached.",
                  "Some older Surface models have no Surface Aggregator. Otherwise try the linux-surface kernel.")
    return _r("sam", t, "warn", "The Surface Aggregator driver isn't loaded. Older models (Surface 3, Pro 3) don't have one.",
              "Install the linux-surface kernel if your model is newer.", "software")


def c_input(x: Ctx):
    t = "Keyboard and touchpad"
    devs = pen._input_devices()
    if not devs:
        return _r("input", t, "skip", "Couldn't read the input device list.")
    # a keyboard is whatever the kernel gave a keyboard handler and LED control (Surface keyboards are named by
    # USB id, e.g. "Microsoft Surface 045E:09AE", not "keyboard")
    kb = [d for d in devs if "kbd" in d["handlers"] and "leds" in d["handlers"]]
    tp = [d for d in devs if re.search(r"touchpad|trackpad", d["name"], re.I)]
    if kb and tp:
        return _r("input", t, "ok", f"{len(kb)} keyboard and {len(tp)} touchpad device(s) found.")
    return _r("input", t, "warn", ("No keyboard found. " if not kb else "") + ("No touchpad found." if not tp else ""),
              "If you're using a Type Cover, check it is attached firmly.")


def c_touch(x: Ctx):
    t = "Touchscreen and pen"
    p = pen.state()
    if not p["touch_devices"] and not p["pen_devices"]:
        return _r("touch", t, "skip", "No touchscreen or pen devices detected (not every model has them).")
    if p["iptsd_active"]:
        return _r("touch", t, "ok", "Touch devices found and the touch service (iptsd) is running.")
    if p["iptsd_installed"]:
        return _r("touch", t, "warn", "Touch devices found but the touch service isn't running.",
                  "Restart it from Touch & Pen.", "pen")
    return _r("touch", t, "warn", "Touch devices found but iptsd isn't installed, so multitouch and the pen won't work.",
              "Install touch support from Touch & Pen.", "pen")


def c_display(x: Ctx):
    t = "Screen brightness control"
    s = display.state()["screen"]
    if s:
        return _r("display", t, "ok", f"Controllable ({s['name']}), currently {s['percent']}%.")
    return _r("display", t, "warn", "No controllable backlight found.")


def c_perf(x: Ctx):
    t = "Performance profiles"
    p = perf.state()
    if p.get("available"):
        return _r("perf", t, "ok", f"Available ({len(p['choices'])} profiles), currently {p['current']}.")
    return _r("perf", t, "warn", "The kernel doesn't expose a performance profile.",
              "The linux-surface kernel provides it on most models.", "software")


def c_battery(x: Ctx):
    t = "Battery"
    b = battery.state()
    if not b.get("present"):
        return _r("battery", t, "skip", "No battery found.")
    h, cyc = b.get("health_percent"), b.get("cycles")
    detail = f"{b['percent']}% · {b['status']}" + (f" · health {h}% of original" if h else "") + (f" · {cyc} cycles" if cyc else "")
    if h is None:
        return _r("battery", t, "ok", detail)
    if h < 60:
        return _r("battery", t, "fail", detail, "The battery has lost much of its capacity and may need replacing.")
    if h < 80:
        return _r("battery", t, "warn", detail, "Capacity is down noticeably; expect shorter battery life.")
    return _r("battery", t, "ok", detail)


def c_thermal(x: Ctx):
    t = "Temperatures and throttling"
    th = thermal.state()
    if not th["temps"]:
        return _r("thermal", t, "skip", "No temperature sensors found.")
    cpu = next((s for s in th["temps"] if s["label"] == "CPU"), th["temps"][0])
    thr = th["throttle"]["level"]
    detail = f"{cpu['label']} {cpu['celsius']:.0f} °C" + ("; the CPU is being slowed by heat" if thr else "; not throttling")
    if cpu["celsius"] >= 95:
        return _r("thermal", t, "fail", detail, "Dangerously hot. Check vents and the performance profile.", "performance")
    if cpu["celsius"] >= 85 or thr:
        return _r("thermal", t, "warn", detail, "Running hot. Try a cooler performance profile or thermald.", "performance")
    return _r("thermal", t, "ok", detail)


def c_firmware(x: Ctx):
    t = "Missing firmware"
    log = _kernel_log()
    if log is None:
        return _r("firmware", t, "skip", "Couldn't read the kernel log (needs the background service).")
    files = sorted(set(re.findall(r"Direct firmware load for (\S+) failed", log)))
    if files:
        return _r("firmware", t, "warn", "The kernel couldn't load: " + ", ".join(files[:5]) + (" …" if len(files) > 5 else ""),
                  "Update linux-firmware, or see the Fixes page for your model.", "fixes")
    return _r("firmware", t, "ok", "No firmware load failures this boot.")


def c_kernel_errors(x: Ctx):
    t = "Surface driver errors"
    log = _kernel_log()
    if log is None:
        return _r("kerrors", t, "skip", "Couldn't read the kernel log (needs the background service).")
    bad = [ln for ln in log.splitlines() if re.search(r"surface|ssam|ipts|ithc", ln, re.I)
           and re.search(r"\b(error|failed|timeout|timed out|fault)\b", ln, re.I)]
    if not bad:
        return _r("kerrors", t, "ok", "No Surface driver errors this boot.")
    who = re.match(r"\s*([A-Za-z_]+)", bad[0])
    detail = f"{len(bad)} driver message(s) mention errors" + (f" (first from {who.group(1)})" if who else "") + f": {bad[0][:140]}"
    hint = "If something isn't working, export the diagnostics bundle from Software & Updates and report it."
    if len(bad) >= 25:
        return _r("kerrors", t, "fail", detail, hint)
    return _r("kerrors", t, "warn", detail, hint)


def c_wifi(x: Ctx):
    t = "Wi-Fi"
    ifs = [d.name for d in sysfs.glob("/sys/class/net/*") if sysfs.p(d / "wireless").exists() or sysfs.p(d / "phy80211").exists()]
    if not ifs:
        return _r("wifi", t, "warn", "No Wi-Fi interface found.")
    log = _kernel_log() or ""
    crashes = len(re.findall(r"mwifiex.*(?:fw|firmware).*(?:fail|crash|timeout|hang)", log, re.I))
    if crashes:
        return _r("wifi", t, "warn", f"{ifs[0]} found, but the Marvell Wi-Fi firmware had {crashes} problem(s) this boot.",
                  "See the Wi-Fi fix on the Fixes page.", "fixes")
    return _r("wifi", t, "ok", f"{ifs[0]} found.")


def c_camera(x: Ctx):
    t = "Cameras"
    c = camera.state(x.dev.model)
    if c["video_nodes"]:
        return _r("camera", t, "ok", f"{len(c['video_nodes'])} camera device(s) found.")
    if c["wiki_status"] in ("ok", "partial"):
        return _r("camera", t, "warn", "No camera device found, but this model's camera can work.",
                  "See the Cameras page for drivers and setup.", "camera")
    return _r("camera", t, "skip", "No camera device found; the linux-surface wiki lists this model's camera as unsupported.")


def c_sleep(x: Ctx):
    t = "Sleep"
    s = sleep.state()
    mem = s["mem_sleep"]
    if not mem["modes"]:
        return _r("sleep", t, "skip", "Couldn't read sleep modes.")
    res = s.get("s0ix_residency_us")
    note = ""
    if res is not None:
        note = " Deep idle time recorded." if res > 0 else " No deep idle time recorded yet; suspend once and check again."
    return _r("sleep", t, "ok", f"Sleep mode {mem['current']} (available: {', '.join(mem['modes'])}).{note}")


def c_services(x: Ctx):
    t = "Background services"
    if not sysfs.have("systemctl"):
        return _r("services", t, "skip")
    act = lambda u: sysfs.run(["systemctl", "is-active", u]).stdout.strip() == "active"  # noqa: E731
    probs = []
    if sysfs.have("tlp") and act("tlp"):
        probs.append("TLP is running; the linux-surface project warns it can slow Surface devices")
    if sysfs.have("thermald") and not act("thermald") and x.dev.cpu_vendor == "intel":
        probs.append("thermald is installed but not running")
    failed = sysfs.run(["systemctl", "--failed", "--no-legend", "--plain"]).stdout.strip().splitlines()
    if failed:
        probs.append(f"{len(failed)} failed system unit(s): " + ", ".join(ln.split()[0] for ln in failed[:3]))
    if probs:
        return _r("services", t, "warn", "; ".join(probs) + ".", "", "performance" if "TLP" in probs[0] else None)
    return _r("services", t, "ok", "No failed units; no conflicting power tools found.")


def c_secureboot(x: Ctx):
    t = "Secure Boot and the Surface kernel key"
    sb = (sysfs.run(["mokutil", "--sb-state"]).stdout or "").lower()
    if "enabled" not in sb:
        return _r("secureboot", t, "ok" if "disabled" in sb else "skip", "Secure Boot is off." if "disabled" in sb else "Unknown.")
    cer = "/usr/share/linux-surface-secureboot/surface.cer"
    if not sysfs.p(cer).exists():
        return _r("secureboot", t, "ok" if x.dev.surface_kernel else "warn",
                  "Secure Boot is on." + ("" if x.dev.surface_kernel else " The linux-surface key package isn't installed."),
                  "" if x.dev.surface_kernel else "Install the Secure Boot key from Software & Updates.",
                  None if x.dev.surface_kernel else "software")
    r = sysfs.run(["mokutil", "--test-key", cer])
    if "already enrolled" in (r.stdout + r.stderr):
        return _r("secureboot", t, "ok", "Secure Boot is on and the linux-surface key is enrolled.")
    return _r("secureboot", t, "warn", "Secure Boot is on but the linux-surface key isn't enrolled, so that kernel won't boot.",
              "Restart and complete the key screen (password: surface).", "software")


def c_repo(x: Ctx):
    t = "Update source"
    if x.distro.family not in ("debian", "fedora", "arch"):
        return _r("repo", t, "skip", "Not applicable on this distribution.")
    if planner.repo_present(x.distro):
        return _r("repo", t, "ok", "The linux-surface repository is set up.")
    return _r("repo", t, "warn", "The linux-surface repository isn't set up, so new kernels and drivers can't be found.",
              "Set it up from Software & Updates.", "software")


def c_disk(x: Ctx):
    t = "Disk space"
    out, probs = [], []
    for mount, need_mb in (("/", 2048), ("/boot", 300)):
        try:
            u = shutil.disk_usage(sysfs.p(mount))
        except OSError:
            continue
        free_mb = u.free // (1024 * 1024)
        out.append(f"{mount}: {free_mb} MB free")
        if free_mb < need_mb:
            probs.append(f"{mount} has only {free_mb} MB free")
    if not out:
        return _r("disk", t, "skip")
    if probs:
        return _r("disk", t, "warn", "; ".join(probs) + ". Kernel updates need room in /boot.",
                  "Remove old kernels with: sudo apt autoremove (Debian/Ubuntu).")
    return _r("disk", t, "ok", " · ".join(out))


def c_dgpu(x: Ctx):
    t = "Discrete GPU"
    from .hw import book
    g = book.dgpu()
    if not g:
        return _r("dgpu", t, "skip", "No discrete GPU.")
    if g.get("driver"):
        return _r("dgpu", t, "ok", f"Using the {g['driver']} driver; power state {g.get('power_state') or '?'}.")
    return _r("dgpu", t, "warn", "A discrete GPU is present but no driver is bound to it.",
              "Install the NVIDIA driver from Surface Book or Software & Updates.", "software")


CHECKS: list[tuple[str, Callable[[Ctx], dict]]] = [
    ("kernel", c_kernel), ("sam", c_sam), ("input", c_input), ("touch", c_touch), ("display", c_display),
    ("perf", c_perf), ("battery", c_battery), ("thermal", c_thermal), ("firmware", c_firmware),
    ("kerrors", c_kernel_errors), ("wifi", c_wifi), ("camera", c_camera), ("dgpu", c_dgpu), ("sleep", c_sleep),
    ("services", c_services), ("secureboot", c_secureboot), ("repo", c_repo), ("disk", c_disk),
]


def check_ids() -> list[str]:
    return [i for i, _ in CHECKS]


def run_check(check_id: str, ctx: Ctx) -> dict:
    for i, fn in CHECKS:
        if i == check_id:
            try:
                return fn(ctx)
            except Exception as e:  # noqa: BLE001 - one broken probe must not stop the test
                return _r(i, i, "skip", f"The check itself failed: {e}")
    raise KeyError(check_id)


def summarize(results: list[dict]) -> dict:
    counts = {k: sum(1 for r in results if r["status"] == k) for k in ("ok", "warn", "fail", "skip")}
    verdict = "fail" if counts["fail"] else "warn" if counts["warn"] else "ok"
    return {"counts": counts, "verdict": verdict}


def report_text(results: list[dict], device: dict | None = None, stress: dict | None = None) -> str:
    """Plain-text report to paste into a bug report. Contains no personal data beyond the device model and OS."""
    mark = {"ok": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
    s = summarize(results)
    lines = ["Surface Companion system test", ""]
    if device:
        lines.append(f"Device: {device.get('model', '?')} · kernel {device.get('kernel', '?')} · {device.get('distro', '')}".rstrip(" ·"))
    c = s["counts"]
    lines += [f"Result: {mark[s['verdict']]}  ({c['ok']} passed, {c['warn']} warnings, {c['fail']} failed, {c['skip']} skipped)", ""]
    for r in results:
        lines.append(f"[{mark[r['status']]}] {r['title']}" + (f": {r['detail']}" if r["detail"] else ""))
        if r["hint"] and r["status"] in ("warn", "fail"):
            lines.append(f"       -> {r['hint']}")
    if stress:
        lines += ["", "Stress test: " + stress.get("summary", "")]
    return "\n".join(lines) + "\n"


TITLES = {
    "kernel": "Surface kernel", "sam": "Surface Aggregator (keyboard, battery, performance mode)",
    "input": "Keyboard and touchpad", "touch": "Touchscreen and pen", "display": "Screen brightness control",
    "perf": "Performance profiles", "battery": "Battery", "thermal": "Temperatures and throttling",
    "firmware": "Missing firmware", "kerrors": "Surface driver errors", "wifi": "Wi-Fi", "camera": "Cameras",
    "dgpu": "Discrete GPU", "sleep": "Sleep", "services": "Background services",
    "secureboot": "Secure Boot and the Surface kernel key", "repo": "Update source", "disk": "Disk space",
}
