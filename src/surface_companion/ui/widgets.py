"""Small reusable widgets shared by the pages."""
from __future__ import annotations

from PyQt6.QtCore import QPropertyAnimation, QRectF, Qt, pyqtProperty, pyqtSignal, QEasingCurve, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
                             QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget)

from . import theme


def label(text="", name="", wrap=True) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def button(text, kind="", on_click=None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click:
        b.clicked.connect(lambda _=False: on_click())
    return b


class Card(QFrame):
    def __init__(self, title: str = "", subtitle: str = ""):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 16, 18, 16)
        self.lay.setSpacing(10)
        if title:
            self.lay.addWidget(label(title, "h2"))
        if subtitle:
            self.lay.addWidget(label(subtitle, "muted"))

    def add(self, w):
        self.lay.addWidget(w)
        return w

    def add_layout(self, l):
        self.lay.addLayout(l)


def separator() -> QFrame:
    f = QFrame()
    f.setObjectName("sep")
    return f


class Toggle(QAbstractButton):
    """iOS-style switch; identical on every desktop."""
    def __init__(self):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pos = 0.0
        self.anim = QPropertyAnimation(self, b"pos_", self)
        self.anim.setDuration(120)
        self.anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self.toggled.connect(self._animate)

    def _animate(self, on):
        self.anim.stop()
        self.anim.setEndValue(1.0 if on else 0.0)
        self.anim.start()

    def set_silently(self, on: bool):
        self.blockSignals(True)
        self.setChecked(on)
        self._pos = 1.0 if on else 0.0
        self.blockSignals(False)
        self.update()

    def getPos(self):
        return self._pos

    def setPos(self, v):
        self._pos = v
        self.update()
    pos_ = pyqtProperty(float, getPos, setPos)

    def paintEvent(self, _):
        c = theme.colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor(c["accent"] if self.isChecked() else c["track"])
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 44, 24), 12, 12)
        p.setBrush(QColor("white"))
        x = 3 + 20 * self._pos
        p.drawEllipse(QRectF(x, 3, 18, 18))


class Row(QWidget):
    """Title + description on the left, a control on the right."""
    def __init__(self, title: str, desc: str = "", control: QWidget | None = None):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(14)
        col = QVBoxLayout()
        col.setSpacing(1)
        self.title = QLabel(title)
        self.title.setWordWrap(True)
        col.addWidget(self.title)
        self.desc = label(desc, "muted")
        self.desc.setVisible(bool(desc))
        col.addWidget(self.desc)
        lay.addLayout(col, 1)
        self.control = control
        if control is not None:
            lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_desc(self, t: str):
        self.desc.setText(t)
        self.desc.setVisible(bool(t))


class SliderRow(QWidget):
    """Slider with a live percentage; only emits `committed` when the user lets go."""
    committed = pyqtSignal(int)

    def __init__(self, lo=0, hi=100, suffix="%"):
        super().__init__()
        self.suffix = suffix
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setMinimumWidth(180)
        self.slider.setMinimumHeight(26)
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(48)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value_label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(lambda: self.committed.emit(self.slider.value()))
        self.slider.valueChanged.connect(self._changed)
        self._silent = False

    def _changed(self, v):
        self.value_label.setText(f"{v}{self.suffix}")
        if not self._silent:
            self._timer.start()

    def set_value(self, v: int):
        if self.slider.isSliderDown():
            return
        self._silent = True
        self.slider.setValue(v)
        self.value_label.setText(f"{v}{self.suffix}")
        self._silent = False


class Pill(QLabel):
    def __init__(self, text="", tone="muted"):
        super().__init__(text)
        self.set(text, tone)

    def set(self, text: str, tone: str = "muted"):
        c = theme.colors()
        col = c.get(tone, c["muted"])
        self.setText(text)
        self.setStyleSheet(f"QLabel {{ color: {col}; border: 1px solid {col}; border-radius: 9px;"
                           f" padding: 1px 9px; background: transparent; }}")


class Banner(QFrame):
    def __init__(self, text="", tone="warn", actions: list[QWidget] | None = None):
        super().__init__()
        self.setObjectName("banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.text = label(text)
        lay.addWidget(self.text, 1)
        for a in actions or []:
            lay.addWidget(a)
        self.tone = tone
        self.restyle()

    def restyle(self):
        col = theme.colors().get(self.tone, theme.colors()["warn"])
        width = 3 if self.tone == "bad" else 1
        self.setStyleSheet(f"QFrame#banner {{ border: {width}px solid {col}; }}")

    def set_text(self, t):
        self.text.setText(t)


class ChoiceGroup(QWidget):
    """A column of large selectable cards (used for performance profiles)."""
    chosen = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        self.hints: dict[str, QLabel] = {}
        self._ids: tuple = ()

    def set_choices(self, choices: list[dict]):
        ids = tuple(c["id"] for c in choices)
        if ids == self._ids:
            return
        self._ids = ids
        for b in self.buttons.values():
            self.group.removeButton(b)
            b.deleteLater()
        self.buttons = {}
        self.hints = {}
        for c in choices:
            b = QPushButton()
            b.setObjectName("choice")
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(60)
            bl = QVBoxLayout(b)
            bl.setContentsMargins(14, 8, 14, 8)
            bl.setSpacing(1)
            for text, name in ((c["label"], ""), (c.get("hint", ""), "muted")):
                if text:
                    lb = QLabel(text)
                    lb.setObjectName(name) if name else None
                    if name:
                        self.hints[c["id"]] = lb
                    lb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                    bl.addWidget(lb)
            b.clicked.connect(lambda _=False, cid=c["id"]: self.chosen.emit(cid))
            self.group.addButton(b)
            self.lay.addWidget(b)
            self.buttons[c["id"]] = b

    def set_hint(self, cid: str, text: str):
        lb = self.hints.get(cid)
        if lb and lb.text() != text:
            lb.setText(text)

    def set_current(self, cid: str | None):
        b = self.buttons.get(cid or "")
        if b and not b.isChecked():
            b.setChecked(True)


class LogView(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setMinimumHeight(180)
        self.setMaximumBlockCount(5000)

    def add(self, line: str):
        self.appendPlainText(line)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


class Sparkline(QWidget):
    """Tiny history graph. `values` is any sequence of numbers; the range auto-scales with a floor."""
    def __init__(self, min_span: float = 10.0, color: str = "accent", height: int = 36):
        super().__init__()
        self.values: list[float] = []
        self.min_span = min_span
        self.color = color
        self.setMinimumHeight(height)
        self.setMaximumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values):
        self.values = list(values)
        self.update()

    def paintEvent(self, _):
        vals = self.values
        if len(vals) < 2:
            return
        c = theme.colors()
        lo, hi = min(vals), max(vals)
        if hi - lo < self.min_span:
            mid = (hi + lo) / 2
            lo, hi = mid - self.min_span / 2, mid + self.min_span / 2
        w, h = self.width() - 2, self.height() - 6
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        for i, v in enumerate(vals):
            x = 1 + w * i / (len(vals) - 1)
            y = 3 + h * (1 - (v - lo) / (hi - lo))
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        col = QColor(c.get(self.color, c["accent"]))
        p.setPen(QPen(col, 1.8))
        p.drawPath(path)
