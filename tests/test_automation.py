import os
import struct
import time

import pytest

from conftest import put
from surface_companion import actions, automation
from surface_companion.hw import idle


@pytest.fixture
def env(root):
    put(root, "/sys/firmware/acpi/platform_profile", "balanced")
    put(root, "/sys/firmware/acpi/platform_profile_choices", "low-power balanced performance")
    put(root, "/sys/class/leds/surface::kbd_backlight/max_brightness", "3")
    put(root, "/sys/class/leds/surface::kbd_backlight/brightness", "2")
    st = actions.Settings(path=root / "settings.json")
    acts = actions.build(st)
    return st, acts, root


def prof(root):
    return (root / "sys/firmware/acpi/platform_profile").read_text()


# ------------------------------------------------------------------ automatic profile

def test_automatic_profile_follows_the_power_source(env):
    st, a, root = env
    power = {"ac": False}
    st.auto.ac_fn = lambda: power["ac"]
    a["set_auto_profile"].fn({"battery": "low-power", "ac": "performance"})
    assert prof(root) == "low-power" and st.overrides()["auto_profile"]
    power["ac"] = True
    st.tick_all()
    assert prof(root) == "performance"
    power["ac"] = False
    st.tick_all()
    assert prof(root) == "low-power"


def test_automatic_profile_system_leaves_that_source_alone(env):
    st, a, root = env
    power = {"ac": False}
    st.auto.ac_fn = lambda: power["ac"]
    a["set_auto_profile"].fn({"battery": "low-power", "ac": "system"})
    power["ac"] = True
    st.tick_all()
    assert prof(root) == "low-power"          # plugged in = system's call, so we did not touch it


def test_choosing_a_profile_or_system_ends_automatic_mode(env):
    st, a, root = env
    st.auto.ac_fn = lambda: False
    a["set_auto_profile"].fn({"battery": "low-power", "ac": "balanced"})
    assert st.overrides()["auto_profile"] and not st.overrides()["profile"]
    a["set_profile"].fn({"profile": "performance"})
    assert not st.overrides()["auto_profile"] and st.overrides()["profile"]
    a["set_auto_profile"].fn({"battery": "low-power", "ac": "balanced"})
    assert not st.overrides()["profile"]
    a["set_profile"].fn({"profile": "system"})
    assert not st.overrides()["auto_profile"] and not st.overrides()["profile"]


def test_automatic_profile_rejects_unknown_profiles_and_survives_unknown_power_state(env):
    st, a, root = env
    with pytest.raises(ValueError):
        a["set_auto_profile"].fn({"battery": "turbo", "ac": "balanced"})
    assert not st.overrides()["auto_profile"]
    st.auto.ac_fn = lambda: None
    a["set_auto_profile"].fn({"battery": "low-power", "ac": "performance"})
    assert prof(root) == "balanced"           # can't tell the source: change nothing


def test_on_ac_reads_power_supplies(root):
    put(root, "/sys/class/power_supply/BAT1/type", "Battery")
    assert automation.on_ac() is None
    put(root, "/sys/class/power_supply/ADP1/type", "Mains")
    put(root, "/sys/class/power_supply/ADP1/online", "0")
    assert automation.on_ac() is False
    put(root, "/sys/class/power_supply/ADP1/online", "1")
    assert automation.on_ac() is True


# ------------------------------------------------------------------ keyboard backlight timeout

class FakeActivity:
    def __init__(self):
        self.idle = 0.0
        self.started = self.stopped = False

    def idle_seconds(self):
        return self.idle

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def kb(root):
    return int((root / "sys/class/leds/surface::kbd_backlight/brightness").read_text())


def test_keyboard_backlight_dims_when_idle_and_returns_on_activity(env):
    st, a, root = env
    st.kbd.activity = fake = FakeActivity()
    a["set_kbd_timeout"].fn({"seconds": 10})
    assert fake.started and st.overrides()["kbd_timeout"] == 10
    fake.idle = 5
    st.tick_all()
    assert kb(root) == 2
    fake.idle = 11
    st.tick_all()
    assert kb(root) == 0
    fake.idle = 0.2                           # you touched a key
    st.tick_all()
    assert kb(root) == 2


def test_timeout_does_nothing_when_backlight_is_already_off_and_never_turns_it_on(env):
    st, a, root = env
    put(root, "/sys/class/leds/surface::kbd_backlight/brightness", "0")
    st.kbd.activity = fake = FakeActivity()
    a["set_kbd_timeout"].fn({"seconds": 5})
    fake.idle = 60
    st.tick_all()
    fake.idle = 0
    st.tick_all()
    assert kb(root) == 0


def test_timeout_stands_down_if_you_change_the_level_yourself(env):
    st, a, root = env
    st.kbd.activity = fake = FakeActivity()
    a["set_kbd_timeout"].fn({"seconds": 5})
    fake.idle = 10
    st.tick_all()
    assert kb(root) == 0
    put(root, "/sys/class/leds/surface::kbd_backlight/brightness", "3")     # user turned it up while idle
    st.tick_all()
    fake.idle = 0
    st.tick_all()
    assert kb(root) == 3                      # not overwritten with the old level


def test_system_turns_the_timeout_off_and_restores_the_light(env):
    st, a, root = env
    st.kbd.activity = fake = FakeActivity()
    a["set_kbd_timeout"].fn({"seconds": 5})
    fake.idle = 10
    st.tick_all()
    assert kb(root) == 0
    a["set_kbd_timeout"].fn({"seconds": "system"})
    assert kb(root) == 2 and fake.stopped and st.overrides()["kbd_timeout"] is None


def test_timeout_needs_a_backlight_and_a_sane_value(env):
    st, a, root = env
    st.kbd.activity = FakeActivity()
    with pytest.raises(ValueError):
        a["set_kbd_timeout"].fn({"seconds": 99999})
    (root / "sys/class/leds/surface::kbd_backlight").rename(root / "sys/class/leds/other")
    with pytest.raises(RuntimeError, match="no keyboard backlight"):
        a["set_kbd_timeout"].fn({"seconds": 10})


# ------------------------------------------------------------------ input activity

def test_device_selection_keeps_keyboards_and_pointers_and_skips_buttons(root):
    def dev(n, name, key="0", rel="0", ab="0"):
        put(root, f"/sys/class/input/event{n}/device/name", name)
        put(root, f"/sys/class/input/event{n}/device/capabilities/key", key)
        put(root, f"/sys/class/input/event{n}/device/capabilities/rel", rel)
        put(root, f"/sys/class/input/event{n}/device/capabilities/abs", ab)
    big = "ffffffff ffffffff"
    dev(0, "Microsoft Surface Keyboard", key=big)
    dev(1, "Surface Touchpad", key="400", ab="3000003")
    dev(2, "Power Button", key="10")
    dev(3, "Lid Switch")
    dev(4, "Intel HID events", key=big)
    dev(5, "Some Mouse", key="1f0000", rel="143")
    assert idle.input_devices() == ["/dev/input/event0", "/dev/input/event1", "/dev/input/event5"]


def test_activity_watcher_records_only_the_time_of_input(root):
    put(root, "/sys/class/input/event0/device/name", "Keyboard")
    put(root, "/sys/class/input/event0/device/capabilities/key", "ffffffff ffffffff")
    node = root / "dev/input/event0"
    node.parent.mkdir(parents=True)
    os.mkfifo(node)
    writer_fd = None
    now = {"t": 100.0}
    act = idle.InputActivity(clock=lambda: now["t"], rescan_seconds=0.05)
    reader = os.open(node, os.O_RDONLY | os.O_NONBLOCK)            # lets us open the write end without blocking
    os.close(reader)
    act.start()
    try:
        time.sleep(0.3)                                             # watcher opens the fifo
        writer_fd = os.open(node, os.O_WRONLY | os.O_NONBLOCK)
        now["t"] = 130.0
        assert act.idle_seconds() == 30.0
        os.write(writer_fd, struct.pack("llHHi", 0, 0, 1, 30, 1))   # one key event, content irrelevant
        for _ in range(40):
            if act.idle_seconds() < 1:
                break
            time.sleep(0.05)
        assert act.idle_seconds() < 1, "input should reset the idle timer"
    finally:
        act.stop()
        if writer_fd is not None:
            os.close(writer_fd)


# ------------------------------------------------------------------ sleep: hand everything back to the system

def test_sleep_system_clears_mem_sleep_lid_action_and_restores_lid_wake(env, monkeypatch):
    st, a, root = env
    from surface_companion.hw import sleep
    monkeypatch.setattr(sleep.sysfs, "run", lambda *x, **k: None)
    put(root, "/sys/power/mem_sleep", "[s2idle] deep")
    put(root, "/sys/bus/acpi/devices/PNP0C0D:00/power/wakeup", "enabled")
    a["set_mem_sleep"].fn({"mem_sleep": "deep"})
    a["set_lid_action"].fn({"action": "ignore"})
    a["set_lid_wake"].fn({"enabled": False})
    a["set_lid_wake"].fn({"enabled": True})
    a["set_lid_wake"].fn({"enabled": False})
    assert st.data["orig_lid_wake"] is True                         # remembered once, from before our first change
    assert (root / "sys/bus/acpi/devices/PNP0C0D:00/power/wakeup").read_text() == "disabled"
    a["sleep_system"].fn({})
    ov = st.overrides()
    assert not ov["mem_sleep"] and not ov["lid_wake"]
    assert sleep.state()["lid_action"] == "default"
    assert (root / "sys/bus/acpi/devices/PNP0C0D:00/power/wakeup").read_text() == "enabled"
