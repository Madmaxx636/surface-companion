"""Short CPU stress test with a temperature safety stop.

Loads every CPU core for a few seconds and watches clock speed, temperature, fan and thermal throttling. It stops at
once if the CPU gets too hot. It tells you whether the machine holds its speed under load, which is what the
performance profile and cooling are for. Graphics and memory are not stressed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Callable

from . import sysfs
from .hw import battery, clocks, thermal

ABORT_C = 95.0
MIN_BATTERY = 15
BURN = "x = 1\nwhile True:\n    x = (x * 1103515245 + 12345) % 2147483648\n"


def spawn_burners(n: int | None = None) -> list[subprocess.Popen]:
    n = n or os.cpu_count() or 1
    return [subprocess.Popen([sys.executable, "-I", "-c", BURN], preexec_fn=lambda: os.nice(10),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(n)]


def sample() -> dict:
    th = thermal.state()
    cpu = next((t for t in th["temps"] if t["label"] == "CPU"), th["temps"][0] if th["temps"] else None)
    c = clocks.cpu()
    return {"temp": cpu["celsius"] if cpu else None, "clock": c["avg_mhz"] if c else None,
            "throttle": th["throttle"]["level"], "fan": th["fans"][0]["rpm"] if th["fans"] else None}


def precheck() -> str | None:
    b = battery.state()
    if b.get("present") and b.get("status") == "Discharging" and (b.get("percent") or 100) < MIN_BATTERY:
        return f"The battery is at {b['percent']}%. Plug in before running the stress test."
    return None


def run(seconds: int, log: Callable[[str], None], spawn=spawn_burners, take=sample, sleep=time.sleep,
        abort_c: float = ABORT_C, stop: Callable[[], bool] = lambda: False) -> dict:
    if not 5 <= seconds <= 300:
        raise ValueError("The stress test runs between 5 and 300 seconds")
    problem = precheck()
    if problem:
        raise RuntimeError(problem)
    base = take()
    log(f"Idle: {base['temp']} °C, {base['clock']} MHz" if base["temp"] is not None else "Idle sample taken")
    procs = []
    samples: list[dict] = []
    aborted = ""
    try:
        procs = spawn()
        log(f"Loading {len(procs)} CPU threads for {seconds} s. Stops automatically above {abort_c:.0f} °C.")
        for t in range(1, seconds + 1):
            sleep(1)
            if stop():
                aborted = "Stopped by you."
                break
            s = take()
            samples.append(s)
            fan = f", fan {s['fan']} RPM" if s["fan"] else ""
            log(f"{t:>3}s  {s['temp']} °C  {s['clock']} MHz  throttle {s['throttle']}{fan}")
            if s["temp"] is not None and s["temp"] >= abort_c:
                aborted = f"Stopped early: the CPU reached {s['temp']:.0f} °C."
                break
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=3)
            except Exception:  # noqa: BLE001
                p.kill()
    temps = [s["temp"] for s in samples if s["temp"] is not None]
    clk = [s["clock"] for s in samples if s["clock"] is not None]
    peak = max(temps) if temps else None
    throttled = any(s["throttle"] for s in samples)
    first, last = (clk[0], clk[-1]) if clk else (None, None)
    avg_late = sum(clk[-max(1, len(clk) // 3):]) / max(1, len(clk[-max(1, len(clk) // 3):])) if clk else None
    drop = bool(first and avg_late and avg_late < first * 0.7)
    if aborted and aborted.startswith("Stopped early"):
        status, summary = "fail", aborted + " Check the vents and the performance profile."
    elif aborted:
        status, summary = "skip", aborted
    elif throttled or drop:
        status = "warn"
        summary = (f"The CPU slowed down under load" + (f" (from {first / 1000:.1f} GHz to about {avg_late / 1000:.1f} GHz)" if drop else "")
                   + (f", thermal throttling was active" if throttled else "") + f". Peak {peak:.0f} °C." if peak is not None else ".")
    else:
        status = "ok"
        summary = (f"Held about {sum(clk) / len(clk) / 1000:.1f} GHz for {len(samples)} s with no throttling, peak {peak:.0f} °C."
                   if clk and peak is not None else f"Completed {len(samples)} s with no throttling.")
    return {"status": status, "summary": summary, "peak_temp": peak, "clock_start": first, "clock_end": last,
            "throttled": throttled, "seconds": len(samples), "idle_temp": base["temp"]}
