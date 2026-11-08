"""First-run setup: scan the device, show what was found, offer to set things up, then a guided tour."""
from __future__ import annotations

import json

from PyQt6.QtCore import QEvent, QPoint, QTimer, Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QScrollArea,
                             QStackedWidget, QVBoxLayout, QWidget)

from . import prefs, theme
from .dialogs import confirm
from .widgets import Card, Pill, Row, button, label

SCAN_STEPS = [
    ("state", "Identifying your Surface", "get_state"),
    ("software", "Checking your system and installed software", "get_software"),
    ("kernel", "Looking for the linux-surface kernel", "get_kernel"),
    ("slow", "Checking touch, pen and cameras", "get_slow_state"),
    ("fixes", "Looking for fixes that apply to your model", "get_fixes"),
    ("updates", "Checking for updates", "check_updates"),
]


class ScanRow(QWidget):
    def __init__(self, text):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        self.mark = QLabel("○")
        self.mark.setFixedWidth(22)
        self.text = QLabel(text)
        self.note = QLabel("")
        self.note.setObjectName("muted")
        lay.addWidget(self.mark)
        lay.addWidget(self.text, 1)
        lay.addWidget(self.note)

    def running(self):
        self.mark.setText("…")
        self.text.setStyleSheet("font-weight: 600;")

    def done(self, ok=True, note=""):
        c = theme.colors()
        self.mark.setText("✓" if ok else "!")
        self.mark.setStyleSheet(f"color: {c['good'] if ok else c['warn']}; font-weight: 700;")
        self.text.setStyleSheet("")
        self.note.setText(note)


def summarize(data: dict) -> dict:
    """Turn raw scan data into what the results page shows. Pure function, easy to test."""
    st, sw, kn = data.get("state") or {}, data.get("software") or {}, data.get("kernel") or {}
    slow, fixes = data.get("slow") or {}, data.get("fixes") or []
    dev = st.get("device", {})
    system = sw.get("system", {})
    ups = data.get("updates") or {"updates": [], "warnings": []}
    # kernel verdict
    if not kn:
        verdict = "Couldn't look up kernel versions."
    elif kn.get("installed") and not kn.get("running_is_surface"):
        verdict = (f"linux-surface {kn['installed']} is installed, but you're running {kn['running']}. Restart and "
                   "choose the surface kernel in the boot menu.")
    elif kn.get("installed") and kn.get("available") and kn["installed"] != kn["available"]:
        verdict = f"Update available: {kn['installed']} → {kn['available']}."
    elif kn.get("installed"):
        verdict = "You have the newest kernel from the repository."
    elif kn.get("available"):
        verdict = f"Not installed. Version {kn['available']} is available from the repository."
    elif not kn.get("repo") and kn.get("latest_release"):
        verdict = (f"Not installed, and this system can't see the repository yet. The newest release is "
                   f"{kn['latest_release']}.")
    else:
        verdict = "No linux-surface kernel found for this system."
    feats = []
    disp = st.get("display", {})
    perf = st.get("performance", {})
    bat = st.get("battery", {})
    th = st.get("thermal", {})
    feats += [("Screen brightness", bool(disp.get("screen"))), ("Keyboard backlight", bool(disp.get("keyboard"))),
              ("Performance profiles", bool(perf.get("available"))),
              ("Battery charge limit", bat.get("charge_limit") is not None),
              ("Fan speed readout", bool(th.get("fans")) or bool(th.get("fan_levels"))),
              ("Touchscreen", bool(slow.get("pen", {}).get("touch_devices"))),
              ("Surface Pen", bool(slow.get("pen", {}).get("pen_devices"))),
              ("Camera", bool(slow.get("camera", {}).get("video_nodes")))]
    if dev.get("family") == "book" or dev.get("has_dtx"):
        feats.append(("Detachable screen", bool(st.get("book", {}).get("dtx"))))
    rec = [f for f in fixes if f["level"] == "recommended" and f["status"] == "available"]
    opt = [f for f in fixes if f["level"] != "recommended" and f["status"] == "available" and f["level"] != "advisory"]
    return {"dev": dev, "system": system, "kernel": kn, "verdict": verdict, "features": feats,
            "recommended_fixes": rec, "optional_fixes": opt, "updates": ups["updates"], "warnings": ups["warnings"],
            "distro": sw.get("distro", {}), "assessment": sw.get("assessment", {"level": "supported", "reasons": []}),
            "components": sw.get("components", []), "battery": bat, "memory_mb": (st.get("clocks") or {}).get("memory", {}).get("total_mb")}


def setup_items(summary: dict) -> list[dict]:
    """What the wizard offers to do. Unsupported systems are never auto-set-up here: they need the full warning."""
    items = []
    s = summary
    asm = s["assessment"]["level"]
    sysd = s["system"]
    fam = s["distro"].get("family")
    can = asm == "supported" and fam in ("debian", "fedora", "arch") and not s["distro"].get("immutable")
    if can and not sysd.get("repo"):
        items.append({"id": "repo", "title": "Set up the linux-surface repository",
                      "why": "Needed to find and update Surface kernels and drivers.",
                      "action": "setup_repo", "args": {}})
    comps = {c["id"]: c for c in s["components"]}
    if can and comps.get("kernel", {}).get("installable") and not comps["kernel"].get("installed") and not s["kernel"].get("installed"):
        ids = [i for i in ("kernel", "iptsd", "libwacom", "secureboot")
               if comps.get(i, {}).get("installable") and not comps[i].get("installed")]
        items.append({"id": "install", "title": "Install the linux-surface kernel and touch/pen support",
                      "why": "Adds drivers for Surface hardware that stock kernels lack.",
                      "action": "install", "args": {"ids": ids, "accept_unsupported": False}})
    if can and s["updates"]:
        items.append({"id": "update", "title": f"Update {len(s['updates'])} Surface package"
                      + ("s" if len(s["updates"]) != 1 else ""),
                      "why": ", ".join(u["name"] for u in s["updates"][:4]) + ("…" if len(s["updates"]) > 4 else ""),
                      "action": "apply_updates", "args": {"pkgs": [u["name"] for u in s["updates"]]}})
    for f in s["recommended_fixes"]:
        if f["scope"] == "system" and f["applicable"]:
            items.append({"id": "fix-" + f["id"], "title": f["title"], "why": f["summary"],
                          "action": "apply_fix", "args": {"id": f["id"]}})
    return items


class WelcomeWizard(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win, self.b = win, win.backend
        self.data: dict = {}
        self.summary: dict = {}
        self.items: list[dict] = []
        self.checks: list[tuple[QCheckBox, dict]] = []
        self.want_tour = False
        self.setWindowTitle("Welcome to Surface Companion")
        self.setMinimumSize(740, 600)
        self.setModal(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 20)
        self.dots = QLabel("")
        self.dots.setObjectName("muted")
        outer.addWidget(self.dots)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.nav = QHBoxLayout()
        self.skip_btn = button("Skip setup", "link", self._skip)
        self.back_btn = button("Back", on_click=lambda: self._go(self.stack.currentIndex() - 1))
        self.next_btn = button("Next", "primary", self._next)
        self.nav.addWidget(self.skip_btn)
        self.nav.addStretch(1)
        self.nav.addWidget(self.back_btn)
        self.nav.addWidget(self.next_btn)
        outer.addLayout(self.nav)
        self._build_welcome()
        self._build_scan()
        self._build_results()
        self._build_setup()
        self._build_done()
        self._go(0)

    # ---------------------------------------------------------------- pages
    def _page(self, title, sub=""):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(12)
        lay.addWidget(label(title, "h1"))
        if sub:
            lay.addWidget(label(sub, "muted"))
        self.stack.addWidget(w)
        return lay

    def _scroll_page(self, title, sub=""):
        lay = self._page(title, sub)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        body = QVBoxLayout(inner)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(12)
        sc.setWidget(inner)
        lay.addWidget(sc, 1)
        return body

    def _build_welcome(self):
        lay = self._page("Welcome", "Surface Companion controls and fixes your Surface on Linux. It works the same on "
                                    "any desktop.")
        c = Card("Let's get you set up")
        for t in ("Scan your device and see exactly what you have",
                  "See which linux-surface kernel is available for it",
                  "Find the fixes that apply to your model",
                  "Take a short tour of the app"):
            c.add(label("•  " + t))
        c.add(label("Nothing is changed during the scan. You choose what to set up afterwards.", "muted"))
        lay.addWidget(c)
        lay.addStretch(1)

    def _build_scan(self):
        lay = self._page("Scanning your Surface", "This takes a few seconds. Checking for updates needs the network.")
        c = Card()
        self.scan_rows: dict[str, ScanRow] = {}
        for key, text, _ in SCAN_STEPS:
            r = ScanRow(text)
            self.scan_rows[key] = r
            c.add(r)
        self.bar = QProgressBar()
        self.bar.setRange(0, len(SCAN_STEPS))
        self.bar.setTextVisible(False)
        c.add(self.bar)
        lay.addWidget(c)
        lay.addStretch(1)

    def _build_results(self):
        self.results_body = self._scroll_page("Here's what we found")

    def _build_setup(self):
        self.setup_body = self._scroll_page("Set up", "Pick what you'd like done now. You can always do any of it later "
                                                      "from the app.")

    def _build_done(self):
        lay = self._page("You're all set")
        self.done_text = label("")
        lay.addWidget(self.done_text)
        c = Card("Take a quick tour?")
        c.add(label("A one-minute walk through each part of the app, including the Simple and Advanced views.", "muted"))
        self.tour_btn = button("Start the tour", "primary", self._finish_with_tour)
        c.add(self.tour_btn)
        lay.addWidget(c)
        lay.addStretch(1)

    # ---------------------------------------------------------------- flow
    def _go(self, i: int):
        i = max(0, min(self.stack.count() - 1, i))
        self.stack.setCurrentIndex(i)
        names = ["Welcome", "Scan", "Results", "Setup", "Done"]
        self.dots.setText("   ›   ".join(f"<b>{n}</b>" if k == i else n for k, n in enumerate(names)))
        self.dots.setTextFormat(Qt.TextFormat.RichText)
        self.back_btn.setVisible(i in (2, 3, 4))
        self.skip_btn.setVisible(i < 4)
        self.next_btn.setEnabled(True)
        self.next_btn.setText({0: "Scan my Surface", 1: "Scanning…", 2: "Next", 3: "Set up selected", 4: "Finish"}[i])
        if i == 1:
            self.next_btn.setEnabled(False)
            self.back_btn.setVisible(False)
            self._start_scan()
        if i == 3:
            self._refresh_setup_button()

    def _next(self):
        i = self.stack.currentIndex()
        if i == 3:
            self._run_selected()
        elif i == 4:
            self._finish()
        else:
            self._go(i + 1)

    def _skip(self):
        if confirm(self, "Skip setup?", "You can run setup again any time from “Setup and tour…” at the bottom left.",
                   "", "Skip"):
            self._finish()

    def _finish(self):
        prefs.put("firstrun_done", True)
        self.accept()

    def _finish_with_tour(self):
        self.want_tour = True
        self._finish()

    def reject(self):
        prefs.put("firstrun_done", True)       # closing the window counts as seen; it can be reopened from the menu
        super().reject()

    # ---------------------------------------------------------------- scan
    def _start_scan(self):
        self.data = {}
        self.bar.setValue(0)
        for r in self.scan_rows.values():
            r.mark.setText("○")
            r.note.setText("")
        self._scan_step(0)

    def _scan_step(self, i: int):
        if i >= len(SCAN_STEPS):
            self._scan_done()
            return
        key, _, action = SCAN_STEPS[i]
        row = self.scan_rows[key]
        row.running()

        def finish(ok, payload=None, note=""):
            if ok and payload is not None:
                self.data[key] = payload
            row.done(ok, note)
            self.bar.setValue(i + 1)
            QTimer.singleShot(120, lambda: self._scan_step(i + 1))   # a beat so each step is visible

        if key == "updates":
            kn = self.data.get("kernel") or {}
            if not kn.get("repo"):
                finish(False, {"updates": [], "warnings": []}, "repository not set up")
                return

            def job_done(ok, msg):
                if ok:
                    res = json.loads(msg)
                    n = len(res["updates"])
                    finish(True, res, f"{n} available" if n else "up to date")
                else:
                    finish(False, {"updates": [], "warnings": [msg]}, "couldn't check")
            self.b.call(action, ok=lambda r: self.b.track(r["job"], lambda l: None, job_done) if isinstance(r, dict) and "job" in r
                        else finish(False, None, "needs the background service"),
                        err=lambda m: finish(False, None, "needs the background service"))
            return
        self.b.call(action, ok=lambda r: finish(True, r, self._note(key, r)), err=lambda m: finish(False, None, "unavailable"))

    @staticmethod
    def _note(key, r):
        if key == "state":
            return r["device"]["model"]
        if key == "kernel":
            return r.get("available") or r.get("latest_release") or ""
        if key == "fixes":
            n = len([f for f in r if f["level"] == "recommended" and f["status"] == "available"])
            return f"{n} recommended" if n else "none needed"
        return ""

    def _scan_done(self):
        self.summary = summarize(self.data)
        self._fill_results()
        self._fill_setup()
        self.next_btn.setEnabled(True)
        QTimer.singleShot(350, lambda: self._go(2))

    # ---------------------------------------------------------------- results
    def _clear(self, lay):
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _fill_results(self):
        s = self.summary
        lay = self.results_body
        self._clear(lay)
        d, sysd = s["dev"], s["system"]
        c = Card("Your device")
        c.add(label(d.get("model") or "Unknown device", "h2"))
        bits = [s["distro"].get("name", ""), f"kernel {d.get('kernel', '')}",
                {"intel": "Intel CPU", "amd": "AMD CPU"}.get(d.get("cpu_vendor"), "")]
        if s.get("memory_mb"):
            bits.append(f"{s['memory_mb'] / 1024:.0f} GB memory")
        c.add(label(" · ".join(b for b in bits if b), "muted"))
        sb = sysd.get("secure_boot", "unknown")
        c.add(Row("Secure Boot", "", Pill(sb.title(), "good" if sb == "enabled" else "muted")))
        if not d.get("is_surface"):
            c.add(label("This doesn't look like a Surface device, so some features will be missing.", "muted"))
        lay.addWidget(c)

        k = Card("Surface kernel")
        kn = s["kernel"]
        k.add(Row("Running now", kn.get("running", ""), Pill("linux-surface" if kn.get("running_is_surface") else "Standard kernel",
                                                             "good" if kn.get("running_is_surface") else "muted")))
        k.add(Row("Installed", "", Pill(kn.get("installed") or "None", "good" if kn.get("installed") else "muted")))
        k.add(Row("Available from the repository", "", Pill(kn.get("available") or ("Repository not set up" if not kn.get("repo") else "Unknown"),
                                                            "accent" if kn.get("available") else "muted")))
        if kn.get("latest_release"):
            k.add(Row("Newest linux-surface release", "", Pill(kn["latest_release"], "accent")))
        k.add(label(s["verdict"]))
        if s["assessment"]["level"] != "supported":
            k.add(label("<b>Note:</b> " + " ".join(s["assessment"]["reasons"][:1]), "muted"))
        lay.addWidget(k)

        f = Card("What your Surface can do here")
        for name, ok in s["features"]:
            f.add(Row(name, "", Pill("Found" if ok else "Not found", "good" if ok else "muted")))
        lay.addWidget(f)

        x = Card("Fixes")
        rec, opt = s["recommended_fixes"], s["optional_fixes"]
        if rec:
            x.add(label(f"{len(rec)} recommended for your model:"))
            for fx in rec:
                x.add(label("•  " + fx["title"]))
        else:
            x.add(label("No recommended fixes for your model.", "muted"))
        if opt:
            x.add(label(f"{len(opt)} more optional fix{'es' if len(opt) != 1 else ''} are in the Advanced view.", "muted"))
        lay.addWidget(x)
        lay.addStretch(1)

    # ---------------------------------------------------------------- setup
    def _fill_setup(self):
        self._clear(self.setup_body)
        self.items = setup_items(self.summary)
        self.checks = []
        if not self.items:
            c = Card("Nothing needs setting up")
            if self.summary["assessment"]["level"] != "supported":
                c.add(label("This system isn't one linux-surface officially supports, so installs aren't offered here. "
                            "You can still use the Software page, which shows a full warning first.", "muted"))
            else:
                c.add(label("Everything looks good. You can change anything later from the app.", "muted"))
            self.setup_body.addWidget(c)
            self.setup_body.addStretch(1)
            return
        for it in self.items:
            cb = QCheckBox(it["title"])
            cb.setChecked(True)
            cb.toggled.connect(self._refresh_setup_button)
            c = Card()
            c.add(cb)
            c.add(label(it["why"], "muted"))
            self.setup_body.addWidget(c)
            self.checks.append((cb, it))
        self.setup_body.addWidget(label("Each step asks for your password once. You'll see exactly what runs.", "muted"))
        self.setup_body.addStretch(1)

    def _refresh_setup_button(self, *_):
        if self.stack.currentIndex() != 3:
            return
        n = sum(1 for cb, _ in self.checks if cb.isChecked())
        self.next_btn.setText(f"Set up {n} selected" if self.checks and n else "Continue")

    def _run_selected(self):
        todo = [it for cb, it in self.checks if cb.isChecked()]
        if not todo:
            self._go(4)
            self._fill_done([])
            return
        if not confirm(self, "Set up now?", "This will:\n\n" + "\n".join("• " + it["title"] for it in todo),
                       "You'll be asked for your administrator password.", "Set up"):
            return
        results = []

        def step(i):
            if i >= len(todo):
                self._fill_done(results)
                self._go(4)
                return
            it = todo[i]
            self.win.run_job(it["title"], it["action"], it["args"], lambda ok, m, it=it: results.append((it, ok, m)))
            if results and not results[-1][1]:
                self._fill_done(results)
                self._go(4)
                return
            step(i + 1)
        step(0)

    def _fill_done(self, results):
        if not results:
            self.done_text.setText("Nothing was changed. You can set things up any time from the app.")
            return
        ok = [r for r in results if r[1]]
        bad = [r for r in results if not r[1]]
        lines = [f"✓ {r[0]['title']}" for r in ok] + [f"✗ {r[0]['title']}: {r[2]}" for r in bad]
        extra = ""
        if any(r[0]["id"] in ("install", "update") for r in ok):
            extra = ("\n\nRestart your Surface to start the new kernel, and choose it in the boot menu if it isn't "
                     "picked automatically.")
        self.done_text.setText("\n".join(lines) + extra)


# ------------------------------------------------------------------------------------------ guided tour

def tour_steps(win) -> list[dict]:
    steps = [
        {"target": "overview", "title": "Overview",
         "text": "Your device at a glance: brightness, performance mode, temperature, battery, and anything that needs "
                 "attention."},
        {"target": "display", "title": "Display and keyboard",
         "text": "Screen brightness, and the keyboard backlight if your model has one."},
        {"target": "performance", "title": "Performance and thermals",
         "text": "Choose how hard your Surface works: quieter and cooler, or faster and hotter. The Advanced view adds "
                 "live temperatures, fan speed and CPU, graphics and memory clock speeds."},
        {"target": "sleep", "title": "Power and sleep",
         "text": "Lid behaviour, sleep, battery health and a charge limit where your model supports it."},
    ]
    if win.nav["book"].isVisible():
        steps.append({"target": "book", "title": "Surface Book",
                      "text": "Detach the screen safely and manage the discrete GPU."})
    steps += [
        {"target": "pen", "title": "Touch and pen",
         "text": "See whether touch and the pen are detected, and restart the touch service if they stop working."},
        {"target": "camera", "title": "Cameras",
         "text": "What camera hardware you have and whether its drivers are in place. Advanced view lists community "
                 "drivers, behind a clear warning."},
        {"target": "fixes", "title": "Fixes",
         "text": "Known workarounds from the linux-surface project, filtered to your model. Every fix can be undone."},
        {"target": "test", "title": "System test",
         "text": "Check that everything works: automatic health checks, plus hands-on tests for the keyboard, mouse, "
                 "speakers, ports, camera and detach. Copy the report for a bug report."},
        {"target": "software", "title": "Software and updates",
         "text": "Install and update the Surface kernel and drivers. New releases show up here."},
        {"target": "@mode", "title": "Simple or Advanced",
         "text": "Simple shows the essentials. Advanced adds history graphs, every sensor, clock speeds, and more fixes. "
                 "Switch here any time."},
        {"target": "@help", "title": "Come back any time",
         "text": "Reopen the setup or this tour from here."},
    ]
    if getattr(win, "tray", None):
        steps.append({"target": "@center", "title": "Tray icon",
                      "text": "The tray icon switches performance modes in one click. Closing the window keeps the app "
                              "running there."})
    return steps


class Tour(QFrame):
    """A callout card that walks through the app, highlighting the sidebar entry it is talking about."""
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setObjectName("tourcard")
        self.setFixedWidth(340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(8)
        self.count = label("", "muted")
        self.title = label("", "h2")
        self.text = label("")
        lay.addWidget(self.count)
        lay.addWidget(self.title)
        lay.addWidget(self.text)
        row = QHBoxLayout()
        self.skip = button("Skip tour", "link", self.end)
        self.back = button("Back", on_click=lambda: self.show_step(self.i - 1))
        self.nxt = button("Next", "primary", lambda: self.show_step(self.i + 1))
        row.addWidget(self.skip)
        row.addStretch(1)
        row.addWidget(self.back)
        row.addWidget(self.nxt)
        lay.addLayout(row)
        self.steps: list[dict] = []
        self.i = 0
        self.hide()

    def start(self):
        self.steps = tour_steps(self.win)
        self.show_step(0)

    def _target_widget(self, key):
        if key == "@mode":
            return self.win.mode_box
        if key == "@help":
            return self.win.help_btn
        return self.win.nav.get(key)

    def _clear_highlight(self):
        for w in list(self.win.nav.values()) + [self.win.mode_box, self.win.help_btn]:
            if w.property("tour"):
                w.setProperty("tour", False)
                w.style().unpolish(w)
                w.style().polish(w)

    def show_step(self, i: int):
        if i >= len(self.steps):
            self.end()
            return
        self.i = max(0, i)
        st = self.steps[self.i]
        self._clear_highlight()
        key = st["target"]
        if key in self.win.pages:
            self.win.goto(key)
        w = self._target_widget(key)
        if w is not None and key != "@center":
            w.setProperty("tour", True)
            w.style().unpolish(w)
            w.style().polish(w)
        self.count.setText(f"{self.i + 1} of {len(self.steps)}")
        self.title.setText(st["title"])
        self.text.setText(st["text"])
        self.back.setEnabled(self.i > 0)
        self.nxt.setText("Done" if self.i == len(self.steps) - 1 else "Next")
        self.adjustSize()
        if w is not None and key != "@center":
            p = w.mapTo(self.win, QPoint(0, 0))
            x = p.x() + w.width() + 14
            y = min(max(8, p.y() - 6), self.win.height() - self.height() - 8)
        else:
            x = (self.win.width() - self.width()) // 2
            y = (self.win.height() - self.height()) // 2
        self.move(x, y)
        self.show()
        self.raise_()

    def end(self):
        self._clear_highlight()
        self.hide()
        prefs.put("tour_done", True)
