"""Everything the privileged daemon can be asked to do.

Pure Python with no D-Bus in it, so it can be tested against a fake sysfs tree.
Each action declares the polkit action that must be authorised before it runs.
"""
from __future__ import annotations

import json
import os
import re
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import device, fixes, sysfs
from .fixes.base import Ctx
from .hw import battery, book, camera, clocks, display, pen, perf, sleep, thermal
from .pkg import community, distro as dist, nvidia, planner

POLKIT_READ = ""                                   # no authorisation needed
POLKIT_CONTROL = "org.surfacecompanion.control"
POLKIT_DETACH = "org.surfacecompanion.detach"
POLKIT_ADMIN = "org.surfacecompanion.admin"

STATE_DIR = Path(os.environ.get("SC_STATE_DIR", "/var/lib/surface-companion"))
Log = Callable[[str], None]


class Settings:
    """Values re-applied at start and after resume, because firmware tends to reset them."""
    def __init__(self, path: Path | None = None):
        self.path = path or STATE_DIR / "settings.json"
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def set(self, key, value) -> None:
        self.data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1))
        except OSError:
            pass

    def reapply(self) -> list[str]:
        errors = []
        steps = [("profile", perf.set_profile), ("mem_sleep", sleep.set_mem_sleep),
                 ("charge_limit", battery.set_charge_limit), ("dgpu_always_on", book.set_dgpu_power)]
        for key, fn in steps:
            if key in self.data:
                try:
                    fn(self.data[key])
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{key}: {e}")
        return errors


class _TTL:
    def __init__(self, seconds: float):
        self.seconds, self.t, self.v = seconds, 0.0, None

    def get(self, fn):
        now = time.monotonic()
        if self.v is None or now - self.t > self.seconds:
            self.v, self.t = fn(), now
        return self.v


_dev_cache = _TTL(3600)
_distro_cache = _TTL(3600)
_thermal_cache = _TTL(8)
_slow_cache = _TTL(15)


def get_device() -> device.Device:
    return _dev_cache.get(device.detect)


def get_distro() -> dist.Distro:
    return _distro_cache.get(dist.detect)


def fast_state() -> dict:
    dev = get_device()
    return {
        "device": dev.to_dict(),
        "display": display.state(),
        "performance": perf.state(),
        "thermal": {**_thermal_cache.get(thermal.state)},
        "clocks": clocks.state(),
        "battery": battery.state(),
        "sleep": sleep.state(),
        "book": book.state() if dev.is_book or dev.has_dtx else {"dtx": False, "dgpu": None},
    }


def slow_state() -> dict:
    dev = get_device()
    return _slow_cache.get(lambda: {"pen": pen.state(), "camera": camera.state(dev.model)})


# ------------------------------------------------------------------ action implementations

def _ctx() -> Ctx:
    return Ctx(get_device(), get_distro())


def diagnostics(log: Log, out_dir: str | Path | None = None) -> str:
    """Collect a support bundle; same spirit as the project's scripts/diagnostics.sh."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    outdir = Path(out_dir) if out_dir else STATE_DIR
    outdir.mkdir(parents=True, exist_ok=True)
    work = outdir / f"diag-{stamp}"
    work.mkdir()
    cmds = {
        "uname.txt": ["uname", "-a"], "lsmod.txt": ["lsmod"], "lspci.txt": ["lspci", "-nnk"],
        "lsusb.txt": ["lsusb"], "dmesg-surface.txt": ["sh", "-c", "dmesg | grep -iE 'surface|ipts|ithc|ipu|iwlwifi|mwifiex|firmware'"],
        "failed-units.txt": ["systemctl", "--failed", "--no-pager"],
        "mokutil.txt": ["mokutil", "--sb-state"],
        "modules-ssam.txt": ["sh", "-c", "ls /sys/bus/surface_aggregator/devices"],
    }
    for name, argv in cmds.items():
        r = sysfs.run(argv, timeout=30)
        (work / name).write_text(r.stdout + r.stderr)
    dmi = {k: sysfs.read(f"/sys/class/dmi/id/{k}", "") for k in
           ("sys_vendor", "product_name", "product_family", "product_sku", "bios_version", "bios_date")}
    (work / "dmi.json").write_text(json.dumps(dmi, indent=1))
    (work / "state.json").write_text(json.dumps(fast_state(), indent=1))
    (work / "os-release").write_text(sysfs.read("/etc/os-release", "") or "")
    (work / "cmdline").write_text(sysfs.read("/proc/cmdline", "") or "")
    archive = outdir / f"surface-diagnostics-{stamp}.tar.gz"
    with tarfile.open(archive, "w:gz") as t:
        t.add(work, arcname=work.name)
    for f in work.iterdir():
        f.unlink()
    work.rmdir()
    os.chmod(archive, 0o644)
    log(f"Wrote {archive}")
    return str(archive)


@dataclass
class Action:
    polkit: str
    fn: Callable
    job: bool = False       # long-running: runs in the background, streams output


def build(settings: Settings) -> dict[str, Action]:
    def persist(key, fn):
        def run(a):
            fn(a)
            settings.set(key, a[key] if key in a else None)
        return run

    def apply_fix(a, log):
        f = fixes.get(a["id"])
        if f.scope != "system" or not f.apply:
            raise RuntimeError("This fix is not applied by the background service")
        f.apply(_ctx(), log)
        return f"{f.title}: done" + (" (restart to finish)" if f.reboot else "")

    def revert_fix(a, log):
        f = fixes.get(a["id"])
        if not f.revert:
            raise RuntimeError("This fix cannot be undone from here")
        f.revert(_ctx(), log)
        return f"{f.title}: undone"

    def do_install(a, log):
        plan = planner.plan_install(a["ids"], get_device(), get_distro())
        if plan.level == "unsupported" and not a.get("accept_unsupported"):
            raise ValueError("This system is not officially supported. The warning must be accepted first.")
        ok = planner.run_plan(plan, log, dry_run=bool(a.get("dry_run")))
        if not ok:
            raise RuntimeError("Installation did not complete")
        return "Restart to finish." if plan.reboot and not a.get("dry_run") else "Complete."

    def do_updates(a, log):
        for n in a["pkgs"]:
            if not re.fullmatch(r"[a-z0-9][a-z0-9+._-]*", str(n)):
                raise ValueError(f"Invalid package name: {n!r}")
        plan = planner.plan_update(get_distro(), a["pkgs"])
        if not planner.run_plan(plan, log, dry_run=bool(a.get("dry_run"))):
            raise RuntimeError("Update did not complete")
        return "Restart to finish." if plan.reboot else "Complete."

    def _run(argv, log, cwd=None, check=True):
        log("$ " + " ".join(argv))
        rc = planner._stream(argv, log, cwd=cwd)
        if rc and check:
            raise RuntimeError(f"{argv[0]} failed ({rc})")
        return rc

    def _community(a):
        drv = community.by_id(a["id"])
        if drv not in community.for_device(get_device()):
            raise ValueError("That driver is not for this device")
        return drv

    def install_community(a, log):
        drv = _community(a)
        if not a.get("accept_risk"):
            raise ValueError("The risk warning must be accepted first.")
        if drv.mode != "dkms":
            raise ValueError("This driver's installer runs as your user, not from the background service.")
        src = community.fetch(drv, STATE_DIR / "community", log)
        community.dkms_install(drv, get_device(), src, get_distro().family, log, _run)
        return "Installed. Restart, then test the cameras."

    def remove_community(a, log):
        drv = _community(a)
        if drv.mode != "dkms":
            raise ValueError("Undo this driver from its own installer (it runs as your user).")
        community.dkms_remove(drv, log, _run)
        return "Removed. Restart to finish."

    def get_community(a):
        out = []
        for drv in community.for_device(get_device()):
            out.append({"id": drv.id, "title": drv.title, "summary": drv.summary, "risks": drv.risks,
                        "url": drv.url, "commit": drv.commit, "mode": drv.mode, "license": drv.license,
                        "language": drv.language, "not_included": drv.not_included, "repo": drv.repo,
                        "installed": community.installed(drv)})
        return out

    def install_nvidia(a, log):
        pl = nvidia.plan(get_device(), get_distro())
        if pl["blockers"]:
            raise RuntimeError(" ".join(pl["blockers"]))
        if pl["interactive"]:
            raise RuntimeError("Secure Boot is on, so this has to run in a terminal where you can answer the key prompts.")
        steps = [planner.Step(s["title"], s["kind"], argv=s["argv"]) for s in pl["steps"]]
        if not planner.run_plan(planner.Plan(steps=steps), log):
            raise RuntimeError("Installing the NVIDIA driver did not complete")
        return "Installed. Restart to start using the NVIDIA driver."

    def check_updates(a, log):
        log("Checking for updates...")
        return json.dumps(planner.check_updates(get_distro()))

    def setup_repo(a, log):
        d = get_distro()
        if planner.assess(get_device(), d)["level"] == "blocked" or d.immutable or d.family not in ("debian", "fedora", "arch"):
            raise RuntimeError("The repository can't be set up automatically on this system.")
        plan = planner.Plan(steps=planner.repo_steps_for(d))
        if not planner.run_plan(plan, log):
            raise RuntimeError("Setting up the repository did not complete")
        return "Repository is set up. Check for updates again."

    return {
        # reads
        "get_state": Action(POLKIT_READ, lambda a: fast_state()),
        "get_slow_state": Action(POLKIT_READ, lambda a: slow_state()),
        "get_fixes": Action(POLKIT_READ, lambda a: [x for x in fixes.evaluate(_ctx())]),
        "get_software": Action(POLKIT_READ, lambda a: {
            "distro": get_distro().to_dict(),
            "system": planner.system_status(get_device(), get_distro()),
            "assessment": planner.assess(get_device(), get_distro()),
            "nvidia": nvidia.info(),
            "components": planner.component_status(get_device(), get_distro())}),
        "get_community": Action(POLKIT_READ, get_community),
        "get_nvidia": Action(POLKIT_READ, lambda a: {**nvidia.info(), "plan": nvidia.plan(get_device(), get_distro())}),
        "get_kernel": Action(POLKIT_READ, lambda a: planner.kernel_status(get_device(), get_distro())),
        "plan_install": Action(POLKIT_READ, lambda a: planner.plan_install(a["ids"], get_device(), get_distro()).to_dict()),
        # hardware controls
        "set_brightness": Action(POLKIT_CONTROL, lambda a: display.set_screen_percent(a["percent"])),
        "set_keyboard": Action(POLKIT_CONTROL, lambda a: display.set_keyboard_level(a["level"])),
        "set_profile": Action(POLKIT_CONTROL, persist("profile", lambda a: perf.set_profile(a["profile"]))),
        "set_mem_sleep": Action(POLKIT_CONTROL, persist("mem_sleep", lambda a: sleep.set_mem_sleep(a["mem_sleep"]))),
        "set_lid_wake": Action(POLKIT_CONTROL, lambda a: sleep.set_lid_wake(bool(a["enabled"]))),
        "set_lid_action": Action(POLKIT_CONTROL, lambda a: sleep.set_lid_action(a["action"])),
        "set_charge_limit": Action(POLKIT_CONTROL, persist("charge_limit", lambda a: battery.set_charge_limit(a["charge_limit"]))),
        "set_dgpu_power": Action(POLKIT_CONTROL, persist("dgpu_always_on", lambda a: book.set_dgpu_power(bool(a["dgpu_always_on"])))),
        "restart_touch": Action(POLKIT_CONTROL, lambda a: pen.restart_service()),
        "sleep_now": Action(POLKIT_CONTROL, lambda a: sleep.suspend_now(a.get("kind", "suspend"))),
        "detach_clipboard": Action(POLKIT_DETACH, lambda a: book.detach()),
        # privileged jobs
        "apply_fix": Action(POLKIT_ADMIN, apply_fix, job=True),
        "revert_fix": Action(POLKIT_ADMIN, revert_fix, job=True),
        "install": Action(POLKIT_ADMIN, do_install, job=True),
        "setup_repo": Action(POLKIT_ADMIN, setup_repo, job=True),
        "install_nvidia": Action(POLKIT_ADMIN, install_nvidia, job=True),
        "install_community": Action(POLKIT_ADMIN, install_community, job=True),
        "remove_community": Action(POLKIT_ADMIN, remove_community, job=True),
        "check_updates": Action(POLKIT_READ, check_updates, job=True),
        "apply_updates": Action(POLKIT_ADMIN, do_updates, job=True),
        "diagnostics": Action(POLKIT_ADMIN, lambda a, log: diagnostics(log), job=True),
    }
