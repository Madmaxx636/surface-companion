"""Turn "install X" into an explicit, reviewable list of steps, and run them."""
from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from .. import sysfs
from ..device import Device
from . import catalog, distro as dist

Log = Callable[[str], None]


@dataclass
class Step:
    title: str
    kind: str                       # cmd | write | download | key | append
    argv: list[str] = field(default_factory=list)
    path: str = ""
    content: str = ""
    url: str = ""
    mode: int = 0o644

    def describe(self) -> str:
        if self.kind == "cmd":
            return " ".join(self.argv)
        if self.kind == "write":
            return f"write {self.path}"
        if self.kind == "download":
            return f"download {self.url} -> {self.path}"
        if self.kind == "key":
            return f"fetch and verify signing key ({catalog.KEY_FPR_SUFFIX})"
        if self.kind == "append":
            return f"add repository to {self.path}"
        return self.title

    def to_dict(self) -> dict:
        d = asdict(self)
        d["describe"] = self.describe()
        d.pop("content", None)
        return d


@dataclass
class Plan:
    steps: list[Step] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    reboot: bool = False
    level: str = "supported"        # supported | unsupported | blocked
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"steps": [s.to_dict() for s in self.steps], "notes": self.notes,
                "blockers": self.blockers, "reboot": self.reboot, "level": self.level, "risks": self.risks}


def applicable(c: catalog.Component, dev: Device, d: dist.Distro) -> tuple[bool, str]:
    if d.family not in c.packages:
        return False, f"No package is published for {d.name or 'this distribution'}"
    if c.models and not dev.model_in(c.models):
        return False, "Only for " + " and ".join(c.models)
    if c.only_book and not dev.is_book:
        return False, "Only for Surface Book"
    if c.only_intel and dev.cpu_vendor != "intel":
        return False, "thermald supports Intel CPUs only"
    if c.id == "libwacom" and not dist.libwacom_surface_ok(d):
        return False, "Known to break on this release (linux-surface issue 2076)"
    return True, ""


def _repo_steps(d: dist.Distro) -> list[Step]:
    if d.family == "debian":
        return [
            Step("Fetch and verify the linux-surface signing key", "key", path=catalog.APT_KEYRING),
            Step("Add the linux-surface apt repository", "write", path=catalog.APT_LIST,
                 content=catalog.APT_LINE + "\n"),
            Step("Refresh package lists", "cmd", argv=["apt-get", "update"]),
        ]
    if d.family == "fedora":
        return [Step("Add the linux-surface repository", "download", url=catalog.DNF_REPO,
                     path="/etc/yum.repos.d/linux-surface.repo")]
    if d.family == "arch":
        return [
            Step("Fetch and verify the linux-surface signing key", "key", path="/tmp/linux-surface.asc"),
            Step("Import the key into pacman", "cmd", argv=["pacman-key", "--add", "/tmp/linux-surface.asc"]),
            Step("Trust the key locally", "cmd", argv=["pacman-key", "--lsign-key", catalog.KEY_FPR_SUFFIX]),
            Step("Add the linux-surface repository", "append", path="/etc/pacman.conf",
                 content=catalog.PACMAN_BLOCK),
        ]
    return []


def _active_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def repo_present(d: dist.Distro) -> bool:
    """True only if the linux-surface repository is really configured, not just a leftover empty file.

    A distribution upgrade can leave /etc/apt/sources.list.d/linux-surface.list behind but blank (third-party
    sources are disabled during release upgrades), so existence of the file proves nothing.
    """
    if d.family == "debian":
        files = [sysfs.p("/etc/apt/sources.list")] + sysfs.glob("/etc/apt/sources.list.d/*.list") \
            + sysfs.glob("/etc/apt/sources.list.d/*.sources")
        for f in files:
            txt = sysfs.read(f, "") or ""
            lines = _active_lines(txt)
            if f.suffix == ".sources":
                blocks = [b for b in txt.split("\n\n") if "pkg.surfacelinux.com" in b and "enabled: no" not in b.lower()]
                if any(not ln.lstrip().startswith("#") for b in blocks for ln in b.splitlines()):
                    return True
            elif any("pkg.surfacelinux.com" in ln for ln in lines):
                return True
        return False
    if d.family == "fedora":
        txt = sysfs.read("/etc/yum.repos.d/linux-surface.repo", "") or ""
        return "pkg.surfacelinux.com" in txt and "enabled=0" not in txt.replace(" ", "")
    if d.family == "arch":
        import re as _re
        return bool(_re.search(r"^\[linux-surface\]", sysfs.read("/etc/pacman.conf", "") or "", _re.M))
    if d.family == "suse":
        return any("TaivasJumala" in (sysfs.read(f, "") or "") for f in sysfs.glob("/etc/zypp/repos.d/*.repo"))
    return False


def repo_steps_for(d: dist.Distro) -> list[Step]:
    return _repo_steps(d)


def assess(dev: Device, d: dist.Distro) -> dict:
    """How well supported is installing here? supported | unsupported (allowed after a warning) | blocked."""
    blocked, risks = [], []
    machine = platform.machine()
    if machine not in ("x86_64", "AMD64"):
        blocked.append(f"The linux-surface packages are built for x86-64 only; this is {machine}.")
    if d.family == "unknown":
        blocked.append("No supported package manager (apt, dnf, pacman or zypper) was found. Build the kernel from "
                       "source following the linux-surface wiki, or use your distribution's own packaging.")
    if d.family == "suse" and not d.tumbleweed:
        blocked.append("The only openSUSE build is for Tumbleweed. Installing it on another openSUSE release would "
                       "mix incompatible kernel packages.")
    if blocked:
        return {"level": "blocked", "reasons": blocked}
    if d.guessed:
        risks.append(f"Your distribution ({d.name or d.id or 'unknown'}) was not recognised. Because the "
                     f"{ {'debian': 'apt-get', 'fedora': 'dnf', 'arch': 'pacman', 'suse': 'zypper'}[d.family] } "
                     f"command exists, the {d.family.title()} instructions will be tried. The repositories are built "
                     "for specific releases and may not fit yours.")
    if d.immutable:
        risks.append("This is an image-based system. The wiki's recipe swaps the stock kernel and libwacom out of "
                     "the OS image using a placeholder package from 2020 downloaded from GitHub. If the new image "
                     "does not boot, choose the previous entry in the boot menu or run “rpm-ostree rollback”.")
        if dev.model_in(("Surface Book 2",)):
            risks.append("The linux-surface wiki says ALL Fedora Atomic desktops hang at boot on the Surface Book 2.")
    if d.tumbleweed:
        risks.append("The openSUSE kernel comes from one volunteer's personal build service repository, not from "
                     "linux-surface, and its maintainer says it is not actively maintained. Its signing key will be "
                     "trusted automatically. It has no iptsd or libwacom; touch and pen need extra work.")
    if risks:
        risks += ["This replaces or adds a kernel and changes boot entries. Keep your original kernel and make sure "
                  "you can reach your boot menu.",
                  "Secure Boot may refuse the new kernel until its key is enrolled."]
        return {"level": "unsupported", "reasons": risks}
    return {"level": "supported", "reasons": []}


def _ostree_steps(ids: list[str], pkgs_by_id: dict[str, list[str]], plan: Plan) -> None:
    rpm = "/var/tmp/surface-companion-kernel.rpm"
    plan.steps.append(Step("Add the linux-surface repository", "download", url=catalog.DNF_REPO,
                           path="/etc/yum.repos.d/linux-surface.repo"))
    if "kernel" in ids:
        plan.steps.append(Step("Download the placeholder kernel package", "download", url=catalog.SILVERBLUE_RPM, path=rpm))
        plan.steps.append(Step("Replace the stock kernel in the OS image", "cmd", argv=[
            "rpm-ostree", "override", "replace", rpm,
            "--remove", "kernel-core", "--remove", "kernel-modules", "--remove", "kernel-modules-extra",
            "--remove", "libwacom", "--remove", "libwacom-data",
            "--install", "kernel-surface", "--install", "iptsd",
            "--install", "libwacom-surface", "--install", "libwacom-surface-data"]))
        rest = [p for i, ps in pkgs_by_id.items() if i not in ("kernel", "iptsd", "libwacom", "secureboot") for p in ps]
    else:
        rest = [p for i, ps in pkgs_by_id.items() if i != "secureboot" for p in ps]
    if rest:
        plan.steps.append(Step("Layer packages onto the image", "cmd", argv=["rpm-ostree", "install", *rest]))
    if "secureboot" in ids:
        plan.steps.append(Step("Add the Secure Boot key (on its own, as the wiki asks)", "cmd",
                               argv=["rpm-ostree", "install", "surface-secureboot"]))
    plan.notes.append("The change takes effect on the next boot. Undo with “rpm-ostree rollback”.")


def active_session_users() -> list[str]:
    """Names of people logged in at the machine right now (local, graphical or console; never root)."""
    out: list[str] = []
    r = sysfs.run(["loginctl", "list-sessions", "--no-legend"])
    for ln in r.stdout.splitlines():
        parts = ln.split()
        if not parts:
            continue
        info = sysfs.run(["loginctl", "show-session", parts[0], "-p", "Name", "-p", "Active", "-p", "Class", "-p", "Remote"]).stdout
        kv = dict(x.split("=", 1) for x in info.splitlines() if "=" in x)
        if kv.get("Active") == "yes" and kv.get("Class") == "user" and kv.get("Remote") != "yes" \
                and kv.get("Name") and kv["Name"] != "root" and kv["Name"] not in out:
            out.append(kv["Name"])
    return out


def _unit_state(argv: list[str]) -> str:
    r = sysfs.run(argv)
    return (r.stdout or "").strip() or "unknown"


def service_states(c: catalog.Component) -> dict:
    """Is each unit of this component running? System units, and user units for each logged-in user."""
    st: dict = {"system": {u: _unit_state(["systemctl", "is-active", u]) for u in c.services}, "user": {}}
    if c.user_services:
        for user in active_session_users():
            st["user"][user] = {u: _unit_state(["systemctl", "--user", f"--machine={user}@.host", "is-active", u])
                                for u in c.user_services}
    return st


def services_running(st: dict) -> bool:
    return all(v == "active" for v in st["system"].values()) and \
        all(v == "active" for per in st["user"].values() for v in per.values()) and (bool(st["system"]) or bool(st["user"]))


def _service_steps(selected: list[catalog.Component], plan: Plan) -> None:
    """After installing, turn the component's services on (system, and --user for whoever is logged in)."""
    for c in selected:
        for unit in c.services:
            plan.steps.append(Step(f"Enable and start {unit}", "cmd", argv=["systemctl", "enable", "--now", unit]))
        if c.user_services:
            users = active_session_users()
            for unit in c.user_services:
                for user in users:
                    plan.steps.append(Step(f"Enable and start {unit} for {user}", "cmd",
                                           argv=["systemctl", "--user", f"--machine={user}@.host", "enable", "--now", unit]))
                if not users:
                    plan.steps.append(Step(f"Enable {unit} for everyone's next login", "cmd",
                                           argv=["systemctl", "--global", "enable", unit]))
                    plan.notes.append(f"Nobody is logged in right now, so {unit} is enabled for the next login instead.")


def plan_install(ids: list[str], dev: Device, d: dist.Distro) -> Plan:
    plan = Plan()
    verdict = assess(dev, d)
    plan.level, plan.risks = verdict["level"], verdict["reasons"]
    if verdict["level"] == "blocked":
        plan.blockers += verdict["reasons"]
        return plan
    pkgs_by_id: dict[str, list[str]] = {}
    need_repo = False
    allow_erase = False
    for cid in ids:
        c = catalog.by_id(cid)
        ok, why = applicable(c, dev, d)
        if not ok:
            plan.notes.append(f"Skipped {c.title}: {why}")
            continue
        pkgs_by_id[cid] = list(c.packages[d.family])
        need_repo |= c.needs_repo and d.family != "suse"
        plan.reboot |= c.reboot
        allow_erase |= (cid == "kernel" and d.family == "fedora")
        if cid == "libwacom" and d.family == "arch":
            plan.notes.append("libwacom-surface is only in the AUR on Arch; install it with your AUR helper.")
    if "libwacom" in ids and not dist.libwacom_surface_ok(d):
        plan.notes.append("libwacom-surface skipped: on this release the linux-surface wiki says it removes the GNOME "
                          "desktop (issue 2076).")
    if d.family == "arch" and "kernel" in pkgs_by_id:
        if dev.model_in(("Surface Pro 4", "Surface Pro 5", "Surface Pro 6", "Surface Book", "Surface Book 2",
                         "Surface Laptop", "Surface Laptop 2", "Surface Pro (5th Gen)")):
            pkgs_by_id["kernel"].append(catalog.ARCH_EXTRA_MARVELL)
        pkgs_by_id["kernel"].append(catalog.ARCH_EXTRA_INTEL)
    if "secureboot" in pkgs_by_id and "disabled" in (sysfs.run(["mokutil", "--sb-state"]).stdout or ""):
        plan.notes.append("Secure Boot is currently off, so the key is not required yet.")
    if not pkgs_by_id:
        plan.blockers.append("Nothing to install for this device.")
        return plan

    if d.immutable and d.family == "fedora":
        _ostree_steps(list(pkgs_by_id), pkgs_by_id, plan)
        return plan
    if d.immutable:
        plan.blockers.append("Image-based systems other than Fedora Atomic are not supported by this app.")
        return plan

    if d.family == "suse":
        if "kernel" in pkgs_by_id:
            plan.steps.append(Step("Add the openSUSE Tumbleweed Surface repository", "cmd", argv=[
                "zypper", "--non-interactive", "addrepo", "--refresh", catalog.SUSE_REPO]))
            plan.steps.append(Step("Install the kernel", "cmd", argv=[
                "zypper", "--non-interactive", "--gpg-auto-import-keys", "install", "-r", catalog.SUSE_REPO_ALIAS,
                "kernel-default"]))
        rest = [p for i, ps in pkgs_by_id.items() if i != "kernel" for p in ps]
        if rest:
            plan.steps.append(Step("Install packages", "cmd", argv=["zypper", "--non-interactive", "install", *rest]))
        plan.notes.append("Run your bootloader's update step (for example in YaST) and pick the new kernel.")
        return plan

    pkgs = list(dict.fromkeys(p for ps in pkgs_by_id.values() for p in ps))
    if need_repo and not repo_present(d):
        plan.steps += _repo_steps(d)
    if d.family == "debian":
        plan.steps.append(Step("Install packages", "cmd", argv=["apt-get", "install", "-y", *pkgs]))
    elif d.family == "fedora":
        a = ["dnf", "install", "-y"] + (["--allowerasing"] if allow_erase else [])
        plan.steps.append(Step("Install packages", "cmd", argv=a + pkgs))
    elif d.family == "arch":
        plan.steps.append(Step("Update the system and install packages", "cmd",
                               argv=["pacman", "-Syu", "--needed", "--noconfirm", *pkgs]))
        plan.notes.append("Arch installs always update the whole system together with the new packages.")
    _service_steps([catalog.by_id(cid) for cid in pkgs_by_id], plan)
    if "kernel" in pkgs_by_id and d.family in ("debian", "arch"):
        plan.notes.append("Check your bootloader afterwards; the Surface kernel must be the one that boots.")
    return plan


# ----------------------------------------------------------------------------- running

def _fingerprint_ok(keyfile: str) -> bool:
    r = sysfs.run(["gpg", "--show-keys", "--with-colons", keyfile])
    fprs = [ln.split(":")[9] for ln in r.stdout.splitlines() if ln.startswith("fpr:")]
    return any(f.endswith(catalog.KEY_FPR_SUFFIX) for f in fprs)


def _stream(argv: list[str], log: Log, env_extra: dict | None = None, cwd: str | None = None) -> int:
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive", LC_ALL="C", **(env_extra or {}))
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=cwd)
    except OSError as e:
        log(f"error: {e}")
        return 127
    for line in proc.stdout:
        log(line.rstrip())
    return proc.wait()


def run_step(step: Step, log: Log, dry_run: bool = False) -> bool:
    log(f"== {step.title}")
    log(f"$ {step.describe()}")
    if dry_run:
        return True
    try:
        if step.kind == "cmd":
            return _stream(step.argv, log) == 0
        if step.kind == "write":
            Path(step.path).parent.mkdir(parents=True, exist_ok=True)
            Path(step.path).write_text(step.content)
            os.chmod(step.path, step.mode)
            return True
        if step.kind == "download":
            with urllib.request.urlopen(step.url, timeout=30) as r:
                data = r.read()
            Path(step.path).write_bytes(data)
            log(f"downloaded {len(data) // 1024} KiB, sha256 {hashlib.sha256(data).hexdigest()}")
            return True
        if step.kind == "append":
            cur = Path(step.path).read_text()
            if "[linux-surface]" not in cur:
                Path(step.path).write_text(cur.rstrip("\n") + "\n" + step.content)
            return True
        if step.kind == "key":
            with tempfile.TemporaryDirectory() as td:
                asc = os.path.join(td, "key.asc")
                with urllib.request.urlopen(catalog.KEY_URL, timeout=30) as r:
                    Path(asc).write_bytes(r.read())
                if not _fingerprint_ok(asc):
                    log(f"error: downloaded key does not match fingerprint ...{catalog.KEY_FPR_SUFFIX}; refusing to use it")
                    return False
                log("key fingerprint verified")
                if step.path.endswith(".gpg"):
                    r = subprocess.run(["gpg", "--dearmor", "--yes", "-o", step.path, asc],
                                       capture_output=True, text=True)
                    os.chmod(step.path, 0o644)
                    return r.returncode == 0
                shutil.copy(asc, step.path)
                return True
    except Exception as e:  # noqa: BLE001 - report any failure to the user, step fails
        log(f"error: {e}")
        return False
    return False


def run_plan(plan: Plan, log: Log, dry_run: bool = False) -> bool:
    if plan.blockers:
        for b in plan.blockers:
            log(b)
        return False
    for s in plan.steps:
        if not run_step(s, log, dry_run):
            log("Stopped: a step failed. Nothing further was changed.")
            return False
    log("Dry run complete." if dry_run else "Done.")
    return True


# ----------------------------------------------------------------------------- status & updates

def watched_packages(d: dist.Distro) -> dict[str, str]:
    """installed package name -> component id."""
    out = {}
    for c in catalog.COMPONENTS:
        n = c.check_pkg.get(d.family)
        if n:
            out[n] = c.id
    return out


def component_status(dev: Device, d: dist.Distro) -> list[dict]:
    rows = []
    verdict = assess(dev, d)
    for c in catalog.COMPONENTS:
        ok, why = applicable(c, dev, d)
        if ok and verdict["level"] == "blocked":
            ok, why = False, "Not possible on this system"
        ver = dist.installed_version(d, c.check_pkg[d.family]) if ok and d.family in c.check_pkg else None
        row = {"id": c.id, "title": c.title, "why": c.why, "installable": ok, "reason": why,
               "installed": ver, "reboot": c.reboot, "services": None}
        if ver and (c.services or c.user_services):
            st = service_states(c)
            row["services"] = {"state": st, "running": services_running(st)}
        rows.append(row)
    return rows


def system_status(dev: Device, d: dist.Distro) -> dict:
    sb = (sysfs.run(["mokutil", "--sb-state"]).stdout or "").strip()
    return {
        "kernel": dev.kernel, "surface_kernel": dev.surface_kernel,
        "repo": repo_present(d),
        "repo_leftover": d.family == "debian" and not repo_present(d)
                         and any(sysfs.p(f).exists() for f in ("/etc/apt/sources.list.d/linux-surface.list",
                                                               "/etc/apt/sources.list.d/linux-surface.sources")),
        "secure_boot": ("enabled" if "enabled" in sb else "disabled" if "disabled" in sb else "unknown"),
        "tlp": sysfs.have("tlp") and sysfs.run(["systemctl", "is-active", "tlp"]).stdout.strip() == "active",
    }


_latest_cache: tuple[float, str | None] | None = None


def latest_release() -> str | None:
    """Newest linux-surface release version published on GitHub (informational; works without the repo)."""
    global _latest_cache
    import json as _json
    import time as _time
    import urllib.request as _rq
    if _latest_cache and _time.monotonic() - _latest_cache[0] < 3600:
        return _latest_cache[1]
    ver = None
    try:
        req = _rq.Request("https://api.github.com/repos/linux-surface/linux-surface/releases/latest",
                          headers={"User-Agent": "surface-companion", "Accept": "application/vnd.github+json"})
        with _rq.urlopen(req, timeout=5) as r:
            tag = _json.load(r).get("tag_name", "")
        m = re.match(r"^(?:arch|debian|fedora-\d+)-(.+)$", tag)
        ver = m.group(1) if m else (tag or None)
    except Exception:  # noqa: BLE001 - offline or rate limited: simply unknown
        ver = None
    _latest_cache = (_time.monotonic(), ver)
    return ver


def kernel_status(dev: Device, d: dist.Distro, with_latest: bool = True) -> dict:
    """Which linux-surface kernel is running, installed, and available to this system."""
    out = {"running": dev.kernel, "running_is_surface": dev.surface_kernel, "installed": None, "available": None,
           "latest_release": latest_release() if with_latest else None, "repo": repo_present(d),
           "installed_images": sorted(f.name.removeprefix("vmlinuz-") for f in sysfs.glob("/boot/vmlinuz-*surface*"))}
    if d.family == "debian":
        txt = sysfs.run(["apt-cache", "policy", "linux-image-surface"], env=dict(os.environ, LC_ALL="C")).stdout
        for key, name in (("Installed", "installed"), ("Candidate", "available")):
            m = re.search(rf"^\s*{key}:\s*(\S+)", txt, re.M)
            out[name] = m.group(1) if m and m.group(1) != "(none)" else None
    elif d.family == "fedora" and not d.immutable:
        out["installed"] = dist.installed_version(d, "kernel-surface")
        r = sysfs.run(["dnf", "repoquery", "-q", "--cacheonly", "--latest-limit=1", "--qf",
                       "%{version}-%{release}", "kernel-surface"], timeout=60)
        out["available"] = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else None
    elif d.family == "arch":
        out["installed"] = dist.installed_version(d, "linux-surface")
        r = sysfs.run(["pacman", "-Si", "linux-surface"])
        m = re.search(r"^Version\s*:\s*(\S+)", r.stdout, re.M)
        out["available"] = m.group(1) if m else None
    return out


def check_updates(d: dist.Distro, refresh: bool = True) -> dict:
    names = watched_packages(d)
    out: list[dict] = []
    warnings: list[str] = []
    if d.immutable:
        return {"updates": out, "warnings": ["Image-based system: run “rpm-ostree upgrade” to update."]}
    if d.family in ("debian", "fedora", "arch") and not repo_present(d):
        warnings.append("The linux-surface repository is not set up on this system, so newer Surface kernels and "
                        "drivers can't be seen. Set it up from the Software page first.")
    if d.family == "debian":
        if refresh:
            r0 = sysfs.run(["apt-get", "update"], timeout=240, env=dict(os.environ, LC_ALL="C"))
            bad = [ln for ln in (r0.stdout + r0.stderr).splitlines()
                   if ln.startswith(("Err", "E:", "W:")) and "surfacelinux" in ln]
            warnings += [f"Refreshing the Surface repository failed: {ln}" for ln in bad[:3]]
        r = sysfs.run(["apt", "list", "--upgradable"], timeout=60, env=dict(os.environ, LC_ALL="C"))
        for ln in r.stdout.splitlines():
            m = re.match(r"^([^/\s]+)/\S+\s+(\S+)\s+\S+\s+\[upgradable from: (\S+)\]", ln)
            if m and (m.group(1) in names or "surface" in m.group(1)):
                out.append({"name": m.group(1), "installed": m.group(3), "available": m.group(2)})
    elif d.family == "fedora":
        r = sysfs.run(["dnf", "check-update", "-q", "--refresh" if refresh else "--cacheonly"], timeout=240)
        if r.returncode == 100:
            for ln in r.stdout.splitlines():
                parts = ln.split()
                if len(parts) == 3:
                    n = parts[0].rsplit(".", 1)[0]
                    if n in names or "surface" in n:
                        out.append({"name": n, "installed": dist.installed_version(d, n) or "", "available": parts[1]})
    elif d.family == "suse" and not d.immutable:
        r = sysfs.run(["zypper", "--non-interactive", "list-updates"], timeout=180)
        for ln in r.stdout.splitlines():
            parts = [x.strip() for x in ln.split("|")]
            if len(parts) >= 5 and parts[0] == "v" and (parts[2] in names or "surface" in parts[2] or parts[2] == "kernel-default"):
                out.append({"name": parts[2], "installed": parts[3], "available": parts[4]})
    elif d.family == "arch":
        r = sysfs.run(["pacman", "-Qu"], timeout=60)
        for ln in r.stdout.splitlines():
            parts = ln.split()
            if len(parts) >= 4 and (parts[0] in names or "surface" in parts[0]):
                out.append({"name": parts[0], "installed": parts[1], "available": parts[3]})
    return {"updates": out, "warnings": warnings}


def plan_update(d: dist.Distro, pkgs: list[str]) -> Plan:
    plan = Plan()
    if not pkgs:
        plan.blockers.append("Nothing to update.")
        return plan
    if d.immutable:
        plan.steps.append(Step("Update the OS image", "cmd", argv=["rpm-ostree", "upgrade"]))
        plan.reboot = True
        return plan
    if d.family == "debian":
        plan.steps.append(Step("Update Surface packages", "cmd", argv=["apt-get", "install", "-y", "--only-upgrade", *pkgs]))
    elif d.family == "fedora":
        plan.steps.append(Step("Update Surface packages", "cmd", argv=["dnf", "upgrade", "-y", *pkgs]))
    elif d.family == "suse":
        plan.steps.append(Step("Update Surface packages", "cmd", argv=["zypper", "--non-interactive", "update", *pkgs]))
    elif d.family == "arch":
        plan.steps.append(Step("Update the system", "cmd", argv=["pacman", "-Syu", "--noconfirm"]))
        plan.notes.append("Arch does not support updating single packages; this updates the whole system.")
    else:
        plan.blockers.append("Updates are not automated on this distribution.")
    plan.reboot = any("surface" in p for p in pkgs)
    return plan
