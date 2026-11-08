"""Interactive hands-on tests: each is a dialog that ends with It works / Something's wrong / Skip."""
from __future__ import annotations

import os
import subprocess
import tempfile

from PyQt6.QtCore import QEvent, QRectF, QTimer, Qt
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout,
                             QWidget)

from .. import handson
from . import theme
from .widgets import button, label

# physical keys by Linux evdev code, so the test checks the hardware and not the keyboard layout
ROWS = [
    [("Esc", 1)] + [(f"F{i}", 58 + i) for i in range(1, 11)] + [("F11", 87), ("F12", 88)],
    [("`", 41)] + [(str(i % 10), 1 + i) for i in range(1, 11)] + [("-", 12), ("=", 13), ("Bksp", 14)],
    [("Tab", 15)] + [(c, code) for c, code in zip("QWERTYUIOP", range(16, 26))] + [("[", 26), ("]", 27), ("\\", 43)],
    [("Caps", 58)] + [(c, code) for c, code in zip("ASDFGHJKL", range(30, 39))] + [(";", 39), ("'", 40), ("Enter", 28)],
    [("Shift", 42)] + [(c, code) for c, code in zip("ZXCVBNM", range(44, 51))] + [(",", 51), (".", 52), ("/", 53), ("Shift", 54)],
    [("Ctrl", 29), ("Super", 125), ("Alt", 56), ("Space", 57), ("Alt", 100), ("Ctrl", 97), ("←", 105), ("↑", 103), ("↓", 108), ("→", 106)],
]
QT_FALLBACK = {Qt.Key.Key_Escape: 1, Qt.Key.Key_Space: 57, Qt.Key.Key_Return: 28, Qt.Key.Key_Enter: 28,
               Qt.Key.Key_Backspace: 14, Qt.Key.Key_Tab: 15, Qt.Key.Key_Up: 103, Qt.Key.Key_Down: 108,
               Qt.Key.Key_Left: 105, Qt.Key.Key_Right: 106, Qt.Key.Key_Shift: 42, Qt.Key.Key_Control: 29,
               Qt.Key.Key_Alt: 56, Qt.Key.Key_Meta: 125, Qt.Key.Key_CapsLock: 58}
for _row in ROWS:
    for _label, _code in _row:
        if len(_label) == 1 and _label.isalnum():
            QT_FALLBACK[Qt.Key(ord(_label))] = _code


def evdev_code(e) -> int | None:
    """Physical key code of a Qt key event. Qt reports the X11/Wayland keycode, which is the evdev code plus 8."""
    sc = e.nativeScanCode()
    if sc > 8:
        return sc - 8
    return QT_FALLBACK.get(e.key())


class HandsOnDialog(QDialog):
    def __init__(self, parent, title: str, instructions: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(680, 460)
        self.setModal(True)
        self.outcome: str | None = None
        self.note = ""
        self.lay = QVBoxLayout(self)
        self.lay.setSpacing(10)
        self.lay.addWidget(label(title, "h1"))
        self.lay.addWidget(label(instructions, "muted"))
        self.body = QVBoxLayout()
        self.lay.addLayout(self.body, 1)
        row = QHBoxLayout()
        self.skip_btn = button("Skip", "link", lambda: self._done("skip"))
        self.bad_btn = button("Something's wrong", "danger", lambda: self._done("fail"))
        self.ok_btn = button("It works", "primary", lambda: self._done("ok"))
        for b in (self.skip_btn, self.bad_btn, self.ok_btn):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)          # keys and clicks must reach the test, not the buttons
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        row.addWidget(self.bad_btn)
        row.addWidget(self.ok_btn)
        self.lay.addLayout(row)

    def _done(self, outcome: str):
        self.outcome = outcome
        self.accept()

    def set_can_pass(self, ok: bool):
        self.ok_btn.setEnabled(ok)


# ------------------------------------------------------------------ keyboard

class KeyGrid(QWidget):
    def __init__(self):
        super().__init__()
        self.pressed: set[int] = set()
        self.setMinimumHeight(250)

    def codes(self) -> list[int]:
        return sorted({c for r in ROWS for _, c in r})

    def press(self, code: int):
        if code in self.codes():
            self.pressed.add(code)
            self.update()

    def missing(self) -> list[str]:
        have = self.pressed
        return [lbl for r in ROWS for lbl, c in r if c not in have]

    def paintEvent(self, _):
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n_rows = len(ROWS)
        gap = 4
        rh = (self.height() - gap * (n_rows - 1)) / n_rows
        for ri, row in enumerate(ROWS):
            widths = [3.2 if l in ("Space",) else 1.7 if len(l) > 2 else 1.0 for l, _ in row]
            unit = (self.width() - gap * (len(row) - 1)) / sum(widths)
            x, y = 0.0, ri * (rh + gap)
            for (lbl, code), w in zip(row, widths):
                r = QRectF(x, y, unit * w, rh)
                on = code in self.pressed
                p.setPen(QColor(c["border"]))
                p.setBrush(QColor(c["good"]) if on else QColor(c["surface"]))
                p.drawRoundedRect(r, 6, 6)
                p.setPen(QColor("white") if on else QColor(c["muted"]))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, lbl)
                x += unit * w + gap


class KeyboardTest(HandsOnDialog):
    def __init__(self, parent):
        super().__init__(parent, "Keyboard test",
                         "Press every key. Each one lights up when it works. Your desktop may keep a few keys for "
                         "itself (like the Super key); those can't be tested here.")
        self.grid = KeyGrid()
        self.count = label("")
        self.body.addWidget(self.grid)
        self.body.addWidget(self.count)
        self.set_can_pass(False)
        self._update()

    def keyPressEvent(self, e):
        code = evdev_code(e)
        if code is not None:
            self.grid.press(code)
            self._update()
        e.accept()                           # never let Esc or Enter close the dialog

    def _update(self):
        total = len(self.grid.codes())
        got = len(self.grid.pressed)
        self.count.setText(f"{got} of {total} keys detected")
        self.set_can_pass(got >= total * 0.9)
        self.note = f"{got} of {total} keys detected" + ("" if got == total else "; not pressed: " + " ".join(self.grid.missing()))


# ------------------------------------------------------------------ mouse, touchpad, touchscreen

class PointerArea(QFrame):
    def __init__(self, on_event):
        super().__init__()
        self.on_event = on_event
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents, True)
        self.setObjectName("card")
        self._moved = 0
        self._last = None
        self._pos = None

    def mouseMoveEvent(self, e):
        p = e.position()
        if self._last is not None:
            self._moved += abs(p.x() - self._last.x()) + abs(p.y() - self._last.y())
            if self._moved > 400:
                self.on_event("move")
        self._last = p
        self._pos = p
        self.update()

    def mousePressEvent(self, e):
        b = {Qt.MouseButton.LeftButton: "left", Qt.MouseButton.RightButton: "right", Qt.MouseButton.MiddleButton: "middle"}.get(e.button())
        if b:
            self.on_event(b)

    def mouseDoubleClickEvent(self, e):
        self.on_event("double")

    def wheelEvent(self, e):
        self.on_event("scroll")

    def event(self, e):
        if e.type() in (QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate):
            self.on_event("touch")
            return True
        return super().event(e)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._pos is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QColor(theme.colors()["accent"]))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(self._pos, 8, 8)


class PointerTest(HandsOnDialog):
    ITEMS = [("move", "Move the pointer around"), ("left", "Left click"), ("right", "Right click"),
             ("middle", "Middle click (if you have one)"), ("scroll", "Scroll (wheel, or two fingers on the touchpad)"),
             ("double", "Double click"), ("touch", "Touch the screen (touchscreens only)")]
    OPTIONAL = {"middle", "touch"}

    def __init__(self, parent):
        super().__init__(parent, "Mouse, touchpad and touch test",
                         "Use the area below with your mouse, touchpad or finger. Each action ticks off when it is "
                         "detected. Middle click and touch are optional.")
        self.seen: set[str] = set()
        self.area = PointerArea(self._event)
        self.body.addWidget(self.area, 1)
        self.marks = {}
        for key, text in self.ITEMS:
            lb = QLabel(f"○  {text}")
            self.marks[key] = lb
            self.body.addWidget(lb)
        self.set_can_pass(False)

    def _event(self, key: str):
        if key in self.seen:
            return
        self.seen.add(key)
        c = theme.colors()
        text = dict(self.ITEMS)[key]
        self.marks[key].setText(f"✓  {text}")
        self.marks[key].setStyleSheet(f"color: {c['good']}; font-weight: 600;")
        needed = {k for k, _ in self.ITEMS} - self.OPTIONAL
        self.set_can_pass(needed <= self.seen)
        miss = [dict(self.ITEMS)[k] for k, _ in self.ITEMS if k not in self.seen]
        self.note = "detected: " + ", ".join(sorted(self.seen)) + (f"; not detected: {', '.join(miss)}" if miss else "")


# ------------------------------------------------------------------ speakers

class SpeakerTest(HandsOnDialog):
    def __init__(self, parent):
        super().__init__(parent, "Speaker test",
                         "Plays a short tone on the left, the right, then both speakers. Turn your volume to a "
                         "comfortable level first. Tick each one you can hear.")
        self.proc = None
        self.player = handson.player_cmd("x")
        self.tmp = tempfile.mkdtemp(prefix="surface-tone-")
        self.heard = {}
        for ch, text in (("left", "Left speaker"), ("right", "Right speaker"), ("both", "Both speakers")):
            row = QHBoxLayout()
            row.addWidget(button(f"Play {ch}", on_click=lambda c=ch: self._play(c)))
            cb = QCheckBox(f"I heard the {text.lower()}")
            cb.toggled.connect(self._changed)
            self.heard[ch] = cb
            row.addWidget(cb, 1)
            self.body.addLayout(row)
        self.status = label("" if self.player else "No audio player found (pw-play, paplay or aplay). Install PipeWire or "
                                                    "ALSA utilities to run this test.", "muted")
        self.body.addWidget(self.status)
        self.body.addStretch(1)
        self.set_can_pass(False)

    def _play(self, ch: str):
        if not self.player:
            return
        path = handson.tone_wav(os.path.join(self.tmp, f"{ch}.wav"), ch)
        cmd = handson.player_cmd(path)
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.status.setText(f"Couldn't play: {e}")

    def _changed(self):
        self.set_can_pass(all(cb.isChecked() for cb in self.heard.values()))
        self.note = "heard: " + ", ".join(k for k, cb in self.heard.items() if cb.isChecked()) or "heard nothing"

    def done(self, r):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().done(r)


# ------------------------------------------------------------------ ports

class PortsTest(HandsOnDialog):
    def __init__(self, parent):
        super().__init__(parent, "Ports test",
                         "Plug something into each port one at a time (a USB drive, a charger, a monitor, headphones) "
                         "and watch it show up below. Unplug it to see it disappear. Tick Pass when every port you "
                         "care about has worked.")
        self.snap = handson.ports_snapshot()
        self.state = label("")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Plug and unplug events appear here...")
        self.body.addWidget(self.state)
        self.body.addWidget(self.log, 1)
        self.events: list[str] = []
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._render()
        self.set_can_pass(True)

    def _render(self):
        self.state.setText("\n".join(handson.describe_ports(self.snap)))

    def _poll(self):
        new = handson.ports_snapshot()
        for ev in handson.diff_snapshots(self.snap, new):
            self.events.append(ev)
            self.log.appendPlainText(ev)
        self.snap = new
        self._render()
        self.note = (f"{len(self.events)} plug/unplug events seen: " + "; ".join(self.events[:6])) if self.events else "no plug events seen"

    def done(self, r):
        self.timer.stop()
        super().done(r)


# ------------------------------------------------------------------ camera

class CameraTest(HandsOnDialog):
    def __init__(self, parent, run_local):
        super().__init__(parent, "Camera test",
                         "Takes one test photo from the camera you pick and shows it here. The camera light may turn on "
                         "briefly. The photo is kept only in a temporary folder and deleted when you close this window.")
        self.run_local = run_local
        self.nodes = handson.capture_nodes()
        self.tmp = tempfile.mkdtemp(prefix="surface-cam-")
        self.combo = QComboBox()
        for node, name in self.nodes:
            self.combo.addItem(f"{name} ({node})", node)
        self.shot = QLabel("No photo yet")
        self.shot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.shot.setMinimumHeight(240)
        self.shot.setObjectName("card")
        self.status = label("" if self.nodes else "No camera device found.", "muted")
        row = QHBoxLayout()
        row.addWidget(self.combo, 1)
        self.go = button("Take test photo", "primary", self._take)
        row.addWidget(self.go)
        self.body.addLayout(row)
        self.body.addWidget(self.shot, 1)
        self.body.addWidget(self.status)
        self.go.setEnabled(bool(self.nodes))
        self.go.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.set_can_pass(False)

    def _take(self):
        node = self.combo.currentData()
        out = os.path.join(self.tmp, "shot.jpg")
        cmd = handson.capture_cmd(node, out)
        if not cmd:
            self.status.setText("No capture tool found. Install ffmpeg (or v4l-utils or GStreamer) to run this test.")
            return
        self.status.setText("Taking photo...")
        self.go.setEnabled(False)

        def work():
            if os.path.exists(out):
                os.unlink(out)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if r.returncode or not os.path.exists(out):
                raise RuntimeError((r.stderr or "no picture was produced").strip().splitlines()[-1][:160])
            return out

        def ok(path):
            self.go.setEnabled(True)
            pm = QPixmap(path)
            if pm.isNull():
                self.status.setText("The camera produced a file this window can't show.")
                return
            self.shot.setPixmap(pm.scaled(self.shot.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
            self.status.setText("Does the picture look right?")
            self.set_can_pass(True)
            self.note = f"photo taken from {node}"

        def err(msg):
            self.go.setEnabled(True)
            self.status.setText(f"That camera didn't give a picture: {msg}. Try another one from the list.")
            self.note = f"capture from {node} failed: {msg}"
        self.run_local(work, ok=ok, err=err)

    def done(self, r):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().done(r)


# ------------------------------------------------------------------ detach (Surface Book) / Type Cover

class DetachTest(HandsOnDialog):
    def __init__(self, parent, family: str):
        self.book = family == "book"
        super().__init__(parent, "Screen detach test" if self.book else "Detachable keyboard test",
                         ("This test only watches. It never detaches the screen itself (use “Detach screen” on the "
                          "Surface Book page for that). Detach or fold the screen yourself when you're ready, then "
                          "attach it again, and watch the readout change.")
                         if self.book else
                         "Detach the keyboard cover, then attach it again. This test watches the keyboard and touchpad "
                         "disappear and come back. It passes by itself once it has seen both.")
        self.seen: set[str] = set()
        self.kb = handson.keyboard_state()
        self.mode0 = handson.detach_state() if self.book else None
        self.modes_seen: set = set()
        self.state = label("")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.body.addWidget(self.state)
        self.body.addWidget(self.log, 1)
        self.timer = QTimer(self, interval=700)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self.set_can_pass(False)
        self._poll()

    def _poll(self):
        new = handson.keyboard_state()
        for ev in handson.keyboard_events(self.kb, new):
            self.seen.add(ev)
            self.log.appendPlainText(ev)
        self.kb = new
        lines = [f"Keyboards detected: {new['keyboards']}   Touchpads detected: {new['touchpads']}"]
        if self.book:
            d = handson.detach_state()
            if d["dtx"]:
                lines.append(f"Screen mode: {d['mode']}   Latch: {d['latch']}   Base attached: {d['base_attached']}")
                if d["mode"] and d["mode"] not in self.modes_seen:
                    if self.modes_seen:
                        self.log.appendPlainText(f"screen mode changed to {d['mode']}")
                    self.modes_seen.add(d["mode"])
            else:
                lines.append("The detach device (/dev/surface/dtx) was not found. Boot the linux-surface kernel.")
        self.state.setText("\n".join(lines))
        changed = len(self.modes_seen) > 1
        both = {"keyboard detached", "keyboard attached"} <= self.seen
        self.set_can_pass(both or changed)
        self.note = ("saw: " + ", ".join(sorted(self.seen | ({"screen mode change"} if changed else set())))) or "no change seen"

    def done(self, r):
        self.timer.stop()
        super().done(r)
