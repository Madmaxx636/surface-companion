import pytest

from conftest import put
from surface_companion import device
from surface_companion.hw import battery, camera, display, perf, sleep, thermal


def test_brightness_scales_and_never_goes_black(root):
    put(root, "/sys/class/backlight/a/max_brightness", "1000")
    put(root, "/sys/class/backlight/a/brightness", "500")
    put(root, "/sys/class/backlight/a/type", "raw")
    display.set_screen_percent(0)
    assert (root / "sys/class/backlight/a/brightness").read_text() == "10"
    display.set_screen_percent(100)
    assert (root / "sys/class/backlight/a/brightness").read_text() == "1000"
    assert display.state()["screen"]["percent"] == 100


def test_firmware_backlight_preferred_over_raw(root):
    for n, t in (("raw0", "raw"), ("fw0", "firmware")):
        put(root, f"/sys/class/backlight/{n}/max_brightness", "100")
        put(root, f"/sys/class/backlight/{n}/brightness", "10")
        put(root, f"/sys/class/backlight/{n}/type", t)
    assert display.state()["screen"]["name"] == "fw0"


def test_keyboard_backlight_clamped(root):
    put(root, "/sys/class/leds/surface::kbd_backlight/max_brightness", "3")
    put(root, "/sys/class/leds/surface::kbd_backlight/brightness", "0")
    display.set_keyboard_level(99)
    assert (root / "sys/class/leds/surface::kbd_backlight/brightness").read_text() == "3"
    (root / "sys/class/leds/surface::kbd_backlight").rename(root / "sys/class/leds/other")
    with pytest.raises(RuntimeError):
        display.set_keyboard_level(1)


def test_platform_profile(root):
    put(root, "/sys/firmware/acpi/platform_profile", "balanced")
    put(root, "/sys/firmware/acpi/platform_profile_choices", "low-power balanced balanced-performance performance")
    st = perf.state()
    assert [c["id"] for c in st["choices"]] == ["low-power", "balanced", "balanced-performance", "performance"]
    perf.set_profile("performance")
    assert (root / "sys/firmware/acpi/platform_profile").read_text() == "performance"
    with pytest.raises(ValueError):
        perf.set_profile("turbo")


def test_legacy_perf_mode(root):
    node = "/sys/bus/surface_aggregator/devices/01:03:01:00:01/perf_mode"
    put(root, node, "1")
    assert perf.state()["current"] == "balanced"
    perf.set_profile("performance")
    assert (root / node.lstrip("/")).read_text() == "4"


def test_sleep_modes_and_lid(root):
    put(root, "/sys/power/mem_sleep", "[s2idle] deep")
    assert sleep.state()["mem_sleep"] == {"modes": ["s2idle", "deep"], "current": "s2idle"}
    sleep.set_mem_sleep("deep")
    assert (root / "sys/power/mem_sleep").read_text() == "deep"
    with pytest.raises(ValueError):
        sleep.set_mem_sleep("shallow")
    sleep.set_lid_action("ignore")
    assert sleep.state()["lid_action"] == "ignore"
    with pytest.raises(ValueError):
        sleep.set_lid_action("explode")


def test_lid_wake_toggle(root):
    put(root, "/sys/bus/acpi/devices/PNP0C0D:00/power/wakeup", "enabled")
    sleep.set_lid_wake(False)
    assert sleep.state()["lid_wake"] is False


def test_thermal_reads_fans_and_temps(root):
    put(root, "/sys/class/hwmon/hwmon0/name", "coretemp")
    put(root, "/sys/class/hwmon/hwmon0/temp1_input", "45500")
    put(root, "/sys/class/hwmon/hwmon1/name", "surface_fan")
    put(root, "/sys/class/hwmon/hwmon1/fan1_input", "2300")
    st = thermal.state()
    assert st["temps"] == [{"label": "CPU", "celsius": 45.5, "crit": None}]
    assert st["fans"][0]["rpm"] == 2300
    assert st["fan_control"] is False


def test_battery_charge_limit(root):
    b = "/sys/class/power_supply/BAT1"
    put(root, b + "/type", "Battery")
    put(root, b + "/capacity", "80")
    put(root, b + "/energy_full", "90")
    put(root, b + "/energy_full_design", "100")
    assert battery.state()["health_percent"] == 90
    assert battery.state()["charge_limit"] is None
    with pytest.raises(RuntimeError):
        battery.set_charge_limit(80)
    put(root, b + "/charge_control_end_threshold", "100")
    battery.set_charge_limit(10)           # clamped to 50
    assert (root / "sys/class/power_supply/BAT1/charge_control_end_threshold").read_text() == "50"


def test_device_detection(root):
    put(root, "/sys/class/dmi/id/sys_vendor", "Microsoft Corporation")
    put(root, "/sys/class/dmi/id/product_name", "Surface Laptop Studio")
    put(root, "/proc/cpuinfo", "vendor_id\t: GenuineIntel")
    d = device.detect()
    assert d.is_surface and d.family == "laptop-studio" and d.cpu_vendor == "intel"
    assert not d.has_dtx


def test_camera_lookup_prefers_longest_prefix():
    assert camera.lookup("Surface Pro 7+") == ("IPU6", "partial")
    assert camera.lookup("Surface Pro 7") == ("IPU4", "none")
    assert camera.lookup("Surface Laptop 3")[1] == "ok"
    assert camera.lookup("Toaster") == ("unknown", "unknown")


def test_thermal_prefers_package_sensor_and_reads_throttle(root):
    put(root, "/sys/class/hwmon/hwmon0/name", "coretemp")
    put(root, "/sys/class/hwmon/hwmon0/temp1_input", "50000")
    put(root, "/sys/class/hwmon/hwmon0/temp1_label", "Package id 0")
    put(root, "/sys/class/hwmon/hwmon0/temp2_input", "70000")
    put(root, "/sys/class/hwmon/hwmon0/temp2_label", "Core 0")
    put(root, "/sys/class/thermal/cooling_device1/type", "Processor")
    put(root, "/sys/class/thermal/cooling_device1/cur_state", "5")
    put(root, "/sys/class/thermal/cooling_device1/max_state", "10")
    put(root, "/sys/class/thermal/cooling_device2/type", "Fan")
    put(root, "/sys/class/thermal/cooling_device2/cur_state", "2")
    put(root, "/sys/class/thermal/cooling_device2/max_state", "4")
    st = thermal.state()
    assert st["temps"][0]["celsius"] == 50.0
    assert st["throttle"] == {"level": 5, "max": 10}
    assert st["fan_levels"] == [{"label": "Fan", "level": 2, "max": 4}]
    assert len(st["all_sensors"]) == 2


def test_thermal_falls_back_to_package_zone(root):
    put(root, "/sys/class/thermal/thermal_zone0/type", "x86_pkg_temp")
    put(root, "/sys/class/thermal/thermal_zone0/temp", "61000")
    assert thermal.state()["temps"] == [{"label": "CPU", "celsius": 61.0, "crit": None}]
