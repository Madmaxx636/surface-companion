from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QScrollArea,
                             QVBoxLayout, QWidget)

from . import icons, theme
from .widgets import LogView, button, label


class JobDialog(QDialog):
    """Runs a daemon job and shows its output live."""
    def __init__(self, parent, backend, title, action, args, on_done=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(640, 420)
        self.backend, self.on_done = backend, on_done
        lay = QVBoxLayout(self)
        self.heading = label(title, "h2")
        self.status = label("Waiting for permission...", "muted")
        self.log = LogView()
        self.close_btn = button("Close", on_click=self.accept)
        self.close_btn.setEnabled(False)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.close_btn)
        for w in (self.heading, self.status, self.log):
            lay.addWidget(w)
        lay.addLayout(row)
        self.finished_ok = None
        backend.call(action, args, ok=self._started, err=self._failed)

    def _started(self, res):
        job = res.get("job") if isinstance(res, dict) else None
        if not job:
            self._finish(True, "Done")
            return
        self.status.setText("Working... this can take a few minutes.")
        self.backend.track(job, self.log.add, self._finish)

    def _failed(self, msg):
        self.log.add(msg)
        self._finish(False, msg)

    def _finish(self, ok, msg):
        self.finished_ok = ok
        self.status.setText(("Finished: " if ok else "Failed: ") + msg)
        self.close_btn.setEnabled(True)
        self.close_btn.setObjectName("primary")
        self.close_btn.style().unpolish(self.close_btn)
        self.close_btn.style().polish(self.close_btn)
        if self.on_done:
            self.on_done(ok, msg)

    def reject(self):          # don't let Esc close a running job
        if self.close_btn.isEnabled():
            super().reject()


def confirm(parent, title: str, text: str, detail: str = "", ok_text: str = "Continue", danger=False) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if danger else QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    if detail:
        box.setInformativeText(detail)
    ok = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(box.buttons()[-1])
    box.exec()
    return box.clickedButton() is ok


def notify(parent, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


class RiskDialog(QDialog):
    """Big, unmissable warning. The action button stays disabled until the box is ticked; Cancel is the default."""
    def __init__(self, parent, headline, intro, risks, details="", action_text="Install anyway"):
        super().__init__(parent)
        self.setWindowTitle("Warning")
        self.setModal(True)
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        head = QFrame()
        head.setObjectName("riskhead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(18, 14, 18, 14)
        ic = QLabel()
        ic.setPixmap(icons.warning_pixmap(44, "#ffffff"))
        hl.addWidget(ic)
        t = QLabel(headline)
        t.setWordWrap(True)
        t.setStyleSheet("font-size: 16pt; font-weight: 700;")
        hl.addWidget(t, 1)
        lay.addWidget(head)
        if intro:
            lay.addWidget(label(intro))
        box = QFrame()
        box.setObjectName("riskbox")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(8)
        bl.addWidget(label("What can go wrong", "h2"))
        for r in risks:
            lb = QLabel("•  " + r)
            lb.setWordWrap(True)
            bl.addWidget(lb)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(box)
        scroll.setMinimumHeight(min(320, 60 + 44 * len(risks)))
        lay.addWidget(scroll, 1)
        if details:
            self.details = QPlainTextEdit(details)
            self.details.setReadOnly(True)
            self.details.setMaximumHeight(150)
            self.details.hide()
            self.toggle = button("Show what will run", on_click=self._toggle)
            lay.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
            lay.addWidget(self.details)
        self.check = QCheckBox("I understand the risks and want to continue anyway.")
        lay.addWidget(self.check)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel = button("Cancel", on_click=self.reject)
        self.go = button(action_text, "dangerfill", self.accept)
        self.go.setEnabled(False)
        self.check.toggled.connect(self.go.setEnabled)
        row.addWidget(self.cancel)
        row.addWidget(self.go)
        lay.addLayout(row)
        self.cancel.setDefault(True)
        self.cancel.setFocus()

    def _toggle(self):
        show = not self.details.isVisible()
        self.details.setVisible(show)
        self.toggle.setText("Hide" if show else "Show what will run")


def confirm_risk(parent, headline, intro, risks, details="", action_text="Install anyway") -> bool:
    return RiskDialog(parent, headline, intro, risks, details, action_text).exec() == QDialog.DialogCode.Accepted
