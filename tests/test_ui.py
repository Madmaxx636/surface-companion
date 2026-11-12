import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = __import__("tempfile").mkdtemp()      # keep tests away from real preferences
pytest.importorskip("PyQt6.QtWidgets")


def test_every_page_builds_and_renders_state(root):
    """Smoke test: window, all pages, light and dark, against a fake Surface Book."""
    from conftest import put
    from PyQt6.QtWidgets import QApplication
    from surface_companion.ui import theme
    from surface_companion.ui.backend import Backend
    from surface_companion.ui.main import MainWindow

    put(root, "/sys/class/dmi/id/sys_vendor", "Microsoft Corporation")
    put(root, "/sys/class/dmi/id/product_name", "Surface Book 2")
    put(root, "/sys/class/backlight/a/max_brightness", "100")
    put(root, "/sys/class/backlight/a/brightness", "40")
    put(root, "/sys/class/backlight/a/type", "raw")
    put(root, "/sys/firmware/acpi/platform_profile", "balanced")
    put(root, "/sys/firmware/acpi/platform_profile_choices", "low-power balanced performance")
    from surface_companion import actions
    for c in (actions._dev_cache, actions._distro_cache, actions._thermal_cache, actions._slow_cache):
        c.v = None

    app = QApplication.instance() or QApplication([])
    for scheme in ("light", "dark"):
        os.environ["SC_THEME"] = scheme
        theme.apply(app)
        w = MainWindow(Backend(session_bus=True))
        w.show()
        import time
        t = time.time()
        while time.time() - t < 1.5:
            app.processEvents()
        assert w.state and w.state["device"]["family"] == "book"
        assert w.nav["book"].isVisibleTo(w)
        for key in w.pages:
            w.goto(key)
            app.processEvents()
            assert w.stack.currentWidget() is w.pages[key]
        w.tray = None
        w.timer.stop()
        w.close()
        from PyQt6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(5000)
        app.processEvents()


def test_risk_dialog_needs_explicit_acceptance():
    from PyQt6.QtWidgets import QApplication
    from surface_companion.ui import theme
    from surface_companion.ui.dialogs import RiskDialog
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    dlg = RiskDialog(None, "Unofficial driver", "intro", ["it may break", "it may not boot"], "cmd details")
    assert not dlg.go.isEnabled() and dlg.cancel.isDefault()
    dlg.check.setChecked(True)
    assert dlg.go.isEnabled()
    dlg.check.setChecked(False)
    assert not dlg.go.isEnabled()


def _app():
    from PyQt6.QtWidgets import QApplication
    from surface_companion.ui import theme
    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    return app


def _pump(app, secs):
    import time
    t = time.time()
    while time.time() - t < secs:
        app.processEvents()


def test_simple_and_advanced_views_toggle_widgets(root, monkeypatch, tmp_path):
    from surface_companion.ui import prefs
    monkeypatch.setattr(prefs, "get", lambda k, d=None: d)
    monkeypatch.setattr(prefs, "put", lambda k, v: None)
    from surface_companion.ui.backend import Backend
    from surface_companion.ui.main import MainWindow
    app = _app()
    w = MainWindow(Backend(session_bus=True))
    w.show()
    _pump(app, 0.8)
    ov = w.pages["overview"]
    assert not w.advanced and not ov.therm_card.isVisibleTo(w) and ov.simple_temp.isVisibleTo(w)
    assert not ov.clocks_card.isVisibleTo(w)
    w.set_advanced(True)
    assert ov.therm_card.isVisibleTo(w) and ov.clocks_card.isVisibleTo(w) and not ov.simple_temp.isVisibleTo(w)
    w.set_advanced(False)
    assert not ov.clocks_card.isVisibleTo(w)
    w.tray = None
    w.timer.stop()
    w.close()


def test_simple_view_hides_optional_fixes_and_says_so(root, monkeypatch):
    from surface_companion.ui import prefs
    monkeypatch.setattr(prefs, "get", lambda k, d=None: d)
    monkeypatch.setattr(prefs, "put", lambda k, v: None)
    from surface_companion.ui.backend import Backend
    from surface_companion.ui.main import MainWindow
    app = _app()
    w = MainWindow(Backend(session_bus=True))
    w.show()
    fx = [{"id": "a", "title": "A", "summary": "s", "category": "c", "source": "u", "level": "recommended",
           "status": "available", "scope": "system", "reboot": False, "revertable": True, "steps": "", "applicable": True, "note": ""},
          {"id": "b", "title": "B", "summary": "s", "category": "c", "source": "u", "level": "optional",
           "status": "available", "scope": "system", "reboot": False, "revertable": True, "steps": "", "applicable": True, "note": ""}]
    page = w.pages["fixes"]
    page.render(fx)
    assert page.list.count() == 1 and page.hidden_note.isVisibleTo(page.window()) is not None
    assert "1 more optional" in page.hidden_note.text()
    w.set_advanced(True)
    assert page.list.count() == 2 and page.hidden_note.isHidden()
    w.tray = None
    w.timer.stop()
    w.close()


def test_tour_walks_every_sidebar_page_and_ends(root, monkeypatch):
    from surface_companion.ui import prefs
    saved = {}
    monkeypatch.setattr(prefs, "get", lambda k, d=None: saved.get(k, d))
    monkeypatch.setattr(prefs, "put", lambda k, v: saved.__setitem__(k, v))
    from surface_companion.ui.backend import Backend
    from surface_companion.ui.main import MainWindow
    app = _app()
    w = MainWindow(Backend(session_bus=True))
    w.show()
    w.tray = None
    w.start_tour()
    titles = [s["title"] for s in w.tour.steps]
    assert titles[0] == "Overview" and "Simple or Advanced" in titles and "Come back any time" in titles
    assert "Surface Book" not in titles                          # not a Book
    for i in range(len(w.tour.steps)):
        w.tour.show_step(i)
        assert w.tour.isVisible()
    w.tour.show_step(len(w.tour.steps))
    assert not w.tour.isVisible() and saved.get("tour_done") is True
    w.timer.stop()
    w.close()


def test_wizard_scan_runs_all_steps_with_local_backend(root, monkeypatch):
    from conftest import put
    from surface_companion.ui import prefs
    saved = {}
    monkeypatch.setattr(prefs, "get", lambda k, d=None: saved.get(k, d))
    monkeypatch.setattr(prefs, "put", lambda k, v: saved.__setitem__(k, v))
    put(root, "/sys/class/dmi/id/sys_vendor", "Microsoft Corporation")
    put(root, "/sys/class/dmi/id/product_name", "Surface Laptop 3")
    from surface_companion import actions
    for c in (actions._dev_cache, actions._distro_cache, actions._thermal_cache, actions._slow_cache):
        c.v = None
    from surface_companion.ui.backend import Backend
    from surface_companion.ui.main import MainWindow
    from surface_companion.ui.welcome import WelcomeWizard
    app = _app()
    w = MainWindow(Backend(session_bus=True))
    w.show()
    dlg = WelcomeWizard(w)
    dlg.show()
    dlg._next()                                             # Welcome -> Scan
    for _ in range(80):
        _pump(app, 0.25)
        if dlg.stack.currentIndex() >= 2:
            break
    assert dlg.stack.currentIndex() == 2, "scan should land on the results page"
    assert dlg.bar.value() == len(dlg.scan_rows)
    assert dlg.summary["dev"]["model"] == "Surface Laptop 3"
    dlg._finish()
    assert saved.get("firstrun_done") is True
    w.tray = None
    w.timer.stop()
    w.close()


def test_book_page_offers_nvidia_install_only_when_driver_is_not_nvidia(root, monkeypatch):
    from surface_companion.ui import prefs
    monkeypatch.setattr(prefs, "get", lambda k, d=None: d)
    monkeypatch.setattr(prefs, "put", lambda k, v: None)
    from surface_companion.ui.backend import Backend
    from surface_companion.ui.main import MainWindow
    app = _app()
    w = MainWindow(Backend(session_bus=True))
    w.show()
    page = w.pages["book"]
    st = {"book": {"dtx": False, "dgpu": {"driver": "nouveau", "power_state": "D3cold", "runtime_status": "suspended",
                                         "power_control": "auto"}}}
    page.update_state(st)
    assert not page.nv_row.isHidden() and "nouveau" in page.nv_row.desc.text()
    st["book"]["dgpu"]["driver"] = "nvidia"
    page.update_state(st)
    assert page.nv_row.isHidden()
    w.tray = None
    w.timer.stop()
    w.close()


def test_performance_page_selects_system_until_you_choose_a_profile(root, monkeypatch):
    from surface_companion.ui import prefs
    monkeypatch.setattr(prefs, "get", lambda k, d=None: d)
    monkeypatch.setattr(prefs, "put", lambda k, v: None)
    from surface_companion.ui.backend import Backend
    from surface_companion.ui.main import MainWindow
    app = _app()
    w = MainWindow(Backend(session_bus=True))
    w.show()
    page = w.pages["performance"]
    perf = {"available": True, "source": "platform_profile", "current": "balanced",
            "choices": [{"id": "low-power", "label": "Battery Saver", "hint": "h"},
                        {"id": "balanced", "label": "Recommended", "hint": "h"}]}
    base = {"performance": perf, "thermal": {"temps": [], "fans": [], "fan_levels": [], "all_sensors": [],
                                             "throttle": {"level": 0, "max": 10}, "thermald": {"installed": False, "active": False}},
            "clocks": {}}
    page.update_state({**base, "overrides": {"profile": False}})
    assert list(page.profiles.buttons)[0] == "system" and page.profiles.buttons["system"].isChecked()
    assert "Right now: Recommended" in page.profiles.hints["system"].text()
    page.update_state({**base, "overrides": {"profile": True}})
    assert page.profiles.buttons["balanced"].isChecked() and not page.profiles.buttons["system"].isChecked()
    w.tray = None
    w.timer.stop()
    w.close()


def _win(monkeypatch, saved=None):
    from surface_companion.ui import prefs
    saved = {} if saved is None else saved
    monkeypatch.setattr(prefs, "get", lambda k, d=None: saved.get(k, d))
    monkeypatch.setattr(prefs, "put", lambda k, v: saved.__setitem__(k, v))
    from surface_companion.ui.backend import Backend
    from surface_companion.ui.main import MainWindow
    app = _app()
    w = MainWindow(Backend(session_bus=True))
    w.show()
    w.tray = None
    w.timer.stop()
    return app, w, saved


def _base_state(**over):
    st = {"device": {"model": "Surface Laptop 3", "family": "laptop", "kernel": "k", "is_surface": True, "has_dtx": False,
                     "surface_kernel": True, "cpu_vendor": "intel"},
          "display": {"screen": {"percent": 50, "name": "x", "value": 1, "max": 2}, "keyboard": {"name": "k", "value": 2, "max": 3}},
          "performance": {"available": True, "current": "balanced", "source": "platform_profile",
                          "choices": [{"id": "low-power", "label": "Battery Saver", "hint": "h"},
                                      {"id": "balanced", "label": "Recommended", "hint": "h"}]},
          "thermal": {"temps": [], "fans": [], "fan_levels": [], "all_sensors": [], "throttle": {"level": 0, "max": 10},
                      "thermald": {"installed": False, "active": False}},
          "clocks": {}, "battery": {"present": True, "percent": 85, "status": "Charging", "charge_limit": None,
                                     "health_percent": 90, "cycles": 1},
          "sleep": {"mem_sleep": {"modes": ["s2idle", "deep"], "current": "s2idle"}, "s0ix_residency_us": 0,
                    "can_hibernate": False, "lid_wake": True, "lid_action": "default"},
          "book": {"dtx": False, "dgpu": None}, "overrides": {}, "auto_profile_config": None}
    st.update(over)
    return st


def test_brightness_system_toggle_locks_both_sliders_and_is_remembered(root, monkeypatch):
    app, w, saved = _win(monkeypatch)
    w.state = _base_state()
    page = w.pages["display"]
    page.update_state(w.state)
    assert page.bright_row.isEnabled() and not page.bright_system.isChecked()
    w.set_brightness_system(True)
    assert saved["brightness_system"] is True
    page.update_state(w.state)
    assert not page.bright_row.isEnabled() and page.bright_system.isChecked()
    assert "system" in page.bright_row.desc.text().lower()
    w.pages["overview"].update_state(w.state)
    assert not w.pages["overview"].bright.isEnabled()
    w.set_brightness_system(False)
    page.update_state(w.state)
    assert page.bright_row.isEnabled()
    w.close()


def test_sleep_master_switch_disables_the_individual_controls(root, monkeypatch):
    app, w, saved = _win(monkeypatch)
    page = w.pages["sleep"]
    page.update_state(_base_state())                        # no overrides, lid default: system manages sleep
    assert page.sleep_system.isChecked() and not page.lid_row.isEnabled() and not page.mode_row.isEnabled()
    saved["sleep_manual"] = True                            # user switched the master off
    page.update_state(_base_state())
    assert not page.sleep_system.isChecked() and page.lid_row.isEnabled() and page.mode_row.isEnabled()
    saved.clear()
    page.update_state(_base_state(overrides={"mem_sleep": True}))   # an existing override means manual by default
    assert not page.sleep_system.isChecked()
    w.close()


def test_charge_limit_unsupported_shows_reminder_and_supported_shows_limit(root, monkeypatch):
    app, w, saved = _win(monkeypatch)
    page = w.pages["sleep"]
    page.update_state(_base_state())
    assert not page.no_limit.isHidden() and not page.remind_toggle_row.isHidden() and page.limit_toggle_row.isHidden()
    st = _base_state()
    st["battery"]["charge_limit"] = 100
    page.update_state(st)
    assert page.no_limit.isHidden() and not page.limit_toggle_row.isHidden()
    w.close()


def test_unplug_reminder_fires_once_per_charge_at_the_chosen_level(root, monkeypatch):
    app, w, saved = _win(monkeypatch, {"reminder_on": True, "reminder_pct": 80})
    msgs = []
    w.notify = lambda t, x: msgs.append(t)
    low = _base_state()
    low["battery"].update(percent=70, status="Charging")
    w._check_charge_reminder(low)
    assert msgs == []
    hi = _base_state()
    hi["battery"].update(percent=81, status="Charging")
    w._check_charge_reminder(hi)
    w._check_charge_reminder(hi)
    assert len(msgs) == 1 and "81%" in msgs[0]
    dis = _base_state()
    dis["battery"].update(percent=79, status="Discharging")
    w._check_charge_reminder(dis)
    w._check_charge_reminder(hi)
    assert len(msgs) == 2                                    # re-armed after unplugging
    saved["reminder_on"] = False
    w._reminded = False
    w._check_charge_reminder(hi)
    assert len(msgs) == 2
    w.close()


def test_no_reminder_when_hardware_limit_exists(root, monkeypatch):
    app, w, saved = _win(monkeypatch, {"reminder_on": True, "reminder_pct": 50})
    msgs = []
    w.notify = lambda t, x: msgs.append(t)
    st = _base_state()
    st["battery"].update(percent=99, status="Charging", charge_limit=80)
    w._check_charge_reminder(st)
    assert msgs == []
    w.close()


def test_performance_page_automatic_card_and_defaults(root, monkeypatch):
    app, w, saved = _win(monkeypatch)
    page = w.pages["performance"]
    calls = []
    page.act = lambda action, args=None, ok=None: calls.append((action, args))
    cfg = {"battery": "low-power", "ac": "balanced"}
    page.update_state(_base_state(overrides={"auto_profile": True}, auto_profile_config=cfg))
    assert list(page.profiles.buttons)[:2] == ["system", "auto"] and page.profiles.buttons["auto"].isChecked()
    assert not page.auto_card.isHidden()
    assert page.auto_batt.currentData() == "low-power" and page.auto_ac.currentData() == "balanced"
    page.auto_ac.setCurrentIndex(page.auto_ac.findData("system"))
    page._auto_changed()
    assert calls[-1] == ("set_auto_profile", {"battery": "low-power", "ac": "system"})
    page._chosen("auto")
    assert calls[-1] == ("set_auto_profile", {"battery": "low-power", "ac": "balanced"})
    page._chosen("balanced")
    assert calls[-1] == ("set_profile", {"profile": "balanced"})
    page.update_state(_base_state())
    assert page.auto_card.isHidden() and page.profiles.buttons["system"].isChecked()
    w.close()


def test_keyboard_timeout_selector_reflects_state_and_sends_choice(root, monkeypatch):
    app, w, saved = _win(monkeypatch)
    page = w.pages["display"]
    calls = []
    page.act = lambda action, args=None, ok=None: calls.append((action, args))
    page.update_state(_base_state(overrides={"kbd_timeout": 30}))
    assert page.kbd_timeout.currentData() == 30
    page.update_state(_base_state(overrides={"kbd_timeout": 45}))      # a value set elsewhere still shows
    assert page.kbd_timeout.currentData() == 45
    page.update_state(_base_state())
    assert page.kbd_timeout.currentData() == "system"
    i = page.kbd_timeout.findData(10)
    page.kbd_timeout.setCurrentIndex(i)
    page.kbd_timeout.activated.emit(i)
    assert calls[-1] == ("set_kbd_timeout", {"seconds": 10})
    w.close()


# ------------------------------------------------------------------ system test page and hands-on dialogs

def test_keyboard_test_lights_keys_and_passes_at_ninety_percent(root, monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from surface_companion.ui import handson as ho
    app = _app()
    dlg = ho.KeyboardTest(None)
    dlg.show()
    assert not dlg.ok_btn.isEnabled()
    QTest.keyClick(dlg, Qt.Key.Key_Escape)                      # must not close the dialog
    assert dlg.isVisible() and 1 in dlg.grid.pressed
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        QTest.keyClick(dlg, getattr(Qt.Key, "Key_" + ch))
    for k in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Backspace, Qt.Key.Key_Tab, Qt.Key.Key_Up, Qt.Key.Key_Down,
              Qt.Key.Key_Left, Qt.Key.Key_Right):
        QTest.keyClick(dlg, k)
    assert "keys detected" in dlg.count.text()
    assert not dlg.ok_btn.isEnabled()                             # symbols and F-keys still missing
    for code in dlg.grid.codes():
        dlg.grid.press(code)
    dlg._update()
    assert dlg.ok_btn.isEnabled() and dlg.note.startswith("70 of 70") or dlg.ok_btn.isEnabled()
    dlg.close()


def test_evdev_code_prefers_native_scancode_and_falls_back_to_the_qt_key():
    from PyQt6.QtCore import Qt
    from surface_companion.ui import handson as ho

    class E:
        def __init__(self, sc, key): self._sc, self._k = sc, key
        def nativeScanCode(self): return self._sc
        def key(self): return self._k
    assert ho.evdev_code(E(38, Qt.Key.Key_A)) == 30          # xkb keycode 38 = evdev 30 = A, whatever the layout says
    assert ho.evdev_code(E(0, Qt.Key.Key_Q)) == 16
    assert ho.evdev_code(E(0, Qt.Key.Key_F13)) is None


def test_pointer_test_needs_move_clicks_and_scroll(root):
    from surface_companion.ui import handson as ho
    app = _app()
    dlg = ho.PointerTest(None)
    for ev in ("move", "left", "right", "double"):
        dlg._event(ev)
    assert not dlg.ok_btn.isEnabled()
    dlg._event("scroll")
    assert dlg.ok_btn.isEnabled()                                  # middle click and touch are optional
    assert "not detected: Middle click" in dlg.note and "Touch the screen" in dlg.note
    dlg.close()


def test_speaker_test_needs_every_side_heard(root, monkeypatch):
    from surface_companion import handson
    from surface_companion.ui import handson as ho
    app = _app()
    monkeypatch.setattr(handson, "player_cmd", lambda p: None)
    dlg = ho.SpeakerTest(None)
    assert "No audio player" in dlg.status.text() and not dlg.ok_btn.isEnabled()
    for cb in dlg.heard.values():
        cb.setChecked(True)
    assert dlg.ok_btn.isEnabled()
    dlg.close()


def test_ports_and_detach_dialogs_report_what_they_saw(root, monkeypatch):
    from surface_companion import handson
    from surface_companion.ui import handson as ho
    app = _app()
    snaps = iter([{"usb": {}, "typec": {}, "displays": {}, "jacks": {}},
                  {"usb": {}, "typec": {}, "displays": {}, "jacks": {}},
                  {"usb": {"3-1": {"name": "SanDisk Ultra", "removable": "removable", "speed": "480"}}, "typec": {},
                   "displays": {}, "jacks": {}}])
    last = {"s": None}
    def snap():
        try:
            last["s"] = next(snaps)
        except StopIteration:
            pass
        return last["s"]
    monkeypatch.setattr(handson, "ports_snapshot", snap)
    dlg = ho.PortsTest(None)
    dlg._poll()
    dlg._poll()
    assert "USB device plugged in: SanDisk Ultra" in dlg.log.toPlainText() and "1 plug/unplug" in dlg.note
    dlg.close()
    kbs = iter([{"keyboards": 1, "touchpads": 1}, {"keyboards": 1, "touchpads": 1}, {"keyboards": 0, "touchpads": 0},
                {"keyboards": 1, "touchpads": 1}])
    cur = {"v": {"keyboards": 1, "touchpads": 1}}
    def kb():
        try:
            cur["v"] = next(kbs)
        except StopIteration:
            pass
        return cur["v"]
    monkeypatch.setattr(handson, "keyboard_state", kb)
    d = ho.DetachTest(None, "pro")
    d.timer.stop()
    assert not d.ok_btn.isEnabled()
    d._poll()                                                      # keyboard disappears
    assert "keyboard detached" in d.log.toPlainText()
    assert not d.ok_btn.isEnabled()                                # attached again not yet seen
    d._poll()                                                      # and comes back
    assert d.ok_btn.isEnabled() and "keyboard attached" in d.note
    d.close()


def test_book_detach_test_reads_dtx_and_passes_on_a_mode_change(root, monkeypatch):
    from surface_companion import handson
    from surface_companion.ui import handson as ho
    app = _app()
    modes = iter(["laptop", "laptop", "tablet"])
    cur = {"m": "laptop"}
    def ds():
        try:
            cur["m"] = next(modes)
        except StopIteration:
            pass
        return {"dtx": True, "mode": cur["m"], "latch": "closed", "base_attached": True}
    monkeypatch.setattr(handson, "detach_state", ds)
    monkeypatch.setattr(handson, "keyboard_state", lambda: {"keyboards": 1, "touchpads": 1})
    d = ho.DetachTest(None, "book")
    d.timer.stop()
    assert "never detaches" in d.layout().itemAt(1).widget().text()
    assert not d.ok_btn.isEnabled()                                # still in laptop mode
    d._poll()                                                      # screen goes to tablet mode
    assert d.ok_btn.isEnabled() and "screen mode change" in d.note
    d.close()


def test_system_test_page_runs_checks_and_builds_a_report(root, monkeypatch):
    from conftest import put
    app, w, saved = _win(monkeypatch)
    put(root, "/sys/class/dmi/id/sys_vendor", "Microsoft Corporation")
    put(root, "/sys/class/dmi/id/product_name", "Surface Book 2")
    put(root, "/sys/class/backlight/a/max_brightness", "100")
    put(root, "/sys/class/backlight/a/brightness", "50")
    put(root, "/sys/class/backlight/a/type", "raw")
    from surface_companion import actions, systemtest
    monkeypatch.setattr(systemtest, "_kernel_log", lambda: "")
    for c in (actions._dev_cache, actions._distro_cache, actions._thermal_cache, actions._slow_cache):
        c.v = None
    w.state = _base_state()
    w.state["device"]["family"] = "book"
    w.distro_name = "Ubuntu"
    page = w.pages["test"]
    page.update_state(w.state)
    assert set(page.hands_rows) == {"keyboard", "pointer", "speakers", "ports", "camera", "detach"}
    assert page.hands_rows["detach"][0].title.text() == "Screen detach"
    page.run_all()
    for _ in range(120):
        _pump(app, 0.1)
        if not page.running:
            break
    assert not page.running and len(page.results) == len(systemtest.TITLES)
    assert "passed" in page.summary_text.text()
    page.hands_results["keyboard"] = {"id": "hw-keyboard", "title": "Keyboard", "status": "ok", "detail": "70 of 70 keys",
                                      "hint": "", "goto": None}
    rep = page.report()
    assert "Surface Companion system test" in rep and "[PASS] Keyboard: 70 of 70 keys" in rep and "Device:" in rep
    w.close()


def test_system_test_page_for_a_laptop_has_no_detach_test_and_stress_is_advanced_only(root, monkeypatch):
    app, w, saved = _win(monkeypatch)
    page = w.pages["test"]
    page.update_state(_base_state())
    assert "detach" not in page.hands_rows
    assert page.stress_card.isHidden()                             # Simple view
    w.set_advanced(True)
    assert not page.stress_card.isHidden()
    w.close()


def test_fixes_page_shows_conflicts_without_an_apply_button_and_the_software_page_offers_to_enable_dtx(root, monkeypatch):
    from PyQt6.QtWidgets import QLabel, QPushButton
    app, w, saved = _win(monkeypatch)
    page = w.pages["fixes"]
    fx = {"id": "gpe-lid-wake", "title": "Opening the lid doesn't wake the device", "summary": "s", "category": "c", "source": "u",
          "level": "recommended", "status": "conflict", "scope": "system", "reboot": False, "revertable": True, "steps": "",
          "applicable": True, "note": "Conflicts with “Closing the lid wakes a sleeping device”, which is applied. Undo that one first."}
    page.render([fx])
    card = page.list.itemAt(0).widget()
    labels = [lb.text() for lb in card.findChildren(QLabel)]
    assert "Conflicts" in labels and any("Undo that one first" in t for t in labels)
    assert [b.text() for b in card.findChildren(QPushButton)] == []                 # no Apply, no Undo
    fx2 = dict(fx, id="x", status="available", note="")
    page.render([fx2])
    assert [b.text() for b in page.list.itemAt(0).widget().findChildren(QPushButton)] == ["Apply"]
    sw = w.pages["software"]
    data = {"distro": {"name": "Ubuntu", "family": "debian", "immutable": False},
            "system": {"kernel": "k", "surface_kernel": True, "repo": True, "secure_boot": "enabled", "tlp": False},
            "assessment": {"level": "supported", "reasons": []}, "nvidia": {},
            "components": [{"id": "dtx", "title": "Clipboard detach daemon", "why": "w", "installable": True, "reason": "",
                            "installed": "0.3.12-1", "reboot": False,
                            "services": {"running": False, "state": {"system": {"surface-dtx-daemon.service": "active"},
                                                                     "user": {"lane": {"surface-dtx-userd.service": "inactive"}}}}}]}
    sw.render(data)
    texts = [lb.text() for lb in sw.comp_rows.parentWidget().findChildren(QLabel)]
    assert any("surface-dtx-userd (lane): inactive" in t for t in texts)
    assert "Enable services" in [b.text() for b in sw.comp_rows.parentWidget().findChildren(QPushButton)]
    w.close()
