import pytest

from conftest import put
from surface_companion import actions
from surface_companion.hw import sleep


@pytest.fixture
def acts(root):
    put(root, "/sys/firmware/acpi/platform_profile", "balanced")
    put(root, "/sys/firmware/acpi/platform_profile_choices", "low-power balanced performance")
    put(root, "/sys/power/mem_sleep", "[s2idle] deep")
    st = actions.Settings(path=root / "settings.json")
    return st, actions.build(st), root


def test_choosing_a_profile_takes_control_and_system_gives_it_back(acts):
    st, a, root = acts
    a["set_profile"].fn({"profile": "performance"})
    assert st.overrides()["profile"] is True
    assert (root / "sys/firmware/acpi/platform_profile").read_text() == "performance"
    a["set_profile"].fn({"profile": "system"})
    assert st.overrides()["profile"] is False
    # System means hands off: the current value is left alone, and nothing is re-applied after resume
    assert (root / "sys/firmware/acpi/platform_profile").read_text() == "performance"
    (root / "sys/firmware/acpi/platform_profile").write_text("low-power")
    assert st.reapply() == []
    assert (root / "sys/firmware/acpi/platform_profile").read_text() == "low-power"


def test_system_is_reflected_in_get_state_and_survives_restart(acts):
    st, a, root = acts
    assert a["get_state"].fn({})["overrides"] == {"profile": False, "mem_sleep": False, "charge_limit": False,
                                                  "dgpu_always_on": False, "lid_wake": False, "auto_profile": False,
                                                  "kbd_timeout": None}
    a["set_mem_sleep"].fn({"mem_sleep": "deep"})
    assert a["get_state"].fn({})["overrides"]["mem_sleep"] is True
    again = actions.Settings(path=root / "settings.json")
    assert again.overrides()["mem_sleep"] is True
    a["set_mem_sleep"].fn({"mem_sleep": "system"})
    assert actions.Settings(path=root / "settings.json").overrides()["mem_sleep"] is False


def test_charge_limit_system_resets_to_full_and_releases(acts):
    st, a, root = acts
    b = "/sys/class/power_supply/BAT1"
    put(root, b + "/type", "Battery")
    put(root, b + "/charge_control_end_threshold", "100")
    a["set_charge_limit"].fn({"charge_limit": 80})
    assert (root / "sys/class/power_supply/BAT1/charge_control_end_threshold").read_text() == "80"
    assert st.overrides()["charge_limit"]
    a["set_charge_limit"].fn({"charge_limit": "system"})
    assert (root / "sys/class/power_supply/BAT1/charge_control_end_threshold").read_text() == "100"
    assert not st.overrides()["charge_limit"]


def test_charge_limit_system_still_releases_when_battery_has_no_limit_control(acts):
    st, a, root = acts
    st.set("charge_limit", 80)
    a["set_charge_limit"].fn({"charge_limit": "system"})       # no threshold file: must not raise
    assert not st.overrides()["charge_limit"]


def test_dgpu_off_means_system_decides(acts):
    st, a, root = acts
    put(root, "/sys/bus/pci/devices/0000:01:00.0/vendor", "0x10de")
    put(root, "/sys/bus/pci/devices/0000:01:00.0/class", "0x030200")
    put(root, "/sys/bus/pci/devices/0000:01:00.0/power/control", "auto")
    a["set_dgpu_power"].fn({"dgpu_always_on": True})
    assert (root / "sys/bus/pci/devices/0000:01:00.0/power/control").read_text() == "on" and st.overrides()["dgpu_always_on"]
    a["set_dgpu_power"].fn({"dgpu_always_on": False})
    assert (root / "sys/bus/pci/devices/0000:01:00.0/power/control").read_text() == "auto"
    assert not st.overrides()["dgpu_always_on"]


def test_lid_default_removes_only_our_own_override(root, monkeypatch):
    monkeypatch.setattr(sleep.sysfs, "run", lambda *a, **k: None)
    sleep.set_lid_action("ignore")
    assert sleep.state()["lid_action"] == "ignore"
    sleep.set_lid_action("default")
    assert sleep.state()["lid_action"] == "default"
    assert not (root / sleep.LOGIND_DROPIN.lstrip("/")).exists()
    # a drop-in the user wrote themselves is never touched
    put(root, sleep.LOGIND_DROPIN, "[Login]\nHandleLidSwitch=ignore\n")
    sleep.set_lid_action("default")
    assert (root / sleep.LOGIND_DROPIN.lstrip("/")).exists()
    with pytest.raises(ValueError):
        sleep.set_lid_action("explode")


def test_an_invalid_profile_is_still_rejected_and_not_saved(acts):
    st, a, root = acts
    with pytest.raises(ValueError):
        a["set_profile"].fn({"profile": "turbo"})
    assert not st.overrides()["profile"]
