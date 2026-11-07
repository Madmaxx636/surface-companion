from __future__ import annotations

import argparse
import os
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from . import theme
from .backend import Backend
from .main import MainWindow


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="surface-companion")
    ap.add_argument("--session-bus", action="store_true", help="talk to a daemon on the session bus (development)")
    ap.add_argument("--hidden", action="store_true", help="start in the tray without showing the window")
    a, qt_args = ap.parse_known_args(argv)
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("Surface Companion")
    app.setDesktopFileName("surface-companion")
    app.setQuitOnLastWindowClosed(False)
    theme.apply(app)
    win = MainWindow(Backend(session_bus=a.session_bus))
    if hasattr(app.styleHints(), "colorSchemeChanged"):
        app.styleHints().colorSchemeChanged.connect(lambda _: (theme.apply(app), win.restyle()))
    app.setQuitOnLastWindowClosed(win.tray is None)
    if not (a.hidden and win.tray):
        win.show()
    if not os.environ.get("SC_SKIP_WELCOME"):
        QTimer.singleShot(600, win.maybe_first_run)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
