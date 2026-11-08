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
