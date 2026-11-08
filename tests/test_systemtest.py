import struct
import subprocess
import wave

import pytest

from conftest import put
from surface_companion import device, handson, stress, sysfs, systemtest as st
from surface_companion.pkg import distro as dist


def ctx(model="Surface Laptop 3", surface_kernel=True, family="laptop", cpu="intel"):
    return st.Ctx(device.Device(vendor="Microsoft Corporation", model=model, family=family, is_surface=True,
                                surface_kernel=surface_kernel, kernel="7.2.15-1-surface", cpu_vendor=cpu),
                  dist.Distro(id="ubuntu", family="debian"))


def fake_log(monkeypatch, text):
    monkeypatch.setattr(st, "_kernel_log", lambda: text)


# ------------------------------------------------------------------ health checks

def test_titles_match_the_checks_exactly(root, monkeypatch):
    assert list(st.TITLES) == st.check_ids()
    fake_log(monkeypatch, "")
    monkeypatch.setattr(sysfs, "run", lambda argv, timeout=10, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    for cid in st.check_ids():
        r = st.run_check(cid, ctx())
        assert r["id"] == cid and r["title"] == st.TITLES[cid], cid
        assert r["status"] in ("ok", "warn", "fail", "skip")


def test_kernel_check(root):
    assert st.run_check("kernel", ctx())["status"] == "ok"
    r = st.run_check("kernel", ctx(surface_kernel=False))
    assert r["status"] == "warn" and r["goto"] == "software"
    other = st.Ctx(device.Device(vendor="Dell", model="XPS"), dist.Distro())
    assert st.run_check("kernel", other)["status"] == "skip"


def test_keyboard_is_found_by_its_handlers_not_its_name(root):
    put(root, "/proc/bus/input/devices",
        'N: Name="Microsoft Surface 045E:09AE"\nH: Handlers=sysrq kbd event3 leds\n\n'
        'N: Name="Microsoft Surface 045E:09AF Touchpad"\nH: Handlers=mouse1 event4\n\n')
    assert st.run_check("input", ctx())["status"] == "ok"
    put(root, "/proc/bus/input/devices", 'N: Name="Microsoft Surface 045E:09AF Touchpad"\nH: Handlers=mouse1 event4\n\n')
    r = st.run_check("input", ctx())
    assert r["status"] == "warn" and "No keyboard" in r["detail"]


def test_touchpad_is_not_mistaken_for_a_touchscreen(root, monkeypatch):
    monkeypatch.setattr(sysfs, "run", lambda argv, timeout=10, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    put(root, "/proc/bus/input/devices", 'N: Name="Microsoft Surface 045E:09AF Touchpad"\nH: Handlers=mouse1 event4\n\n')
    assert st.run_check("touch", ctx())["status"] == "skip"
    put(root, "/proc/bus/input/devices", 'N: Name="IPTS 045E:0984 Touchscreen"\nH: Handlers=event9\n\n')
    r = st.run_check("touch", ctx())
    assert r["status"] == "warn" and "touch service" in r["detail"]


def test_battery_thresholds(root):
    b = "/sys/class/power_supply/BAT1"
    put(root, b + "/type", "Battery")
    put(root, b + "/capacity", "80")
    put(root, b + "/status", "Discharging")
    put(root, b + "/energy_full_design", "100")
    for full, want in (("95", "ok"), ("70", "warn"), ("50", "fail")):
        put(root, b + "/energy_full", full)
        assert st.run_check("battery", ctx())["status"] == want


def test_thermal_thresholds_and_throttling(root):
    put(root, "/sys/class/hwmon/hwmon0/name", "coretemp")
    put(root, "/sys/class/hwmon/hwmon0/temp1_label", "Package id 0")
    for temp, want in (("50000", "ok"), ("88000", "warn"), ("97000", "fail")):
        put(root, "/sys/class/hwmon/hwmon0/temp1_input", temp)
        assert st.run_check("thermal", ctx())["status"] == want
    put(root, "/sys/class/hwmon/hwmon0/temp1_input", "50000")
    put(root, "/sys/class/thermal/cooling_device1/type", "Processor")
    put(root, "/sys/class/thermal/cooling_device1/cur_state", "4")
    put(root, "/sys/class/thermal/cooling_device1/max_state", "10")
    assert st.run_check("thermal", ctx())["status"] == "warn"


def test_log_based_checks(root, monkeypatch):
    fake_log(monkeypatch, "ok line\nintel_pmc: fine\n")
    assert st.run_check("firmware", ctx())["status"] == "ok"
    assert st.run_check("kerrors", ctx())["status"] == "ok"
    fake_log(monkeypatch, "iwlwifi 0000:00:14.3: Direct firmware load for iwlwifi-so-a0.ucode failed with error -2\n")
    r = st.run_check("firmware", ctx())
    assert r["status"] == "warn" and "iwlwifi-so-a0.ucode" in r["detail"] and r["goto"] == "fixes"
    errs = "\n".join("ipts 0000:00:16.4: Failed to process buffer: -16" for _ in range(3))
    fake_log(monkeypatch, errs)
    r = st.run_check("kerrors", ctx())
    assert r["status"] == "warn" and "first from ipts" in r["detail"]
    fake_log(monkeypatch, errs.replace("buffer", "buffer\nipts failed to x") + "\n" + "\n".join(["surface error timeout"] * 30))
    assert st.run_check("kerrors", ctx())["status"] == "fail"
    fake_log(monkeypatch, None)
    assert st.run_check("firmware", ctx())["status"] == "skip"


def test_a_crashing_check_is_reported_as_skipped_not_raised(root, monkeypatch):
    monkeypatch.setattr(st, "c_display", lambda x: 1 / 0)
    monkeypatch.setattr(st, "CHECKS", [("display", st.c_display)])
    r = st.run_check("display", ctx())
    assert r["status"] == "skip" and "failed" in r["detail"]
    with pytest.raises(KeyError):
        st.run_check("nope", ctx())


def test_summary_and_report_text():
    res = [{"id": "a", "title": "A", "status": "ok", "detail": "fine", "hint": "", "goto": None},
           {"id": "b", "title": "B", "status": "warn", "detail": "hmm", "hint": "do x", "goto": None},
           {"id": "c", "title": "C", "status": "skip", "detail": "", "hint": "", "goto": None}]
    s = st.summarize(res)
    assert s["verdict"] == "warn" and s["counts"] == {"ok": 1, "warn": 1, "fail": 0, "skip": 1}
    assert st.summarize(res + [{"status": "fail"}])["verdict"] == "fail"
    t = st.report_text(res, {"model": "Surface Go 3", "kernel": "k", "distro": "Ubuntu"}, {"summary": "Held 3 GHz"})
    assert "Result: WARN" in t and "[PASS] A: fine" in t and "-> do x" in t and "Surface Go 3" in t and "Stress test: Held 3 GHz" in t
    assert "[SKIP] C" in t


def test_disk_check_warns_on_a_full_boot(root, monkeypatch):
    import shutil
    from collections import namedtuple
    U = namedtuple("U", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: U(10**10, 9 * 10**9, 100 * 1024 * 1024 if str(p).endswith("boot") else 10**10))
    (root / "boot").mkdir()
    r = st.run_check("disk", ctx())
    assert r["status"] == "warn" and "/boot" in r["detail"]


# ------------------------------------------------------------------ stress test

def burner_set(procs=2):
    class P:
        terminated = False
        def terminate(self): P.terminated = True
        def wait(self, timeout=None): return 0
        def kill(self): pass
    return [P() for _ in range(procs)], P


def run_stress(samples, seconds, abort_c=95.0, stop=lambda: False):
    procs, P = burner_set()
    it = iter([{"temp": 40.0, "clock": 3400, "throttle": 0, "fan": None}] + samples)
    logs = []
    r = stress.run(seconds, logs.append, spawn=lambda: procs, take=lambda: next(it), sleep=lambda s: None,
                   abort_c=abort_c, stop=stop)
    return r, P, logs


def s(temp, clock, throttle=0, fan=None):
    return {"temp": temp, "clock": clock, "throttle": throttle, "fan": fan}


def test_stress_ok_when_clocks_hold(monkeypatch):
    monkeypatch.setattr(stress, "precheck", lambda: None)
    r, P, _ = run_stress([s(70, 3000), s(75, 2950), s(76, 3000), s(77, 2980), s(78, 3000)], 5)
    assert r["status"] == "ok" and r["peak_temp"] == 78 and not r["throttled"] and P.terminated


def test_stress_warns_on_throttling_or_big_clock_drop(monkeypatch):
    monkeypatch.setattr(stress, "precheck", lambda: None)
    r, _, _ = run_stress([s(70, 3000), s(80, 2800, 1), s(85, 1800), s(86, 1500), s(86, 1400)], 5)
    assert r["status"] == "warn" and r["throttled"]
    r, _, _ = run_stress([s(70, 3000), s(80, 2900), s(85, 1900), s(86, 1700), s(86, 1700)], 5)
    assert r["status"] == "warn" and "slowed down" in r["summary"]


def test_stress_aborts_when_too_hot_and_always_kills_the_load(monkeypatch):
    monkeypatch.setattr(stress, "precheck", lambda: None)
    r, P, logs = run_stress([s(80, 3000), s(96, 2500), s(97, 2000), s(98, 1500), s(99, 1200)], 5)
    assert r["status"] == "fail" and "Stopped early" in r["summary"] and r["seconds"] == 2 and P.terminated


def test_stress_can_be_stopped_by_the_user_and_cleans_up_on_errors(monkeypatch):
    monkeypatch.setattr(stress, "precheck", lambda: None)
    n = {"i": 0}
    def stop():
        n["i"] += 1
        return n["i"] > 2
    r, P, _ = run_stress([s(60, 3000)] * 5, 5, stop=stop)
    assert r["status"] == "skip" and P.terminated
    procs, P = burner_set()
    def boom():
        raise RuntimeError("sensor died")
    calls = iter([s(40, 3400, 0), None])
    def take():
        v = next(calls)
        if v is None:
            boom()
        return v
    with pytest.raises(RuntimeError):
        stress.run(5, lambda l: None, spawn=lambda: procs, take=take, sleep=lambda x: None)
    assert P.terminated


def test_stress_refuses_bad_duration_and_low_battery(root):
    with pytest.raises(ValueError):
        stress.run(1, lambda l: None)
    with pytest.raises(ValueError):
        stress.run(9999, lambda l: None)
    b = "/sys/class/power_supply/BAT1"
    put(root, b + "/type", "Battery")
    put(root, b + "/capacity", "10")
    put(root, b + "/status", "Discharging")
    with pytest.raises(RuntimeError, match="Plug in"):
        stress.run(10, lambda l: None)


def test_stress_action_is_a_job_that_needs_control_permission(root, monkeypatch):
    from surface_companion import actions
    a = actions.build(actions.Settings(path=root / "s.json"))["stress_test"]
    assert a.job and a.polkit == actions.POLKIT_CONTROL


# ------------------------------------------------------------------ hands-on helpers

def test_usb_snapshot_and_diff(root):
    put(root, "/sys/bus/usb/devices/3-1/idVendor", "0781")
    put(root, "/sys/bus/usb/devices/3-1/idProduct", "5581")
    put(root, "/sys/bus/usb/devices/3-1/product", "Ultra")
    put(root, "/sys/bus/usb/devices/3-1/manufacturer", "SanDisk")
    put(root, "/sys/bus/usb/devices/3-1/removable", "removable")
    put(root, "/sys/bus/usb/devices/3-1/speed", "480")
    put(root, "/sys/bus/usb/devices/usb3/product", "xHCI")                  # root hub: ignored
    put(root, "/sys/bus/usb/devices/3-1:1.0/bInterfaceClass", "08")        # interface: ignored
    snap = handson.ports_snapshot()
    assert list(snap["usb"]) == ["3-1"] and snap["usb"]["3-1"]["name"] == "SanDisk Ultra"
    empty = {"usb": {}, "typec": {}, "displays": {}, "jacks": {}}
    assert handson.diff_snapshots(empty, snap) == ["USB device plugged in: SanDisk Ultra (480 Mb/s)"]
    assert handson.diff_snapshots(snap, empty) == ["USB device removed: SanDisk Ultra"]
    assert any("SanDisk Ultra" in ln for ln in handson.describe_ports(snap))


def test_typec_displays_and_jacks_diff(root, monkeypatch):
    put(root, "/sys/class/drm/card1-HDMI-A-1/status", "disconnected")
    put(root, "/sys/class/drm/card1-eDP-1/status", "connected")
    put(root, "/sys/class/typec/port0/data_role", "[host] device")
    old = handson.ports_snapshot()
    assert "eDP-1" not in old["displays"] and old["typec"]["port0"]["partner"] is False
    put(root, "/sys/class/drm/card1-HDMI-A-1/status", "connected")
    (root / "sys/class/typec/port0-partner").mkdir()
    put(root, "/sys/class/typec/port0/power_operation_mode", "usb_power_delivery")
    new = handson.ports_snapshot()
    ev = handson.diff_snapshots(old, new)
    assert "Display output HDMI-A-1: a screen connected" in ev
    assert any(e.startswith("USB-C port0: something connected") and "usb_power_delivery" in e for e in ev)
    new2 = {**new, "jacks": {"Headphone Jack": True}}
    assert handson.diff_snapshots(new, new2) == ["Headphone Jack: plugged in"]


def test_audio_jack_parsing_ignores_phantom_jacks(root, monkeypatch):
    txt = ("numid=18,iface=CARD,name='HDMI/DP,pcm=3 Jack'\n  ; type=BOOLEAN,access=r-------,values=1\n  : values=off\n"
           "numid=20,iface=CARD,name='Headphone Jack'\n  ; type=BOOLEAN,access=r-------,values=1\n  : values=on\n"
           "numid=21,iface=CARD,name='Speaker Phantom Jack'\n  ; type=BOOLEAN,access=r-------,values=1\n  : values=on\n")
    monkeypatch.setattr(sysfs, "have", lambda c: c == "amixer")
    monkeypatch.setattr(sysfs, "run", lambda argv, timeout=10, **kw: subprocess.CompletedProcess(argv, 0, txt, ""))
    assert handson.jacks() == {"HDMI/DP,pcm=3 Jack": False, "Headphone Jack": True}


def test_keyboard_attach_detach_events():
    both = {"keyboards": 1, "touchpads": 1}
    none = {"keyboards": 0, "touchpads": 0}
    assert handson.keyboard_events(both, none) == ["keyboard detached", "touchpad gone"]
    assert handson.keyboard_events(none, both) == ["keyboard attached", "touchpad back"]
    assert handson.keyboard_events(both, both) == []
    assert handson.detach_capable("book") and handson.detach_capable("pro") and not handson.detach_capable("laptop")


def test_tone_files_are_stereo_and_pan_correctly(tmp_path):
    for channel in ("left", "right", "both"):
        path = handson.tone_wav(str(tmp_path / f"{channel}.wav"), channel, seconds=0.2)
        with wave.open(path) as w:
            assert w.getnchannels() == 2 and w.getframerate() == 44100
            data = w.readframes(w.getnframes())
        samples = struct.unpack("<" + "h" * (len(data) // 2), data)
        left, right = samples[0::2], samples[1::2]
        assert (max(map(abs, left)) > 1000) == (channel in ("left", "both"))
        assert (max(map(abs, right)) > 1000) == (channel in ("right", "both"))
        assert max(map(abs, samples)) < 32767 * 0.3            # quiet enough not to hurt


def test_player_and_capture_tool_selection(root, monkeypatch):
    monkeypatch.setattr(handson.shutil, "which", lambda t: "/usr/bin/" + t if t == "paplay" else None)
    assert handson.player_cmd("/x.wav") == ["paplay", "/x.wav"]
    assert handson.capture_cmd("/dev/video0", "/o.jpg") is None
    monkeypatch.setattr(handson.shutil, "which", lambda t: "/usr/bin/" + t if t == "ffmpeg" else None)
    cmd = handson.capture_cmd("/dev/video2", "/o.jpg")
    assert cmd[0] == "ffmpeg" and "/dev/video2" in cmd and cmd[-1] == "/o.jpg" and "-update" in cmd
    monkeypatch.setattr(handson.shutil, "which", lambda t: None)
    assert handson.player_cmd("x") is None


def test_capture_nodes_only_include_picture_nodes(root):
    for n, idx, name in ((0, 0, "Front: color"), (1, 1, "Front: color"), (2, 0, "Front: IR"), (3, 1, "Front: IR")):
        put(root, f"/sys/class/video4linux/video{n}/index", str(idx))
        put(root, f"/sys/class/video4linux/video{n}/name", name)
    assert handson.capture_nodes() == [("/dev/video0", "Front: color"), ("/dev/video2", "Front: IR")]
