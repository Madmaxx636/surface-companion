from __future__ import annotations

from collections import defaultdict, deque

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (QMenu as _QMenu, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton,
                             QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget)

from . import icons, prefs, theme
from .backend import Backend
import subprocess

from ..pkg import community, nvidia as nvidia_mod
from .dialogs import JobDialog, confirm, confirm_risk, notify
from .welcome import Tour, WelcomeWizard
from .pages import (BookPage, CameraPage, DisplayPage, FixesPage, OverviewPage, PenPage, PerformancePage,
                    PowerPage, SoftwarePage)
from .widgets import label

POLL_MS = 2500


class MainWindow(QMainWindow):
    def __init__(self, backend: Backend):
        super().__init__()
        self.backend = backend
        self.state: dict | None = None
        self.software: dict | None = None
        self.updates: list = []
        self.history: dict[str, deque] = defaultdict(lambda: deque(maxlen=120))   # ~5 minutes of samples
        self.distro_name = ""
        self.advanced = bool(prefs.get("advanced", False))
        self.brightness_system = bool(prefs.get("brightness_system", False))
        self._reminded = False
        self._modal: list = []             # (widget, advanced_only, simple_only)
        self.setWindowTitle("Surface Companion")
        self.setWindowIcon(icons.app_icon())
        self.resize(1000, 700)
        self.setMinimumSize(760, 520)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(210)
        sl = QVBoxLayout(self.sidebar)
        sl.setContentsMargins(10, 14, 10, 12)
        sl.setSpacing(2)
        sl.addWidget(label("Surface Companion", "h2"))
        sl.addSpacing(10)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}
        self.nav: dict[str, QPushButton] = {}
        specs = [("overview", "home", OverviewPage), ("display", "display", DisplayPage),
                 ("performance", "performance", PerformancePage), ("sleep", "sleep", PowerPage),
                 ("book", "book", BookPage), ("pen", "pen", PenPage), ("camera", "camera", CameraPage),
                 ("fixes", "fixes", FixesPage), ("software", "software", SoftwarePage)]
        self.icon_names = {}
        for key, icon, cls in specs:
            page = cls(self)
            self.pages[key] = page
            self.stack.addWidget(page)
            b = QPushButton("  " + page.title.replace("&", "&&"))
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.goto(k))
            self.nav_group.addButton(b)
            sl.addWidget(b)
            self.nav[key] = b
            self.icon_names[key] = icon
        self.nav["book"].setVisible(False)
        sl.addStretch(1)
        self.mode_box = QFrame()
        ml = QHBoxLayout(self.mode_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)
        self.simple_btn, self.adv_btn = QPushButton("Simple"), QPushButton("Advanced")
        self.mode_group = QButtonGroup(self)
        for b, name in ((self.simple_btn, "segl"), (self.adv_btn, "segr")):
            b.setObjectName(name)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            self.mode_group.addButton(b)
            ml.addWidget(b)
        self.simple_btn.setToolTip("Just the essentials")
        self.adv_btn.setToolTip("Everything: clock speeds, history graphs, all sensors, community drivers, extra fixes")
        self.simple_btn.clicked.connect(lambda: self.set_advanced(False))
        self.adv_btn.clicked.connect(lambda: self.set_advanced(True))
        sl.addWidget(self.mode_box)
        self.tour = Tour(self)
        self.help_btn = QPushButton("Setup and tour…")
        self.help_btn.setObjectName("link")
        self.help_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        help_menu = _QMenu(self.help_btn)
        help_menu.addAction("Run setup again", self.run_welcome)
        help_menu.addAction("Take the tour", self.start_tour)
        self.help_btn.clicked.connect(lambda: help_menu.exec(self.help_btn.mapToGlobal(self.help_btn.rect().topRight())))
        sl.addWidget(self.help_btn)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        sl.addWidget(self.status)
        outer.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setSpacing(0)
        outer.addLayout(right, 1)
        right.addWidget(self.stack, 1)
        self.toast_label = QLabel("")
        self.toast_label.setObjectName("banner")
        self.toast_label.setWordWrap(True)
        self.toast_label.setContentsMargins(16, 8, 16, 8)
        self.toast_label.hide()
        right.addWidget(self.toast_label)
        self.toast_timer = QTimer(self, singleShot=True, interval=7000)
        self.toast_timer.timeout.connect(self.toast_label.hide)

        self.nav_group.buttonToggled.connect(self._nav_icons)
        self.set_advanced(self.advanced, save=False)
        self.restyle()
        self.timer = QTimer(self, interval=POLL_MS)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.backend.daemonChanged.connect(lambda _: self.refresh())
        self.goto("overview")
        self.refresh()
        self.backend.call("get_software", ok=self._got_software, err=lambda m: None)
        self._tray()

    # -- theming
    def restyle(self):
        c = theme.colors()
        for key, b in self.nav.items():
            b.setIcon(icons.icon(self.icon_names[key], c["text"]))
        self._nav_icons()

    def _nav_icons(self, *_):
        c = theme.colors()
        for key, b in self.nav.items():
            b.setIcon(icons.icon(self.icon_names[key], c["accent_text"] if b.isChecked() else c["text"]))

    # -- tray
    def _tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icons.app_icon(), self)
        self.tray.setToolTip("Surface Companion")
        self.tray_menu = QMenu()
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda r: self.showNormal() or self.activateWindow()
                                    if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self._tray_profiles = ()
        self.tray.show()

    def _update_tray(self):
        if not self.tray or not self.state:
            return
        th = self.state["thermal"]
        bits = [f"{t['label']} {t['celsius']:.0f} °C" for t in th["temps"][:1]]
        bits += [f"{f['rpm']} RPM" for f in th["fans"][:1]]
        self.tray.setToolTip("Surface Companion" + ("\n" + " · ".join(bits) if bits else ""))
        perf = self.state["performance"]
        ids = tuple(c["id"] for c in perf.get("choices", [])) if perf.get("available") else ()
        if ids != self._tray_profiles:
            self._tray_profiles = ids
            self.tray_menu.clear()
            for c in ([{"id": "system", "label": "System"}] + perf["choices"]) if perf.get("available") else []:
                a = QAction(c["label"], self.tray_menu)
                a.setCheckable(True)
                a.triggered.connect(lambda _=False, cid=c["id"]: self.backend.call(
                    "set_profile", {"profile": cid}, ok=lambda _: self.refresh(), err=self.toast))
                a.setData(c["id"])
                self.tray_menu.addAction(a)
            self.tray_menu.addSeparator()
            self.tray_menu.addAction("Open Surface Companion", lambda: (self.showNormal(), self.activateWindow()))
            self.tray_menu.addAction("Quit", self.close_for_real)
        ovs = self.state.get("overrides") or {}
        for a in self.tray_menu.actions():
            if a.data():
                a.setChecked(not ovs.get("auto_profile") and
                             a.data() == (perf.get("current") if ovs.get("profile") else "system"))

    def close_for_real(self):
        self.tray = None
        from PyQt6.QtWidgets import QApplication
        QApplication.quit()

    def closeEvent(self, e):
        if getattr(self, "tray", None) and self.tray.isVisible():
            self.hide()      # keep running in the tray so profile switching stays one click away
            e.ignore()
        else:
            e.accept()

    # -- first run, setup and tour
    def maybe_first_run(self):
        if not prefs.get("firstrun_done", False):
            self.run_welcome()

    def run_welcome(self):
        dlg = WelcomeWizard(self)
        dlg.exec()
        self.refresh()
        self.backend.call("get_software", ok=self._software_and_page, err=lambda m: None)
        if dlg.want_tour:
            QTimer.singleShot(300, self.start_tour)

    def start_tour(self):
        self.tour.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.tour.isVisible():
            self.tour.show_step(self.tour.i)

    def set_brightness_system(self, on: bool):
        """On: the app never writes screen brightness; the desktop and firmware own it."""
        self.brightness_system = on
        prefs.put("brightness_system", on)
        if self.state:
            self.stack.currentWidget().update_state(self.state)

    def notify(self, title: str, text: str):
        if getattr(self, "tray", None):
            self.tray.showMessage(title, text, icons.app_icon(), 15000)
        else:
            self.toast(f"{title}: {text}")

    def _check_charge_reminder(self, st: dict):
        bat = st.get("battery") or {}
        if not (prefs.get("reminder_on", False) and bat.get("present") and bat.get("charge_limit") is None):
            return
        pct = int(prefs.get("reminder_pct", 80))
        if bat.get("status") == "Charging" and (bat.get("percent") or 0) >= pct:
            if not self._reminded:
                self._reminded = True
                self.notify(f"Battery is at {bat['percent']}%", f"You can unplug now (your reminder level is {pct}%).")
        elif bat.get("status") != "Charging":
            self._reminded = False

    # -- simple / advanced
    def register(self, w, advanced_only=False, simple_only=False):
        self._modal.append((w, advanced_only, simple_only))
        w.setVisible(self._wanted(advanced_only, simple_only))

    def _wanted(self, advanced_only, simple_only) -> bool:
        return (self.advanced or not advanced_only) and (not self.advanced or not simple_only)

    def set_advanced(self, on: bool, save: bool = True):
        self.advanced = on
        (self.adv_btn if on else self.simple_btn).setChecked(True)
        if save:
            prefs.put("advanced", on)
        alive = []
        for w, a, s_ in self._modal:
            try:
                w.setVisible(self._wanted(a, s_))
                alive.append((w, a, s_))
            except RuntimeError:       # widget was rebuilt and deleted
                pass
        self._modal = alive
        for p in self.pages.values():
            p.mode_changed()

    # -- API used by pages
    def component(self, cid: str) -> dict | None:
        if not self.software:
            return None
        return next((c for c in self.software["components"] if c["id"] == cid), None)

    def goto(self, key: str):
        self.nav[key].setChecked(True)
        self.stack.setCurrentWidget(self.pages[key])
        self.pages[key].on_show()
        if self.state:
            self.pages[key].update_state(self.state)

    def refresh(self):
        self.backend.call("get_state", ok=self._got_state, err=self._state_err)

    def _got_state(self, st):
        self.state = st
        self._record(st["thermal"])
        self._record_clocks(st.get("clocks") or {})
        self._check_charge_reminder(st)
        d = st["device"]
        self.nav["book"].setVisible(d["family"] == "book" or d["has_dtx"])
        cur = self.stack.currentWidget()
        cur.update_state(st)
        self.status.setText("Background service: " + ("running" if self.backend.has_daemon else "not running"))
        self._update_tray()

    def _record(self, th: dict):
        for t in th["temps"]:
            self.history["t:" + t["label"]].append(t["celsius"])
        for f in th["fans"]:
            self.history["f:" + f["label"]].append(f["rpm"])
        for f in th["fan_levels"]:
            self.history["l:" + f["label"]].append(f["level"])
        self.history["thr"].append(th["throttle"]["level"])

    def _record_clocks(self, ck: dict):
        if ck.get("cpu"):
            self.history["c:cpu"].append(ck["cpu"]["avg_mhz"])
        if ck.get("gpu"):
            self.history["c:gpu"].append(ck["gpu"]["mhz"])
        if (ck.get("memory") or {}).get("total_mb"):
            self.history["c:mem"].append(ck["memory"]["used_mb"])

    def _state_err(self, msg):
        self.status.setText(msg)

    def _got_software(self, d):
        self.software = d
        self.distro_name = d["distro"]["name"]
        if self.state:
            self.pages[self._current_key()].update_state(self.state)

    def _current_key(self):
        return next(k for k, p in self.pages.items() if p is self.stack.currentWidget())

    def toast(self, msg: str):
        self.toast_label.setText(msg)
        self.toast_label.show()
        self.toast_timer.start()

    def run_job(self, title, action, args, on_done=None):
        dlg = JobDialog(self, self.backend, title, action, args, on_done)
        dlg.exec()

    def install(self, ids: list[str]):
        if not ids:
            self.toast("Nothing to install.")
            return

        def got(plan):
            if plan["blockers"]:
                confirm(self, "Can't install here", "\n".join(plan["blockers"]), "", "OK")
                return
            steps = "These commands will run as administrator:\n\n" + "\n".join(
                f"• {s['title']}\n    {s['describe']}" for s in plan["steps"])
            if plan["notes"]:
                steps += "\n\n" + "\n".join(plan["notes"])
            if plan["reboot"]:
                steps += "\n\nYou'll need to restart afterwards."
            unsupported = plan["level"] == "unsupported"
            if unsupported:
                ok = confirm_risk(self, "Unsupported system. Installing here is at your own risk",
                                  "This setup is not one linux-surface supports. It may work, but nobody tests it "
                                  "and a problem could leave the device unable to boot normally.",
                                  plan["risks"], steps, "Install anyway")
            else:
                ok = confirm(self, "Review installation", "Install the selected software?", steps, "Install")
            if ok:
                self.run_job("Installing", "install", {"ids": ids, "accept_unsupported": unsupported},
                             lambda ok, m: self.backend.call("get_software", ok=self._software_and_page))
        self.backend.call("plan_install", {"ids": ids}, ok=got, err=self.toast)

    def install_nvidia(self):
        """Plan the NVIDIA driver install, show it, then run it (in a terminal when Secure Boot needs prompts)."""
        def got(info):
            pl = info["plan"]
            if pl["blockers"]:
                confirm(self, "NVIDIA driver", "\n".join(pl["blockers"]), "", "OK")
                return
            detail = "\n".join(f"• {s['title']}\n    {s['describe']}" for s in pl["steps"])
            detail += "\n\n" + "\n".join(pl["notes"]) + "\n\nThings to know:\n" + "\n".join("• " + r for r in pl["risks"])
            where = ("It will open a terminal, because Secure Boot needs you to answer prompts."
                     if pl["interactive"] else "You'll be asked for your administrator password.")
            if not confirm(self, "Install the NVIDIA driver?",
                           "This installs NVIDIA's own graphics driver for the discrete GPU.", detail + "\n\n" + where,
                           "Install"):
                return
            if pl["interactive"]:
                cmd = community.terminal_command(["bash", "-c", nvidia_mod.terminal_script(pl["steps"])])
                if not cmd:
                    notify(self, "No terminal found", "Open a terminal and run:\n\n" + nvidia_mod.terminal_script(pl["steps"]))
                    return
                subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.toast("Opened a terminal. Restart when it says it has finished, then complete the key screen.")
            else:
                self.run_job("Installing NVIDIA driver", "install_nvidia", {}, lambda ok, m: self.refresh())
        self.backend.call("get_nvidia", ok=got, err=self.toast)

    def _software_and_page(self, d):
        self._got_software(d)
        self.pages["software"].render(d)
