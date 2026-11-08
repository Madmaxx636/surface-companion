"""Suspend, resume and wake sources."""
from __future__ import annotations

from .. import sysfs

LOGIND_DROPIN = "/etc/systemd/logind.conf.d/50-surface-companion.conf"
MARK = "# Managed by Surface Companion\n"
LID_ACTIONS = ("suspend", "ignore", "lock", "poweroff", "hibernate")


def _lid_device():
    for d in sysfs.glob("/sys/bus/acpi/devices/PNP0C0D:*"):
        if sysfs.p(d / "power/wakeup").exists():
            return d
    return None


def _lid_action() -> str:
    txt = sysfs.read(LOGIND_DROPIN, "") or ""
    for line in txt.splitlines():
        if line.startswith("HandleLidSwitch="):
            return line.split("=", 1)[1].strip()
    return "default"


def state() -> dict:
    ms = sysfs.read("/sys/power/mem_sleep", "") or ""
    modes = [m.strip("[]") for m in ms.split()]
    cur = next((m.strip("[]") for m in ms.split() if m.startswith("[")), None)
    res = sysfs.read_int("/sys/devices/system/cpu/cpuidle/low_power_idle_system_residency_us")
    lid = _lid_device()
    disk = sysfs.read("/sys/power/disk", "") or ""
    return {
        "mem_sleep": {"modes": modes, "current": cur},
        "s0ix_residency_us": res,
        "can_hibernate": "disk" in (sysfs.read("/sys/power/state", "") or "") and bool(disk),
        "lid_wake": None if lid is None else (sysfs.read(lid / "power/wakeup") == "enabled"),
        "lid_action": _lid_action(),
    }


def set_mem_sleep(mode: str) -> None:
    modes = state()["mem_sleep"]["modes"]
    if mode not in modes:
        raise ValueError(f"Unsupported sleep mode: {mode}")
    sysfs.write("/sys/power/mem_sleep", mode)


def set_lid_wake(enabled: bool) -> None:
    lid = _lid_device()
    if lid is None:
        raise RuntimeError("No lid wake source found")
    sysfs.write(lid / "power/wakeup", "enabled" if enabled else "disabled")


def set_lid_action(action: str) -> None:
    """`default` removes our override so the system (logind / the desktop) decides again."""
    if action != "default" and action not in LID_ACTIONS:
        raise ValueError(f"Unsupported lid action: {action}")
    path = sysfs.p(LOGIND_DROPIN)
    if action == "default":
        if path.exists() and (sysfs.read(LOGIND_DROPIN, "") or "").startswith(MARK.strip()):
            path.unlink()
            sysfs.run(["systemctl", "kill", "-s", "HUP", "systemd-logind.service"])
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{MARK}[Login]\nHandleLidSwitch={action}\nHandleLidSwitchExternalPower={action}\n")
    # logind re-reads its config on SIGHUP; restarting it would drop sessions.
    sysfs.run(["systemctl", "kill", "-s", "HUP", "systemd-logind.service"])


def suspend_now(kind: str = "suspend") -> None:
    if kind not in ("suspend", "hibernate"):
        raise ValueError(kind)
    r = sysfs.run(["systemctl", kind], timeout=30)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or f"{kind} failed")
