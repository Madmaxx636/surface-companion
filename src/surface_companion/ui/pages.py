from __future__ import annotations

import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QScrollArea,
                             QVBoxLayout, QWidget)

from .. import fixes as fixes_mod
from ..fixes.base import Ctx
from ..device import Device
from ..pkg import community, distro as dist
from .dialogs import JobDialog, confirm, confirm_risk, notify
from . import prefs
from .. import handson as hands
from .widgets import (Banner, Card, ChoiceGroup, Pill, Row, SliderRow, Sparkline, Toggle, button, label, separator)


class Page(QWidget):
    key = ""
    title = ""
    subtitle = ""

    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win, self.b = win, win.backend
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        inner = QWidget()
        scroll.setWidget(inner)
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(32, 28, 32, 28)
        self.body.setSpacing(14)
        self.body.addWidget(label(self.title, "h1"))
        if self.subtitle:
            self.body.addWidget(label(self.subtitle, "muted"))
        self.build()
        self.body.addStretch(1)

    def build(self): ...
    def update_state(self, st: dict): ...
    def on_show(self): ...
    def mode_changed(self): ...        # Simple/Advanced was switched; pages with dynamic content refresh here

    def adv(self, w):
        """Mark a widget as Advanced-only (hidden in Simple view)."""
        self.win.register(w, advanced_only=True)
        return w

    def simple(self, w):
        """Mark a widget as Simple-only (hidden in Advanced view)."""
        self.win.register(w, simple_only=True)
        return w

    def act(self, action, args=None, ok=None):
        def done(res):
            self.win.refresh()
            if ok:
                ok(res)
        self.b.call(action, args, ok=done, err=self.win.toast)


# ------------------------------------------------------------------------------------------ thermal panel

def _tone(celsius: float, crit: float | None) -> str:
    hot = (crit - 5) if crit else 95
    warm = (crit - 20) if crit else 80
    return "bad" if celsius >= hot else "warn" if celsius >= warm else "text"


class _Meter(QWidget):
    """label ... value, with a sparkline underneath."""
    def __init__(self, label_text: str, color: str = "accent", span: float = 10):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        top = QHBoxLayout()
        self.name = QLabel(label_text)
        self.value = QLabel("")
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight)
        top.addWidget(self.name, 1)
        top.addWidget(self.value)
        lay.addLayout(top)
        self.spark = Sparkline(span, color)
        lay.addWidget(self.spark)

    def set(self, text: str, history, tone: str = "text"):
        from . import theme
        self.value.setText(text)
        col = theme.colors().get(tone, theme.colors()["text"])
        self.value.setStyleSheet(f"color: {col}; font-weight: 600;")
        self.spark.set_values(history)


class ThermalPanel(QWidget):
    """Temperatures, fan speed and throttling with a few minutes of history."""
    def __init__(self, win, compact: bool = False):
        super().__init__()
        self.win, self.compact = win, compact
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        self.meters: dict[str, _Meter] = {}
        self.note = label("", "muted")
        self.all_btn = button("Show all sensors", on_click=self._toggle_all)
        self.all_text = label("", "muted")
        self.all_text.setVisible(False)
        self._all = False
        if not compact:
            self.lay.addWidget(self.note)
            self.lay.addWidget(self.all_btn, 0, Qt.AlignmentFlag.AlignLeft)
            self.lay.addWidget(self.all_text)

    def _toggle_all(self):
        self._all = not self._all
        self.all_text.setVisible(self._all)
        self.all_btn.setText("Hide all sensors" if self._all else "Show all sensors")

    def _meter(self, key: str, title: str, color: str, span: float) -> _Meter:
        m = self.meters.get(key)
        if m is None:
            m = self.meters[key] = _Meter(title, color, span)
            self.lay.insertWidget(len(self.meters) - 1, m)
        return m

    def update_state(self, th: dict):
        h = self.win.history
        live = set()
        for t in th["temps"]:
            key = "t:" + t["label"]
            live.add(key)
            self._meter(key, t["label"], "accent", 12).set(f"{t['celsius']:.0f} °C", h.get(key, ()),
                                                           _tone(t["celsius"], t.get("crit")))
        for f in th["fans"]:
            key = "f:" + f["label"]
            live.add(key)
            self._meter(key, f["label"] + " speed", "good", 800).set(f"{f['rpm']} RPM", h.get(key, ()))
        for f in th["fan_levels"]:
            key = "l:" + f["label"]
            live.add(key)
            self._meter(key, "Fan level", "good", 2).set(f"{f['level']} of {f['max']}", h.get(key, ()))
        thr = th["throttle"]
        live.add("thr")
        self._meter("thr", "CPU thermal throttling", "warn", 2).set(
            "None" if thr["level"] == 0 else f"Level {thr['level']} of {thr['max']}", h.get("thr", ()),
            "warn" if thr["level"] else "good")
        for k in [k for k in self.meters if k not in live]:
            self.meters.pop(k).deleteLater()
        if not self.compact:
            if th["fans"]:
                self.note.setText("Fan speed comes from the kernel's hardware monitor.")
            elif th["fan_levels"]:
                self.note.setText("This device reports a fan level, not RPM.")
            else:
                self.note.setText("This device doesn't report fan speed to Linux, so there is nothing to show. "
                                  "Fans are run by the embedded controller; the performance profile changes how "
                                  "hard it cools.")
            self.all_text.setText("\n".join(f"{s['label']}: {s['celsius']:.0f} °C" for s in th["all_sensors"]))
            self.all_btn.setVisible(bool(th["all_sensors"]))


class ClocksPanel(QWidget):
    """CPU, graphics and memory clock speeds with history, plus memory use."""
    def __init__(self, win, compact: bool = False):
        super().__init__()
        self.win, self.compact = win, compact
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.cpu = _Meter("CPU clock", "accent", 800)
        self.gpu = _Meter("Graphics clock", "good", 200)
        self.mem = _Meter("Memory in use", "warn", 1000)
        for m in (self.cpu, self.gpu, self.mem):
            lay.addWidget(m)
        self.detail = label("", "muted")
        lay.addWidget(self.detail)

    @staticmethod
    def _ghz(mhz: float) -> str:
        return f"{mhz / 1000:.2f} GHz" if mhz >= 1000 else f"{mhz:.0f} MHz"

    def update_state(self, ck: dict):
        h = self.win.history
        c, g, m = ck.get("cpu"), ck.get("gpu"), ck.get("memory") or {}
        self.cpu.setVisible(bool(c))
        if c:
            self.cpu.set(self._ghz(c["avg_mhz"]), h.get("c:cpu", ()))
        self.gpu.setVisible(bool(g))
        if g:
            self.gpu.set(f"{g['mhz']} MHz", h.get("c:gpu", ()))
        self.mem.setVisible(bool(m.get("total_mb")))
        if m.get("total_mb"):
            self.mem.set(f"{m['used_mb'] / 1024:.1f} of {m['total_mb'] / 1024:.1f} GB", h.get("c:mem", ()))
        lines = []
        if c:
            lines.append(f"CPU: busiest core {self._ghz(c['max_mhz'])}, slowest {self._ghz(c['min_mhz'])}"
                         + (f", limit {self._ghz(c['cap_mhz'])}" if c.get("cap_mhz") else "")
                         + (f" · governor {c['governor']}" if c.get("governor") else "")
                         + (f" · bias {c['epp']}" if c.get("epp") else ""))
        if g and g.get("max_mhz"):
            lines.append(f"Graphics: up to {g['max_mhz']} MHz")
        sp = m.get("speed")
        if sp:
            kind = (sp.get("type") or "Memory")
            lines.append(f"Memory: {kind} at {sp['configured_mts']} MT/s (the speed the firmware set at boot; Linux "
                         "can't read a live memory clock)")
        elif m.get("total_mb"):
            lines.append("Memory speed needs the background service to read.")
        if m.get("swap_total_mb"):
            lines.append(f"Swap: {m['swap_used_mb']} of {m['swap_total_mb']} MB used")
        self.detail.setText("\n".join(lines))


def _simple_thermal_line(th: dict) -> str:
    cpu = next((t for t in th["temps"] if t["label"] == "CPU"), None) or (th["temps"][0] if th["temps"] else None)
    bits = []
    if cpu:
        state = "hot" if cpu["celsius"] >= 85 else "warm" if cpu["celsius"] >= 70 else "cool"
        bits.append(f"{cpu['label']} {cpu['celsius']:.0f} °C ({state})")
    if th["fans"]:
        bits.append(f"fan {th['fans'][0]['rpm']} RPM")
    if th["throttle"]["level"]:
        bits.append("slowed down by heat")
    return " · ".join(bits) or "No temperature sensors found."


# ------------------------------------------------------------------------------------------ overview

class OverviewPage(Page):
    key, title = "overview", "Overview"

    def build(self):
        self.banners = QVBoxLayout()
        self.body.addLayout(self.banners)
        self.device_card = Card()
        self.model = label("", "h1")
        self.sub = label("", "muted")
        self.device_card.add(self.model)
        self.device_card.add(self.sub)
        self.body.addWidget(self.device_card)

        q = Card("Quick controls")
        self.bright = SliderRow(1, 100)
        self.bright.committed.connect(lambda v: self.act("set_brightness", {"percent": v}))
        q.add(Row("Screen brightness", "", self.bright))
        q.add(separator())
        self.profile_name = label("", "")
        self.profile_btn = button("Change", on_click=lambda: self.win.goto("performance"))
        self.profile_row = Row("Performance", "", self.profile_btn)
        q.add(self.profile_row)
        self.body.addWidget(q)

        self.simple_temp = self.simple(Card("Temperature"))
        self.simple_temp_text = label("")
        self.simple_temp.add(self.simple_temp_text)
        self.body.addWidget(self.simple_temp)

        self.therm_card = self.adv(Card("Temperature and fan"))
        self.therm = ThermalPanel(self.win, compact=True)
        self.therm_card.add(self.therm)
        self.therm_card.add(button("Details", on_click=lambda: self.win.goto("performance")))
        self.body.addWidget(self.therm_card)

        self.clocks_card = self.adv(Card("Clock speeds"))
        self.clocks = ClocksPanel(self.win, compact=True)
        self.clocks_card.add(self.clocks)
        self.body.addWidget(self.clocks_card)

        self.bat_card = Card("Battery")
        self.bat_text = label("")
        self.bat_card.add(self.bat_text)
        self.body.addWidget(self.bat_card)

        self.attention = Card("Needs attention")
        self.attention_list = QVBoxLayout()
        self.attention.add_layout(self.attention_list)
        self.body.addWidget(self.attention)
        self._fixes_counts = None
        self._updates = None

    def on_show(self):
        self.b.call("get_fixes", ok=self._got_fixes, err=lambda m: None)

    def _got_fixes(self, fx):
        self._fixes_counts = [f for f in fx if f["level"] == "recommended" and f["status"] == "available"]
        self._render_attention()

    def update_state(self, st):
        d = st["device"]
        self.model.setText(d["model"] or "Unknown device")
        distro = self.win.distro_name
        self.sub.setText(" · ".join(x for x in (distro, f"kernel {d['kernel']}") if x))
        disp = st["display"]["screen"]
        self.bright.setEnabled(bool(disp) and not self.win.brightness_system)
        if disp:
            self.bright.set_value(disp["percent"])
        self.therm.update_state(st["thermal"])
        self.clocks.update_state(st.get("clocks") or {})
        self.simple_temp_text.setText(_simple_thermal_line(st["thermal"]))
        perf = st["performance"]
        if perf.get("available"):
            cur = next((c["label"] for c in perf["choices"] if c["id"] == perf["current"]), perf["current"])
            ov = (st.get("overrides") or {}).get("profile", False)
            cfg = st.get("auto_profile_config")
            names = {c["id"]: c["label"] for c in perf["choices"]}
            self.profile_row.set_desc(
                f"Automatic: {names.get(cfg['battery'], 'System')} on battery, {names.get(cfg['ac'], 'System')} plugged in"
                if cfg and (st.get("overrides") or {}).get("auto_profile") else cur if ov else f"System ({cur})")
        else:
            self.profile_row.set_desc("Not available on this kernel")
        bat = st["battery"]
        self.bat_card.setVisible(bat.get("present", False))
        if bat.get("present"):
            parts = [f"{bat['percent']}% · {bat['status']}"]
            if bat.get("health_percent"):
                parts.append(f"Capacity is {bat['health_percent']}% of new")
            if bat.get("cycles"):
                parts.append(f"{bat['cycles']} charge cycles")
            self.bat_text.setText("\n".join(parts))
        # banners
        while self.banners.count():
            w = self.banners.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not d["is_surface"]:
            self.banners.addWidget(Banner("This does not look like a Surface device. Controls that need Surface "
                                          "hardware will be missing.", "warn"))
        if not self.win.backend.has_daemon:
            self.banners.addWidget(Banner(
                "The background service isn't running, so this window is read-only. Start it with "
                "“sudo systemctl start surface-companion”, or run the installer again.", "warn"))
        kernel = self.win.component("kernel")
        if kernel and kernel.get("installed") and not d["surface_kernel"]:
            self.banners.addWidget(Banner(
                "The linux-surface kernel is installed but you're running a different kernel. Pick the surface "
                "kernel in your boot menu (or set it as default) after a restart.", "warn"))
        self._render_attention()

    def _render_attention(self):
        while self.attention_list.count():
            w = self.attention_list.takeAt(0).widget()
            if w:
                w.deleteLater()
        items = []
        if self._fixes_counts:
            items.append((f"{len(self._fixes_counts)} recommended fix{'es' if len(self._fixes_counts) != 1 else ''} "
                          "for your model", "fixes"))
        sw = self.win.software
        if sw and not sw["system"]["repo"] and any(c["installed"] for c in sw["components"]) \
                and sw["assessment"]["level"] != "blocked" and sw["distro"]["family"] in ("debian", "fedora", "arch"):
            items.append(("The Surface update repository isn't set up, so new kernels and drivers won't be found",
                          "software"))
        if self.win.updates:
            items.append((f"{len(self.win.updates)} Surface update{'s' if len(self.win.updates) != 1 else ''} "
                          "available", "software"))
        if not items:
            self.attention_list.addWidget(label("Everything looks good.", "muted"))
        for text, page in items:
            r = QHBoxLayout()
            w = QWidget()
            w.setLayout(r)
            r.setContentsMargins(0, 0, 0, 0)
            r.addWidget(label(text), 1)
            r.addWidget(button("Review", on_click=lambda p=page: self.win.goto(p)))
            self.attention_list.addWidget(w)


# ------------------------------------------------------------------------------------------ display

class DisplayPage(Page):
    key, title = "display", "Display & Keyboard"
    subtitle = "Brightness for the screen and the keyboard backlight."
    TIMEOUTS = [("system", "System (never)"), (5, "5 seconds"), (10, "10 seconds"), (15, "15 seconds"),
                (30, "30 seconds"), (60, "1 minute"), (120, "2 minutes"), (300, "5 minutes")]

    def build(self):
        c = Card()
        self.bright_system = Toggle()
        self.bright_system.clicked.connect(lambda on: self.win.set_brightness_system(on))
        c.add(Row("Let the system control brightness",
                  "On: this app never changes the screen brightness; your desktop and the firmware do. "
                  "Off: use the slider below.", self.bright_system))
        c.add(separator())
        self.bright = SliderRow(1, 100)
        self.bright.committed.connect(lambda v: self.act("set_brightness", {"percent": v}))
        self.bright_row = Row("Screen brightness", "", self.bright)
        c.add(self.bright_row)
        self.body.addWidget(c)
        self.kbd_card = Card()
        self.kbd = SliderRow(0, 3, "")
        self.kbd.committed.connect(lambda v: self.act("set_keyboard", {"level": v}))
        self.kbd_card.add(Row("Keyboard backlight", "Off to brightest.", self.kbd))
        self.kbd_card.add(separator())
        self.kbd_timeout = QComboBox()
        for val, text in self.TIMEOUTS:
            self.kbd_timeout.addItem(text, val)
        self.kbd_timeout.activated.connect(lambda i: self.act("set_kbd_timeout", {"seconds": self.kbd_timeout.itemData(i)}))
        self.kbd_card.add(Row("Turn off after no activity",
                              "System: the app doesn't touch it. When set, the background service notes when any key, "
                              "touch or mouse event happens so it knows you're away. It does not record what you type.",
                              self.kbd_timeout))
        self.body.addWidget(self.kbd_card)
        self.no_kbd = label("This model has no keyboard backlight that Linux can control.", "muted")
        self.body.addWidget(self.no_kbd)

    def update_state(self, st):
        s, k = st["display"]["screen"], st["display"]["keyboard"]
        system = self.win.brightness_system
        self.bright_system.set_silently(system)
        self.bright_row.setEnabled(bool(s) and not system)
        if s:
            self.bright.set_value(s["percent"])
            self.bright_row.set_desc("Controlled by the system." if system else "")
        else:
            self.bright_row.set_desc("No controllable backlight found.")
        self.kbd_card.setVisible(bool(k))
        self.no_kbd.setVisible(not k)
        if k:
            self.kbd.slider.setRange(0, k["max"])
            self.kbd.set_value(k["value"])
            secs = (st.get("overrides") or {}).get("kbd_timeout") or "system"
            i = self.kbd_timeout.findData(secs)
            if i < 0:
                self.kbd_timeout.addItem(f"{secs} seconds", secs)
                i = self.kbd_timeout.count() - 1
            self.kbd_timeout.blockSignals(True)
            self.kbd_timeout.setCurrentIndex(i)
            self.kbd_timeout.blockSignals(False)


# ------------------------------------------------------------------------------------------ performance

class PerformancePage(Page):
    key, title = "performance", "Performance & Thermals"
    subtitle = "Choose how hard the device works. This is the same setting as the Surface power slider on Windows."

    def build(self):
        self.profiles = ChoiceGroup()
        self.profiles.chosen.connect(self._chosen)
        self.unavailable = label("This kernel doesn't expose a performance profile. The linux-surface kernel does.",
                                 "muted")
        self.body.addWidget(self.profiles)
        self.auto_card = Card("Automatic: one profile per power source")
        self.auto_batt, self.auto_ac = QComboBox(), QComboBox()
        for cb, key in ((self.auto_batt, "battery"), (self.auto_ac, "ac")):
            cb.activated.connect(lambda _i: self._auto_changed())
        self.auto_card.add(Row("On battery", "", self.auto_batt))
        self.auto_card.add(Row("Plugged in", "", self.auto_ac))
        self.auto_card.add(label("“System” leaves the profile alone for that power source.", "muted"))
        self.auto_card.hide()
        self.body.addWidget(self.auto_card)
        self.body.addWidget(self.unavailable)
        self.simple_temp = self.simple(Card("Temperature"))
        self.simple_temp_text = label("")
        self.simple_temp.add(self.simple_temp_text)
        self.body.addWidget(self.simple_temp)
        self.therm_card = self.adv(Card("Temperatures and fan"))
        self.therm = ThermalPanel(self.win)
        self.therm_card.add(self.therm)
        self.body.addWidget(self.therm_card)
        self.clocks_card = self.adv(Card("Clock speeds"))
        self.clocks = ClocksPanel(self.win)
        self.clocks_card.add(self.clocks)
        self.body.addWidget(self.clocks_card)
        self.thermald = self.adv(Card("Thermal daemon"))
        self.thermald_text = label("")
        self.thermald.add(self.thermald_text)
        self.thermald.add(button("See thermal fixes", on_click=lambda: self.win.goto("fixes")))
        self.body.addWidget(self.thermald)

    def _chosen(self, cid: str):
        if cid == "auto":
            ids = [c["id"] for c in (self._perf or {}).get("choices", [])]
            batt = "low-power" if "low-power" in ids else "balanced" if "balanced" in ids else "system"
            ac = "balanced" if "balanced" in ids else "system"
            self.act("set_auto_profile", {"battery": batt, "ac": ac})
        else:
            self.act("set_profile", {"profile": cid})

    def _auto_changed(self):
        self.act("set_auto_profile", {"battery": self.auto_batt.currentData(), "ac": self.auto_ac.currentData()})

    def update_state(self, st):
        self._perf = st["performance"]
        perf, th = st["performance"], st["thermal"]
        ok = perf.get("available")
        self.profiles.setVisible(bool(ok))
        self.unavailable.setVisible(not ok)
        if ok:
            ovs = st.get("overrides") or {}
            ov, auto = ovs.get("profile", False), ovs.get("auto_profile", False)
            cur_label = next((c["label"] for c in perf["choices"] if c["id"] == perf["current"]), perf["current"])
            self.profiles.set_choices([{"id": "system", "label": "System",
                                        "hint": "Let your system decide (desktop power settings, firmware)."},
                                       {"id": "auto", "label": "Automatic",
                                        "hint": "A different profile on battery and when plugged in."}]
                                      + perf["choices"])
            self.profiles.set_hint("system", f"Let your system decide (desktop power settings, firmware). "
                                              f"Right now: {cur_label}.")
            self.profiles.set_current("auto" if auto else perf["current"] if ov else "system")
            cfg = st.get("auto_profile_config")
            self.auto_card.setVisible(bool(auto and cfg))
            if auto and cfg:
                opts = [("system", "System")] + [(c["id"], c["label"]) for c in perf["choices"]]
                for cb, key in ((self.auto_batt, "battery"), (self.auto_ac, "ac")):
                    if [cb.itemData(i) for i in range(cb.count())] != [o[0] for o in opts]:
                        cb.clear()
                        for oid, text in opts:
                            cb.addItem(text, oid)
                    if not cb.view().isVisible():
                        cb.blockSignals(True)
                        cb.setCurrentIndex(max(0, cb.findData(cfg[key])))
                        cb.blockSignals(False)
        self.therm.update_state(th)
        self.clocks.update_state(st.get("clocks") or {})
        self.simple_temp_text.setText(_simple_thermal_line(th))
        td = th["thermald"]
        self.thermald_text.setText(
            "thermald is running." if td["active"] else
            "thermald is installed but not running." if td["installed"] else
            "thermald is not installed. It lets the CPU throttle gently before the hardware throttles hard.")


# ------------------------------------------------------------------------------------------ power & sleep

LID = [("suspend", "Sleep"), ("ignore", "Do nothing"), ("lock", "Lock the screen"), ("hibernate", "Hibernate"),
       ("poweroff", "Shut down")]
SLEEP_NAMES = {"s2idle": "Modern standby (recommended)", "deep": "Deep sleep (S3)", "shallow": "Shallow",
               "platform": "Platform"}


class PowerPage(Page):
    key, title = "sleep", "Power & Sleep"
    subtitle = "Sleep, wake and battery."

    def build(self):
        c = Card("Sleep")
        self.sleep_system = Toggle()
        self.sleep_system.clicked.connect(self._sleep_master)
        c.add(Row("Let the system manage sleep",
                  "On: this app doesn't change the sleep mode, lid behaviour or lid wake, and gives back anything it "
                  "changed. Off: choose them below.", self.sleep_system))
        c.add(separator())
        self.mode = QComboBox()
        self.mode.activated.connect(lambda i: self.act("set_mem_sleep", {"mem_sleep": self.mode.itemData(i)}))
        self.mode_row = Row("Sleep mode", "", self.mode)
        c.add(self.adv(self.mode_row))
        c.add(self.adv(separator()))
        self.lid = QComboBox()
        self.lid.addItem("System", "default")
        for k, v in LID:
            self.lid.addItem(v, k)
        self.lid.activated.connect(lambda i: self.act("set_lid_action", {"action": self.lid.itemData(i)}))
        self.lid_row = Row("When the lid closes", "Applies on battery and on power.", self.lid)
        c.add(self.lid_row)
        c.add(separator())
        self.lidwake = Toggle()
        self.lidwake.clicked.connect(lambda on: self.act("set_lid_wake", {"enabled": on}))
        self.lidwake_row = Row("Wake when the lid opens", "", self.lidwake)
        c.add(self.lidwake_row)
        c.add(separator())
        self.s0ix = label("", "muted")
        c.add(self.adv(self.s0ix))
        row = QHBoxLayout()
        row.addWidget(button("Sleep now", on_click=lambda: self.act("sleep_now", {"kind": "suspend"})))
        self.hib = button("Hibernate", on_click=lambda: self.act("sleep_now", {"kind": "hibernate"}))
        row.addWidget(self.hib)
        row.addStretch(1)
        c.add_layout(row)
        self.body.addWidget(c)

        self.bat = Card("Battery")
        self.bat_text = label("")
        self.bat.add(self.bat_text)
        self.limit_on = Toggle()
        self.limit_on.clicked.connect(self._limit_toggled)
        self.limit_toggle_row = Row("Limit charging", "Off: the system decides (charges to 100%). On: stop at the level "
                                                      "below, which makes the battery last longer.", self.limit_on)
        self.bat.add(self.limit_toggle_row)
        self.limit = SliderRow(50, 100)
        self.limit.committed.connect(lambda v: self.act("set_charge_limit", {"charge_limit": v}))
        self.limit_row = Row("Charge limit", "", self.limit)
        self.bat.add(self.limit_row)
        # Where the kernel offers no charge limit, a reminder is the honest alternative
        self.no_limit = label("This device's battery driver doesn't offer a charge limit to Linux, so nothing here can "
                              "stop charging. The app can remind you to unplug instead.", "muted")
        self.bat.add(self.no_limit)
        self.remind_on = Toggle()
        self.remind_on.clicked.connect(lambda on: prefs.put("reminder_on", on))
        self.remind_toggle_row = Row("Remind me to unplug", "A notification when charging reaches the level below. "
                                                            "Needs the app running (it stays in the tray).", self.remind_on)
        self.bat.add(self.remind_toggle_row)
        self.remind = SliderRow(50, 100)
        self.remind.set_value(int(prefs.get("reminder_pct", 80)))
        self.remind.committed.connect(lambda v: prefs.put("reminder_pct", v))
        self.remind_row = Row("Remind at", "", self.remind)
        self.bat.add(self.remind_row)
        self.body.addWidget(self.bat)

    def _sleep_master(self, on: bool):
        prefs.put("sleep_manual", not on)
        if on:
            self.act("sleep_system")
        else:
            self.win.refresh()

    def _limit_toggled(self, on: bool):
        if on:
            self.act("set_charge_limit", {"charge_limit": 80})
        else:
            self.act("set_charge_limit", {"charge_limit": "system"})

    def update_state(self, st):
        sl = st["sleep"]
        modes = sl["mem_sleep"]["modes"]
        ov = (st.get("overrides") or {})
        self.mode.blockSignals(True)
        want = ["system"] + modes
        if [self.mode.itemData(i) for i in range(self.mode.count())] != want:
            self.mode.clear()
            self.mode.addItem("System", "system")
            for m in modes:
                self.mode.addItem(SLEEP_NAMES.get(m, m), m)
        cur = sl["mem_sleep"]["current"]
        self.mode.setCurrentIndex(self.mode.findData(cur) if ov.get("mem_sleep") and cur in modes else 0)
        self.mode.blockSignals(False)
        manual = prefs.get("sleep_manual", None)
        if manual is None:       # first time: manual if the app is already overriding anything
            manual = bool(ov.get("mem_sleep") or ov.get("lid_wake") or sl["lid_action"] != "default")
        manual = bool(manual) if not isinstance(manual, str) else manual.lower() == "true"
        self.sleep_system.set_silently(not manual)
        self.lid_row.setEnabled(manual)
        self.mode_row.setEnabled(len(modes) > 1 and manual)
        self.mode_row.set_desc("Only one mode is available on this device." if len(modes) <= 1 else
                               "System leaves it to the kernel. A choice here is re-applied after every wake. Switching "
                               "back to System takes full effect after a restart.")
        i = self.lid.findData(sl["lid_action"])
        if i >= 0 and not self.lid.view().isVisible():
            self.lid.setCurrentIndex(i)
        self.lidwake_row.setEnabled(sl["lid_wake"] is not None and manual)
        if sl["lid_wake"] is not None:
            self.lidwake.set_silently(sl["lid_wake"])
        else:
            self.lidwake_row.set_desc("No lid wake control on this device.")
        r = sl.get("s0ix_residency_us")
        self.s0ix.setText(f"Time spent in low-power idle since boot: {r / 1e6 / 60:.0f} min. If this stays at 0 "
                          "after sleeping, the device isn't reaching its deepest sleep state." if r is not None else
                          "Low-power idle statistics aren't available.")
        self.hib.setEnabled(sl["can_hibernate"])
        b = st["battery"]
        self.bat.setVisible(b.get("present", False))
        if b.get("present"):
            lines = [f"{b['percent']}% · {b['status']}"]
            if b.get("health_percent"):
                lines.append(f"Health: {b['health_percent']}% of original capacity")
            self.bat_text.setText("\n".join(lines))
            lim = b.get("charge_limit")
            on = bool((st.get("overrides") or {}).get("charge_limit", False))
            self.limit_toggle_row.setVisible(lim is not None)
            self.limit_row.setVisible(lim is not None and on)
            for w in (self.no_limit, self.remind_toggle_row, self.remind_row):
                w.setVisible(lim is None)
            if lim is not None:
                self.limit_on.set_silently(on)
                self.limit.set_value(lim)
            else:
                self.remind_on.set_silently(bool(prefs.get("reminder_on", False)))


# ------------------------------------------------------------------------------------------ surface book

class BookPage(Page):
    key, title = "book", "Surface Book"
    subtitle = "Detach the screen and manage the discrete GPU."

    def build(self):
        c = Card("Screen")
        self.mode = label("", "big")
        c.add(self.mode)
        self.detail = label("", "muted")
        c.add(self.detail)
        self.detach_btn = button("Detach screen", "primary", self.detach)
        row = QHBoxLayout()
        row.addWidget(self.detach_btn)
        row.addStretch(1)
        c.add_layout(row)
        self.hint = label("Close apps using the keyboard base (and eject USB drives plugged into it) first. "
                          "The screen releases a few seconds after you confirm.", "muted")
        c.add(self.hint)
        self.body.addWidget(c)
        self.gpu = Card("Discrete GPU")
        self.gpu_text = label("")
        self.gpu.add(self.gpu_text)
        self.gpu_on = Toggle()
        self.gpu.add(Row("Keep the GPU powered", "Off: the system decides and lets it sleep when idle, which saves battery. "
                         "Turn on if a game or compute job stutters.", self.gpu_on))
        self.gpu_on.clicked.connect(lambda on: self.act("set_dgpu_power", {"dgpu_always_on": on}))
        self.nv_btn = button("Install NVIDIA driver…", "primary", lambda: self.win.install_nvidia())
        self.nv_row = Row("NVIDIA driver", "", self.nv_btn)
        self.gpu.add(self.nv_row)
        self.body.addWidget(self.gpu)

    def detach(self):
        if confirm(self, "Detach the screen?", "Save your work and eject anything plugged into the keyboard base.",
                   "The screen unlocks a few seconds after you confirm.", "Detach", danger=True):
            self.act("detach_clipboard", ok=lambda _: self.win.toast("Request sent. Wait for the latch to release."))

    def update_state(self, st):
        bk = st["book"]
        if bk.get("dtx"):
            names = {"laptop": "Laptop", "tablet": "Tablet", "studio": "Studio"}
            self.mode.setText(names.get(bk.get("mode"), "Unknown"))
            self.detail.setText(f"Latch {bk.get('latch') or 'unknown'}")
            self.detach_btn.setEnabled(bk.get("mode") != "tablet")
        else:
            self.mode.setText("Detach unavailable")
            self.detail.setText("The detach device /dev/surface/dtx was not found. Boot the linux-surface kernel.")
            self.detach_btn.setEnabled(False)
        g = bk.get("dgpu")
        self.gpu.setVisible(bool(g))
        if g:
            self.gpu_text.setText(f"{g.get('driver') or 'No driver'} · power state {g.get('power_state') or '?'} "
                                  f"({g.get('runtime_status') or '?'})")
            self.gpu_on.set_silently(g.get("power_control") == "on")
            drv = g.get("driver")
            self.nv_row.setVisible(drv != "nvidia")
            self.nv_row.set_desc("Using the open 'nouveau' driver: fine for the desktop, slow for games and no CUDA. "
                                 "NVIDIA's own driver gives full speed." if drv == "nouveau" else
                                 "No driver is loaded for the GPU yet." if not drv else f"Using the {drv} driver.")


# ------------------------------------------------------------------------------------------ touch & pen

class PenPage(Page):
    key, title = "pen", "Touch & Pen"
    subtitle = "Touchscreen and Surface Pen input."

    def build(self):
        self.status = Card("Status")
        self.rows = QVBoxLayout()
        self.status.add_layout(self.rows)
        self.body.addWidget(self.status)
        self.help = Card("Fix problems")
        self.help.add(label("If touch or the pen stops responding, restarting the touch service usually brings it "
                            "back.", "muted"))
        r = QHBoxLayout()
        self.restart = button("Restart touch service", on_click=lambda: self.act(
            "restart_touch", ok=lambda _: self.win.toast("Touch service restarted")))
        self.install = button("Install touch support", "primary", lambda: self.win.install(["iptsd", "libwacom"]))
        r.addWidget(self.restart)
        r.addWidget(self.install)
        r.addStretch(1)
        self.help.add_layout(r)
        self.help.add(label("The Surface Pen's back button can be remapped with the community tool "
                            "<a href='https://github.com/qzed/linux-surface-pen-button-remap'>"
                            "linux-surface-pen-button-remap</a>.", "muted")).setOpenExternalLinks(True)
        self.body.addWidget(self.help)

    def on_show(self):
        self.b.call("get_slow_state", ok=self._got, err=self.win.toast)

    def _got(self, s):
        p = s["pen"]
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()

        def add(title, pill):
            self.rows.addWidget(Row(title, "", pill))
        add("Touchscreen / touchpad", Pill(", ".join(p["touch_devices"]) or "Not detected",
                                          "good" if p["touch_devices"] else "muted"))
        add("Surface Pen", Pill(", ".join(p["pen_devices"]) or "Not detected", "good" if p["pen_devices"] else "muted"))
        add("Touch service (iptsd)", Pill("Running" if p["iptsd_active"] else "Installed" if p["iptsd_installed"]
                                          else "Not installed", "good" if p["iptsd_active"] else "warn"))
        add("Screen rotation metadata (libwacom-surface)", Pill("Installed" if p["libwacom_surface"] else "Not installed",
                                                                 "good" if p["libwacom_surface"] else "muted"))
        self.restart.setEnabled(bool(p["iptsd_units"]))
        self.install.setVisible(not p["iptsd_installed"])


# ------------------------------------------------------------------------------------------ cameras

WIKI_STATUS = {"ok": ("Works", "good"), "partial": ("Partly works", "warn"), "none": ("Not supported yet", "bad"),
               "unknown": ("Unknown", "muted")}


class CameraPage(Page):
    key, title = "camera", "Cameras"
    subtitle = "Camera drivers and the software that goes with them."

    def build(self):
        self.status = Card("This device")
        self.rows = QVBoxLayout()
        self.status.add_layout(self.rows)
        self.body.addWidget(self.status)
        c = Card("Set up")
        c.add(label("Cameras behind Intel's image processor need extra userspace software (libcamera) and "
                    "firmware. Ordinary USB (UVC) webcams work without it.", "muted"))
        r = QHBoxLayout()
        r.addWidget(button("Install camera support", "primary", lambda: self.win.install(["camera"])))
        r.addWidget(button("Check camera fixes", on_click=lambda: self.win.goto("fixes")))
        r.addStretch(1)
        c.add_layout(r)
        self.body.addWidget(c)
        self.community_host = self.adv(QWidget())
        self.community_box = QVBoxLayout(self.community_host)
        self.community_box.setContentsMargins(0, 0, 0, 0)
        self.community_box.setSpacing(12)
        self.body.addWidget(self.community_host)

    def on_show(self):
        self.b.call("get_slow_state", ok=self._got, err=self.win.toast)
        self.b.call("get_community", ok=self._got_community, err=lambda m: None)

    # ---- community drivers
    def _got_community(self, drivers):
        while self.community_box.count():
            w = self.community_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not drivers:
            return
        hdr = Card("Community drivers")
        hdr.add(label("These are unofficial drivers made by individuals for your exact model. linux-surface has not "
                      "reviewed them. You can install them from here, after a clear warning.", "muted"))
        self.community_box.addWidget(hdr)
        for d in drivers:
            c = Card()
            head = QHBoxLayout()
            head.addWidget(label(d["title"], "h2"), 1)
            head.addWidget(Pill("Installed" if d["installed"] else "Unofficial", "good" if d["installed"] else "bad"))
            c.add_layout(head)
            c.add(label(d["summary"]))
            if d["not_included"]:
                c.add(label(d["not_included"], "muted"))
            c.add(label(f"Licence: {d['license']} · Language: {d['language']} · Pinned to commit {d['commit'][:10]}",
                        "muted"))
            row = QHBoxLayout()
            row.addWidget(button("Install…", "danger", lambda dd=d: self.install_community(dd)))
            if d["installed"]:
                row.addWidget(button("Remove", on_click=lambda dd=d: self.remove_community(dd)))
            lk = QLabel(f"<a href='{d['url']}'>Read the source</a>")
            lk.setOpenExternalLinks(True)
            row.addStretch(1)
            row.addWidget(lk)
            c.add_layout(row)
            self.community_box.addWidget(c)

    def install_community(self, d):
        details = (f"Source: {d['url']}\nPinned commit: {d['commit']}\n"
                   + ("Runs the author's installer in a terminal as you; it will ask for your password there."
                      if d["mode"] == "terminal" else "Builds kernel modules (DKMS) as administrator from that commit."))
        intro = "This is a community project, not part of linux-surface. " + (d["not_included"] or "")
        if not confirm_risk(self, "Unofficial driver. Installing it is at your own risk", intro.strip(), d["risks"],
                            details, "Install anyway"):
            return
        if d["mode"] == "dkms":
            self.win.run_job(d["title"], "install_community", {"id": d["id"], "accept_risk": True},
                             lambda ok, m: self.on_show())
        else:
            self._run_terminal(d, undo=False)

    def remove_community(self, d):
        if not confirm(self, d["title"], "Remove this driver?", "", "Remove"):
            return
        if d["mode"] == "dkms":
            self.win.run_job(d["title"], "remove_community", {"id": d["id"]}, lambda ok, m: self.on_show())
        else:
            self._run_terminal(d, undo=True)

    def _run_terminal(self, d, undo: bool):
        drv = community.by_id(d["id"])
        cache = Path.home() / ".cache" / "surface-companion" / "community"
        self.win.toast("Downloading the pinned version...")

        def launch(src):
            cmd = community.terminal_command(community.script_argv(drv, src, undo))
            if not cmd:
                notify(self, "No terminal found",
                       "Open a terminal and run:\n\n" + " ".join(community.script_argv(drv, src, undo)))
                return
            subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.win.toast("Opened a terminal. Come back here when it says it has finished.")
        self.b.run_local(lambda: community.fetch(drv, cache, lambda _l: None), ok=launch, err=self.win.toast)

    def _got(self, s):
        cam = s["camera"]
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        txt, tone = WIKI_STATUS.get(cam["wiki_status"], WIKI_STATUS["unknown"])
        self.rows.addWidget(Row("Camera hardware", f"Image processor: {cam['isp']}. Status is what the linux-surface wiki reports "
                                "for this model, not a test of your system.", Pill("Wiki: " + txt.lower(), tone)))
        nodes = cam["video_nodes"]
        self.rows.addWidget(Row("Camera devices found", ", ".join(f"{n['node']}: {n['name']}" for n in nodes) or "",
                                Pill(f"{len(nodes)}" if nodes else "None", "good" if nodes else "bad")))
        self.rows.addWidget(self.adv(Row("Kernel drivers loaded", ", ".join(cam["modules"]) or "",
                                         Pill("Yes" if cam["modules"] else "None", "good" if cam["modules"] else "muted"))))
        self.rows.addWidget(self.adv(Row("IPU3 firmware", "", Pill("Present" if cam["ipu3_firmware"] else "Missing",
                                                                   "good" if cam["ipu3_firmware"] else "muted"))))
        self.rows.addWidget(Row("libcamera", "", Pill("Installed" if cam["libcamera"] else "Not installed",
                                                      "good" if cam["libcamera"] else "muted")))
        self.rows.addWidget(self.adv(Row("PipeWire camera plugin", "Lets browsers and video-call apps use libcamera cameras.",
                                         Pill("Installed" if cam["pipewire_libcamera"] else "Not installed",
                                              "good" if cam["pipewire_libcamera"] else "muted"))))


# ------------------------------------------------------------------------------------------ fixes

LEVEL = {"recommended": ("Recommended", "accent"), "optional": ("Optional", "muted"), "advisory": ("Info", "warn")}


class FixesPage(Page):
    key, title = "fixes", "Fixes"
    subtitle = ("Workarounds the linux-surface project documents for known Surface problems. Only fixes that apply "
                "to your device are shown. Each one can be undone.")

    def build(self):
        self.list = QVBoxLayout()
        self.list.setSpacing(12)
        self.body.addLayout(self.list)
        self.empty = label("No known fixes apply to this device.", "muted")
        self.body.addWidget(self.empty)
        self.hidden_note = label("", "muted")
        self.hidden_note.hide()
        self.body.addWidget(self.hidden_note)
        self._fixes = []

    def on_show(self):
        self.b.call("get_fixes", ok=self.render, err=self.win.toast)

    def mode_changed(self):
        self.render(self._fixes)

    def render(self, fx):
        self._fixes = fx
        while self.list.count():
            w = self.list.takeAt(0).widget()
            if w:
                w.deleteLater()
        hidden = 0
        if not self.win.advanced:
            shown = [f for f in fx if f["level"] == "recommended" or f["status"] == "applied"]
            hidden = len(fx) - len(shown)
            fx_all, fx = fx, shown
        self.hidden_note.setVisible(bool(hidden))
        self.hidden_note.setText(f"{hidden} more optional or informational fix{'es' if hidden != 1 else ''} "
                                 "hidden. Switch to Advanced view (bottom left) to see everything.")
        self.empty.setVisible(not fx and not hidden)
        order = {"recommended": 0, "optional": 1, "advisory": 2}
        for f in sorted(fx, key=lambda f: (order[f["level"]], f["status"] == "applied", f["title"])):
            c = Card()
            head = QHBoxLayout()
            head.addWidget(label(f["title"], "h2"), 1)
            applied = f["status"] == "applied"
            if f["status"] == "conflict":
                head.addWidget(Pill("Conflicts", "warn"))
            elif f["level"] != "advisory":
                head.addWidget(Pill("Applied" if applied else LEVEL[f["level"]][0],
                                    "good" if applied else LEVEL[f["level"]][1]))
            else:
                head.addWidget(Pill("Info", "warn"))
            c.add_layout(head)
            c.add(label(f["summary"], "muted"))
            if f.get("note"):
                c.add(label(f["note"]))
            if f["steps"]:
                c.add(label(f["steps"]))
            row = QHBoxLayout()
            if f["applicable"] and not applied and f["status"] != "conflict":
                row.addWidget(button("Apply", "primary", lambda fx=f: self.apply(fx)))
            if applied and f["revertable"]:
                row.addWidget(button("Undo", on_click=lambda fx=f: self.revert(fx)))
            lk = QLabel(f"<a href='{f['source']}'>Source</a>")
            lk.setOpenExternalLinks(True)
            row.addStretch(1)
            row.addWidget(lk)
            if f["reboot"]:
                row.addWidget(label("Needs a restart", "muted"))
            c.add_layout(row)
            self.list.addWidget(c)

    def _run(self, f, action):
        if f["scope"] == "user":
            ctx = Ctx(Device(), dist.Distro())
            fix = fixes_mod.get(f["id"])
            fn = fix.apply if action == "apply_fix" else fix.revert
            lines = []
            self.b.run_local(lambda: fn(ctx, lines.append), ok=lambda _: self.on_show(), err=self.win.toast)
            return
        self.win.run_job(f["title"], action, {"id": f["id"]}, lambda ok, msg: self.on_show())

    def apply(self, f):
        if confirm(self, f["title"], f["summary"],
                   "This changes system files. You'll be asked for your password. You can undo it afterwards.",
                   "Apply"):
            self._run(f, "apply_fix")

    def revert(self, f):
        if confirm(self, f["title"], "Undo this fix?", "", "Undo"):
            self._run(f, "revert_fix")


# ------------------------------------------------------------------------------------------ system test

STATUS_PILL = {"ok": ("Pass", "good"), "warn": ("Warning", "warn"), "fail": ("Fail", "bad"), "skip": ("Skipped", "muted")}


class TestPage(Page):
    key, title = "test", "System test"
    subtitle = "Check that your Surface works: automatic checks, plus hands-on tests for the parts you touch."

    def build(self):
        from .. import systemtest
        self.systemtest = systemtest
        self.results: dict[str, dict] = {}
        self.hands_results: dict[str, dict] = {}
        self.stress_result: dict | None = None
        self.running = False

        bar = QHBoxLayout()
        self.run_btn = button("Run health check", "primary", self.run_all)
        self.copy_btn = button("Copy report", on_click=self.copy_report)
        self.save_btn = button("Save report…", on_click=self.save_report)
        for b in (self.run_btn, self.copy_btn, self.save_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        self.body.addLayout(bar)

        self.summary = Card()
        self.summary_text = label("Not run yet. The health check only reads your system; it changes nothing.", "muted")
        self.summary.add(self.summary_text)
        self.body.addWidget(self.summary)

        self.checks_card = Card("Health check")
        self.rows = QVBoxLayout()
        self.checks_card.add_layout(self.rows)
        self.body.addWidget(self.checks_card)
        self.row_widgets: dict[str, Row] = {}

        self.hands_card = Card("Hands-on tests", "These need you: press, plug, listen, look. Nothing is changed.")
        self.hands_rows: dict[str, tuple[Row, QWidget]] = {}
        self.body.addWidget(self.hands_card)

        self.stress_card = self.adv(Card("Stress test",
                                         "Loads every CPU core for a short time and watches temperature, clock speed, fan "
                                         "and throttling. It stops by itself above 95 °C. Plug in the charger first."))
        self.stress_len = QComboBox()
        for sec in (30, 60, 120):
            self.stress_len.addItem(f"{sec} seconds", sec)
        self.stress_text = label("", "muted")
        srow = QHBoxLayout()
        srow.addWidget(self.stress_len)
        srow.addWidget(button("Start stress test", on_click=self.run_stress))
        srow.addStretch(1)
        self.stress_card.add_layout(srow)
        self.stress_card.add(self.stress_text)
        self.body.addWidget(self.stress_card)

    # ---- hands-on rows are built once we know the device (Book vs Pro etc.)
    def _build_hands(self, family: str):
        if self.hands_rows:
            return
        tests = [("keyboard", "Keyboard", "Press every key.", self._kb),
                 ("pointer", "Mouse, touchpad and touch", "Move, click, scroll, touch.", self._pointer),
                 ("speakers", "Speakers", "Play a tone on each side.", self._speakers),
                 ("ports", "Ports", "Plug something into each port and watch it appear.", self._ports),
                 ("camera", "Camera", "Take a test photo.", self._camera)]
        if hands.detach_capable(family):
            tests.append(("detach", "Screen detach" if family == "book" else "Detachable keyboard",
                          "Watches the detach latch and keyboard while you detach and attach it." if family == "book"
                          else "Detach the keyboard cover and attach it again.", self._detach))
        for tid, name, desc, fn in tests:
            holder = QWidget()
            hl = QHBoxLayout(holder)
            hl.setContentsMargins(0, 0, 0, 0)
            pill = Pill("Not run", "muted")
            hl.addWidget(pill)
            hl.addWidget(button("Start", on_click=lambda f=fn, t=tid, n=name: self._run_hands(f, t, n)))
            row = Row(name, desc, holder)
            self.hands_card.add(row)
            self.hands_rows[tid] = (row, pill)

    def _run_hands(self, factory, tid, name):
        dlg = factory()
        dlg.exec()
        out = dlg.outcome or "skip"
        status = {"ok": "ok", "fail": "fail", "skip": "skip"}[out]
        self.hands_results[tid] = {"id": "hw-" + tid, "title": name, "status": status,
                                   "detail": dlg.note if out != "skip" else "skipped", "hint": "", "goto": None}
        pill = self.hands_rows[tid][1]
        text, tone = STATUS_PILL[status]
        pill.set(text, tone)

    def _kb(self):
        from .handson import KeyboardTest
        return KeyboardTest(self)

    def _pointer(self):
        from .handson import PointerTest
        return PointerTest(self)

    def _speakers(self):
        from .handson import SpeakerTest
        return SpeakerTest(self)

    def _ports(self):
        from .handson import PortsTest
        return PortsTest(self)

    def _camera(self):
        from .handson import CameraTest
        return CameraTest(self, self.b.run_local)

    def _detach(self):
        from .handson import DetachTest
        fam = (self.win.state or {}).get("device", {}).get("family", "")
        return DetachTest(self, fam)

    def update_state(self, st):
        self._build_hands(st["device"]["family"])

    def on_show(self):
        if self.win.state:
            self._build_hands(self.win.state["device"]["family"])

    # ---- health check
    def run_all(self):
        if self.running:
            return
        self.running = True
        self.run_btn.setEnabled(False)
        self.results = {}
        while self.rows.count():
            w = self.rows.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.row_widgets = {}
        self.summary_text.setText("Running...")
        for cid, title in self.systemtest.TITLES.items():
            row = Row(title, "", Pill("…", "muted"))
            self.rows.addWidget(row)
            self.row_widgets[cid] = row
        ids = list(self.systemtest.TITLES)
        self._next_check(ids, 0)

    def _next_check(self, ids, i):
        if i >= len(ids):
            self._finish()
            return

        def got(res):
            self._show(res)
            self._next_check(ids, i + 1)

        def failed(msg):
            self._show({"id": ids[i], "title": self.systemtest.TITLES[ids[i]], "status": "skip",
                        "detail": msg, "hint": "", "goto": None})
            self._next_check(ids, i + 1)
        self.b.call("run_test", {"id": ids[i]}, ok=got, err=failed)

    def _show(self, res):
        self.results[res["id"]] = res
        row = self.row_widgets.get(res["id"])
        if not row:
            return
        text, tone = STATUS_PILL[res["status"]]
        pill = row.control
        pill.set(text, tone)
        desc = res["detail"] + (f"\n→ {res['hint']}" if res["hint"] and res["status"] in ("warn", "fail") else "")
        row.set_desc(desc)

    def _finish(self):
        self.running = False
        self.run_btn.setEnabled(True)
        self.run_btn.setText("Run again")
        s = self.systemtest.summarize(list(self.results.values()))
        c = s["counts"]
        verdict = {"ok": "Everything checked out.", "warn": "Mostly fine, with some things to look at.",
                   "fail": "Something needs attention."}[s["verdict"]]
        self.summary_text.setText(f"{verdict}  {c['ok']} passed · {c['warn']} warnings · {c['fail']} failed · "
                                  f"{c['skip']} skipped")
        self.summary_text.setObjectName("")
        self.summary_text.setStyleSheet("")

    # ---- stress
    def run_stress(self):
        secs = self.stress_len.currentData()
        if not confirm(self, "Run the stress test?",
                       f"This pushes every CPU core to 100% for {secs} seconds. The fan will spin up and the device "
                       "will get warm. It stops by itself above 95 °C.",
                       "Plug in the charger first. Save your work, since the device may feel sluggish while it runs.",
                       "Start"):
            return

        def done(ok, msg):
            if not ok:
                self.stress_text.setText("Couldn't run: " + msg)
                return
            import json as _json
            self.stress_result = _json.loads(msg)
            r = self.stress_result
            self.stress_text.setText(f"{STATUS_PILL[r['status']][0]}: {r['summary']}")
        self.win.run_job("Stress test", "stress_test", {"seconds": secs}, done)

    # ---- report
    def _all_results(self) -> list[dict]:
        order = list(self.systemtest.TITLES)
        out = [self.results[i] for i in order if i in self.results]
        out += list(self.hands_results.values())
        return out

    def report(self) -> str:
        d = (self.win.state or {}).get("device", {})
        dev = {"model": d.get("model", "?"), "kernel": d.get("kernel", "?"), "distro": self.win.distro_name}
        stress = ({"summary": self.stress_result["summary"]} if self.stress_result else None)
        return self.systemtest.report_text(self._all_results(), dev, stress)

    def copy_report(self):
        from PyQt6.QtWidgets import QApplication
        if not self._all_results():
            self.win.toast("Run the health check or a hands-on test first.")
            return
        QApplication.clipboard().setText(self.report())
        self.win.toast("Report copied. Paste it into a bug report or message.")

    def save_report(self):
        from PyQt6.QtWidgets import QFileDialog
        if not self._all_results():
            self.win.toast("Run the health check or a hands-on test first.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save report", str(Path.home() / "surface-system-test.txt"),
                                              "Text files (*.txt)")
        if path:
            Path(path).write_text(self.report())
            self.win.toast(f"Saved to {path}")


# ------------------------------------------------------------------------------------------ software

class SoftwarePage(Page):
    key, title = "software", "Software & Updates"
    subtitle = "Install the Surface kernel and drivers, and keep them up to date."

    def build(self):
        self.banner_box = QVBoxLayout()
        self.body.addLayout(self.banner_box)
        self.sys = Card("System")
        self.sys_rows = QVBoxLayout()
        self.sys.add_layout(self.sys_rows)
        self.body.addWidget(self.sys)

        self.comp = Card("Drivers and tools")
        self.comp_rows = QVBoxLayout()
        self.comp.add_layout(self.comp_rows)
        self.body.addWidget(self.comp)

        self.upd = Card("Updates")
        self.upd_text = label("Not checked yet.", "muted")
        self.upd.add(self.upd_text)
        r = QHBoxLayout()
        self.check_btn = button("Check for updates", on_click=self.check)
        self.update_btn = button("Update all", "primary", self.update_all)
        self.update_btn.setVisible(False)
        self.repo_btn = button("Set up repository", "primary", self.setup_repo)
        self.repo_btn.setVisible(False)
        r.addWidget(self.repo_btn)
        r.addWidget(self.check_btn)
        r.addWidget(self.update_btn)
        r.addStretch(1)
        self.upd.add_layout(r)
        self.body.addWidget(self.upd)

        self.diag = self.adv(Card("Support"))
        self.diag.add(label("Collect a diagnostics bundle (device info, kernel messages, loaded drivers) to attach "
                            "to a bug report. Nothing is uploaded.", "muted"))
        self.diag.add(button("Create diagnostics bundle", on_click=self.diagnostics))
        self.body.addWidget(self.diag)
        self.data = None

    def on_show(self):
        self.b.call("get_software", ok=self.render, err=self.win.toast)

    def render(self, d):
        self.data = d
        self.win.software = d
        for lay in (self.sys_rows, self.comp_rows, self.banner_box):
            while lay.count():
                w = lay.takeAt(0).widget()
                if w:
                    w.deleteLater()
        distro, sysd = d["distro"], d["system"]
        asm = d.get("assessment", {"level": "supported", "reasons": []})
        if asm["level"] == "unsupported":
            self.banner_box.addWidget(Banner(
                "<b>Unsupported system.</b> You can still install the Surface software here, but it is at your own "
                "risk. You will see a full warning and have to confirm before anything runs.", "bad"))
        elif asm["level"] == "blocked":
            self.banner_box.addWidget(Banner("<b>Can't install on this system.</b><br>" + "<br>".join(asm["reasons"]),
                                             "bad"))
        if not sysd["repo"] and can_setup and any(c["installed"] for c in d["components"]):
            self.banner_box.addWidget(Banner(
                "<b>The linux-surface repository isn't set up.</b> You have Surface packages installed, but this "
                "system can't see new releases of them. Use “Set up repository” below.", "bad"))
        if sysd["tlp"]:
            self.banner_box.addWidget(Banner("TLP is running. The linux-surface project warns it can slow Surface "
                                             "devices down.", "warn"))
        self.sys_rows.addWidget(Row("Running kernel", sysd["kernel"],
                                    Pill("linux-surface" if sysd["surface_kernel"] else "Standard kernel",
                                         "good" if sysd["surface_kernel"] else "muted")))
        can_setup = distro["family"] in ("debian", "fedora", "arch") and not distro["immutable"] \
            and asm["level"] != "blocked"
        if sysd["repo"]:
            repo_ctl = Pill("Set up", "good")
            repo_desc = ""
        else:
            repo_ctl = button("Set up repository", "primary", self.setup_repo) if can_setup else Pill("Not set up", "bad")
            repo_desc = ("Without it, new Surface kernels and drivers can't be found, so updates will never show up. "
                         + ("The file /etc/apt/sources.list.d/linux-surface.list exists but is empty, which "
                            "usually happens after a distribution upgrade." if sysd.get("repo_leftover") else ""))
        self.sys_rows.addWidget(Row("linux-surface repository", repo_desc.strip(), repo_ctl))
        sb = sysd["secure_boot"]
        self.sys_rows.addWidget(Row("Secure Boot", "",
                                    Pill(sb.title(), "good" if sb == "enabled" else "muted")))
        for c in d["components"]:
            if not c["installable"] and c["reason"].startswith("Only for Surface Book"):
                continue
            svc = c.get("services")
            if c["installed"] and svc and not svc["running"]:
                ctl = button("Enable services", "primary", lambda cid=c["id"]: self.win.install([cid]))
            elif c["installed"]:
                ctl = Pill(f"Installed {c['installed'][:18]}", "good")
            elif c["installable"]:
                ctl = button("Install", "primary", lambda cid=c["id"]: self.win.install([cid]))
            else:
                ctl = Pill("Unavailable", "muted")
            desc = c["why"] if c["installable"] or not c["reason"] else c["reason"]
            if svc:
                st = svc["state"]
                bits = [f"{u.removesuffix('.service')}: {v}" for u, v in st["system"].items()]
                bits += [f"{u.removesuffix('.service')} ({user}): {v}" for user, per in st["user"].items() for u, v in per.items()]
                desc += "\n" + " · ".join(bits)
            self.comp_rows.addWidget(Row(c["title"], desc, ctl))
        nv = d.get("nvidia") or {}
        if nv.get("present"):
            drv = nv.get("loaded")
            ctl = Pill("Installed", "good") if drv == "nvidia" else button("Install…", "primary", self.win.install_nvidia)
            self.comp_rows.addWidget(Row("NVIDIA graphics driver",
                                         "NVIDIA's own driver for the discrete GPU: full speed and CUDA. "
                                         + ("Currently using the open 'nouveau' driver." if drv == "nouveau" else ""), ctl))
        row = QHBoxLayout()
        wrap = QWidget()
        wrap.setLayout(row)
        row.setContentsMargins(0, 8, 0, 0)
        row.addWidget(button("Install everything recommended", on_click=self.install_recommended))
        row.addStretch(1)
        self.comp_rows.addWidget(wrap)

    def install_recommended(self):
        ids = ["kernel", "iptsd", "libwacom", "secureboot"]
        if self.win.state and self.win.state["device"]["family"] == "book":
            ids.append("dtx")
        self.win.install([i for i in ids if i in {c["id"] for c in self.data["components"] if c["installable"] and not c["installed"]}])

    def setup_repo(self):
        if confirm(self, "Set up the linux-surface repository?",
                   "This adds the project's package repository and signing key so Surface kernels and drivers can be "
                   "installed and updated.", "The key is checked against the published fingerprint before it is "
                   "trusted. You will be asked for your password.", "Set up"):
            self.win.run_job("Setting up repository", "setup_repo", {},
                             lambda ok, m: (self.on_show(), self.check() if ok else None))

    def check(self):
        self.check_btn.setEnabled(False)
        self.upd_text.setText("Checking...")

        def done(ok, msg):
            self.check_btn.setEnabled(True)
            import json
            res = json.loads(msg) if ok else {"updates": [], "warnings": [msg]}
            ups, warns = res["updates"], res["warnings"]
            self.win.updates = ups
            lines = [f"⚠ {w}" for w in warns]
            if ups:
                lines += [f"{u['name']}  {u['installed']} → {u['available']}" for u in ups]
            elif not warns:
                lines.append("Everything Surface-related is up to date.")
            elif ok:
                lines.append("No updates could be found.")
            self.upd_text.setText("\n".join(lines))
            self.update_btn.setVisible(bool(ups))
            self.repo_btn.setVisible(any("not set up" in w for w in warns))
        self.b.call("check_updates", ok=lambda r: self.b.track(r["job"], lambda l: None, done) if isinstance(r, dict) and "job" in r else done(True, '{"updates": [], "warnings": []}'),
                    err=lambda m: done(False, m))

    def update_all(self):
        pkgs = [u["name"] for u in self.win.updates]
        detail = ("Updates:\n" + "\n".join(pkgs))
        if self.data["distro"]["family"] == "arch":
            detail += "\n\nArch can't update single packages, so the whole system is updated."
        if confirm(self, "Install updates?", f"{len(pkgs)} package(s) will be updated.", detail, "Update"):
            self.win.run_job("Updating", "apply_updates", {"pkgs": pkgs}, lambda ok, m: self.check())

    def diagnostics(self):
        self.win.run_job("Diagnostics", "diagnostics", {},
                         lambda ok, msg: None)
