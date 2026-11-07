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
