import os
import struct
import subprocess
import tempfile

import pytest

from conftest import put
from surface_companion import sysfs
from surface_companion.hw import clocks
from surface_companion.pkg import distro as dist, planner
from surface_companion import device


# ------------------------------------------------------------------ clocks

def test_cpu_clock_from_cpufreq(root):
    for i, khz in enumerate((3000000, 1000000)):
        put(root, f"/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq", str(khz))
    put(root, "/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "3900000")
    put(root, "/sys/devices/system/cpu/cpu0/cpufreq/scaling_max_freq", "3500000")
    put(root, "/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "powersave")
    c = clocks.cpu()
    assert (c["avg_mhz"], c["max_mhz"], c["min_mhz"]) == (2000, 3000, 1000)
    assert c["limit_mhz"] == 3900 and c["cap_mhz"] == 3500 and c["governor"] == "powersave"


def test_cpu_clock_falls_back_to_proc_cpuinfo(root):
    put(root, "/proc/cpuinfo", "cpu MHz\t\t: 2400.000\ncpu MHz\t\t: 1600.500\n")
    assert clocks.cpu()["avg_mhz"] == 2000
    assert clocks.cpu()["limit_mhz"] is None


def test_gpu_clock_intel_and_amd(root):
    put(root, "/sys/class/drm/card1/gt_cur_freq_mhz", "350")
    put(root, "/sys/class/drm/card1/gt_RP0_freq_mhz", "1100")
    assert clocks.gpu() == {"name": "Integrated graphics", "mhz": 350, "max_mhz": 1100}
    (root / "sys/class/drm/card1/gt_cur_freq_mhz").unlink()
    put(root, "/sys/class/drm/card1/device/pp_dpm_sclk", "0: 400Mhz\n1: 800Mhz *\n2: 2100Mhz\n")
    g = clocks.gpu()
    assert g["mhz"] == 800 and g["max_mhz"] == 2100


def test_no_gpu_clock_is_none_not_an_error(root):
    assert clocks.gpu() is None


def _smbios17(speed=4267, configured=4267, mtype=0x1E, size=8192):
    b = bytearray(0x28)
    b[0], b[1] = 17, 0x28
    b[0x0C:0x0E] = struct.pack("<H", size)
    b[0x12] = mtype
    b[0x15:0x17] = struct.pack("<H", speed)
    b[0x20:0x22] = struct.pack("<H", configured)
    return bytes(b)


def test_smbios_memory_record_parsing():
    r = clocks.parse_smbios_type17(_smbios17())
    assert r == {"type": "LPDDR4", "rated_mts": 4267, "configured_mts": 4267}
    assert clocks.parse_smbios_type17(_smbios17(size=0)) is None                # empty slot
    assert clocks.parse_smbios_type17(b"\x01" + b"\0" * 40) is None             # not a memory device
    assert clocks.parse_smbios_type17(_smbios17(mtype=0x22, configured=5600))["type"] == "DDR5"


def test_memory_speed_reads_dmi_entries_and_tolerates_no_permission(root):
    clocks._dmi_cache = None
    f = root / "sys/firmware/dmi/entries/17-0/raw"
    f.parent.mkdir(parents=True)
    f.write_bytes(_smbios17(configured=3200, speed=3200, mtype=0x1A))
    assert clocks.memory_speed()["configured_mts"] == 3200
    clocks._dmi_cache = None
    f.chmod(0)
    if os.geteuid() != 0:
        assert clocks.memory_speed() is None
    f.chmod(0o644)
    clocks._dmi_cache = None


def test_memory_usage(root):
    put(root, "/proc/meminfo", "MemTotal: 16384000 kB\nMemAvailable: 8192000 kB\nSwapTotal: 2048000 kB\nSwapFree: 1024000 kB\n")
    m = clocks.memory_usage()
    assert m["total_mb"] == 16000 and m["used_mb"] == 8000 and m["swap_used_mb"] == 1000


# ------------------------------------------------------------------ kernel status

def test_kernel_status_parses_apt_policy(root, monkeypatch):
    monkeypatch.setattr(planner, "latest_release", lambda: "7.2.15-1")
    put(root, "/etc/apt/sources.list.d/s.list", "deb https://pkg.surfacelinux.com/debian release main\n")
    put(root, "/boot/vmlinuz-6.19.8-surface-3", "")
    out = "linux-image-surface:\n  Installed: 6.19.8-surface-3\n  Candidate: 7.2.15-1\n"
    monkeypatch.setattr(sysfs, "run", lambda argv, timeout=10, **kw: subprocess.CompletedProcess(argv, 0, out, ""))
    d = dist.Distro(id="ubuntu", family="debian")
    k = planner.kernel_status(device.Device(kernel="7.0.0-43-generic"), d)
    assert (k["installed"], k["available"], k["latest_release"], k["repo"]) == ("6.19.8-surface-3", "7.2.15-1", "7.2.15-1", True)
    assert k["installed_images"] == ["6.19.8-surface-3"]
    out2 = "linux-image-surface:\n  Installed: (none)\n  Candidate: (none)\n"
    monkeypatch.setattr(sysfs, "run", lambda argv, timeout=10, **kw: subprocess.CompletedProcess(argv, 0, out2, ""))
    k = planner.kernel_status(device.Device(), d)
    assert k["installed"] is None and k["available"] is None


# ------------------------------------------------------------------ wizard logic

from surface_companion.ui import welcome  # noqa: E402


def _data(**over):
    d = {
        "state": {"device": {"model": "Surface Laptop 3", "family": "laptop", "kernel": "7.0.0-43-generic",
                             "cpu_vendor": "intel", "is_surface": True, "has_dtx": False},
                  "display": {"screen": {"percent": 50}, "keyboard": None},
                  "performance": {"available": True}, "battery": {"charge_limit": None}, "thermal": {"fans": [], "fan_levels": []},
                  "clocks": {"memory": {"total_mb": 15588}}},
        "software": {"distro": {"name": "Ubuntu", "family": "debian", "immutable": False},
                     "system": {"repo": True, "secure_boot": "enabled"},
                     "assessment": {"level": "supported", "reasons": []},
                     "components": [{"id": "kernel", "installable": True, "installed": None},
                                    {"id": "iptsd", "installable": True, "installed": None},
                                    {"id": "libwacom", "installable": False, "installed": None},
                                    {"id": "secureboot", "installable": True, "installed": None}]},
        "kernel": {"running": "7.0.0-43-generic", "running_is_surface": False, "installed": None, "available": "7.2.15-1",
                   "latest_release": "7.2.15-1", "repo": True},
        "slow": {"pen": {"touch_devices": ["x"], "pen_devices": []}, "camera": {"video_nodes": [1]}},
        "fixes": [{"id": "reboot-pci", "title": "Reboot hang", "summary": "s", "level": "recommended", "status": "available",
                   "scope": "system", "applicable": True},
                  {"id": "audio", "title": "Hiss", "summary": "s", "level": "optional", "status": "available",
                   "scope": "system", "applicable": True}],
        "updates": {"updates": [], "warnings": []},
    }
    d.update(over)
    return d


def test_summary_verdicts():
    s = welcome.summarize(_data())
    assert "Version 7.2.15-1 is available" in s["verdict"]
    assert [f["id"] for f in s["recommended_fixes"]] == ["reboot-pci"] and len(s["optional_fixes"]) == 1
    assert ("Touchscreen", True) in s["features"] and ("Surface Pen", False) in s["features"]
    k = dict(_data()["kernel"], installed="6.19.8-surface-3")
    assert "installed, but you're running" in welcome.summarize(_data(kernel=k))["verdict"]
    k = dict(k, running_is_surface=True, available="7.2.15-1")
    assert "Update available: 6.19.8-surface-3 → 7.2.15-1" in welcome.summarize(_data(kernel=k))["verdict"]
    k = dict(k, installed="7.2.15-1")
    assert "newest kernel" in welcome.summarize(_data(kernel=k))["verdict"]
    k = {"running": "x", "repo": False, "latest_release": "7.2.15-1", "installed": None, "available": None}
    assert "can't see the repository yet" in welcome.summarize(_data(kernel=k))["verdict"]
    assert "Couldn't look up" in welcome.summarize(_data(kernel={}))["verdict"]


def test_setup_items_offer_repo_kernel_updates_and_recommended_fixes():
    s = welcome.summarize(_data())
    ids = [i["id"] for i in welcome.setup_items(s)]
    assert ids == ["install", "fix-reboot-pci"]
    inst = welcome.setup_items(s)[0]
    assert inst["args"] == {"ids": ["kernel", "iptsd", "secureboot"], "accept_unsupported": False}
    d = _data()
    d["software"]["system"]["repo"] = False
    assert welcome.setup_items(welcome.summarize(d))[0]["id"] == "repo"
    d = _data(updates={"updates": [{"name": "linux-surface"}], "warnings": []})
    assert "update" in [i["id"] for i in welcome.setup_items(welcome.summarize(d))]


def test_wizard_never_auto_installs_on_unsupported_systems():
    d = _data()
    d["software"]["assessment"] = {"level": "unsupported", "reasons": ["x"]}
    ids = [i["id"] for i in welcome.setup_items(welcome.summarize(d))]
    assert "install" not in ids and "repo" not in ids and "update" not in ids


def test_user_scope_fixes_are_not_run_by_the_wizard():
    d = _data()
    d["fixes"][0]["scope"] = "user"
    assert [i["id"] for i in welcome.setup_items(welcome.summarize(d))] == ["install"]
