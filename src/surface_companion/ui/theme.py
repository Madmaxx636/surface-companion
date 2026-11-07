"""One look on every desktop: Fusion style + our own palette and stylesheet.

Follows the system light/dark preference (Qt 6.5+ reads it from the desktop portal / platform
theme on GNOME, KDE, XFCE, etc.). Fonts are left alone so text matches the rest of the desktop.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

LIGHT = dict(bg="#f3f4f6", surface="#ffffff", border="#dcdfe4", text="#1b1d21", muted="#5d6470",
             accent="#0f6cbd", accent_text="#ffffff", sidebar="#e9ebef", hover="#e1e5eb",
             good="#16794a", warn="#a15c00", bad="#b3261e", track="#cfd4db")
DARK = dict(bg="#1c1e22", surface="#262930", border="#383c45", text="#eceef2", muted="#a0a7b3",
            accent="#4aa3ff", accent_text="#06121f", sidebar="#17191c", hover="#30343d",
            good="#4cc38a", warn="#e5a542", bad="#ff7b72", track="#444955")

_current = LIGHT


def colors() -> dict:
    return _current


def is_dark(app: QApplication) -> bool:
    forced = os.environ.get("SC_THEME")
    if forced in ("dark", "light"):
        return forced == "dark"
    hints = app.styleHints()
    if hasattr(hints, "colorScheme"):                       # Qt 6.5+
        return hints.colorScheme() == Qt.ColorScheme.Dark
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128   # older Qt: infer from the palette


def apply(app: QApplication) -> None:
    global _current
    _current = DARK if is_dark(app) else LIGHT
    c = _current
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(c["bg"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(c["surface"]))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c["bg"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(c["surface"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c["accent_text"]))
    pal.setColor(QPalette.ColorRole.Link, QColor(c["accent"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c["surface"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c["text"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c["muted"]))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(c))


def stylesheet(c: dict) -> str:
    return f"""
    QMainWindow, QWidget#page {{ background: {c['bg']}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QLabel {{ background: transparent; }}
    QLabel#h1 {{ font-size: 22pt; font-weight: 600; }}
    QLabel#h2 {{ font-size: 12pt; font-weight: 600; }}
    QLabel#muted {{ color: {c['muted']}; }}
    QLabel#big {{ font-size: 26pt; font-weight: 600; }}

    QFrame#sidebar {{ background: {c['sidebar']}; border: none; }}
    QPushButton#nav {{ text-align: left; padding: 9px 12px; border: none; border-radius: 8px;
                       background: transparent; color: {c['text']}; }}
    QPushButton#nav:hover {{ background: {c['hover']}; }}
    QPushButton#nav:checked {{ background: {c['accent']}; color: {c['accent_text']}; }}

    QFrame#card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px; }}
    QFrame#card QLabel {{ background: transparent; }}
    QFrame#banner {{ border-radius: 10px; border: 1px solid {c['border']}; background: {c['surface']}; }}
    QFrame#sep {{ background: {c['border']}; max-height: 1px; min-height: 1px; border: none; }}

    QPushButton {{ padding: 7px 16px; border-radius: 8px; border: 1px solid {c['border']};
                   background: {c['surface']}; color: {c['text']}; }}
    QPushButton:hover {{ background: {c['hover']}; }}
    QPushButton:disabled {{ color: {c['muted']}; }}
    QPushButton#primary {{ background: {c['accent']}; color: {c['accent_text']}; border: 1px solid {c['accent']}; }}
    QPushButton#primary:hover {{ background: {c['accent']}; opacity: 0.9; }}
    QPushButton#primary:disabled {{ background: {c['track']}; border-color: {c['track']}; color: {c['muted']}; }}
    QPushButton#segl, QPushButton#segr {{ padding: 6px 10px; border: 1px solid {c['border']}; background: {c['surface']}; }}
    QPushButton#segl {{ border-top-right-radius: 0; border-bottom-right-radius: 0; }}
    QPushButton#segr {{ border-top-left-radius: 0; border-bottom-left-radius: 0; border-left: none; }}
    QPushButton#segl:checked, QPushButton#segr:checked {{ background: {c['accent']}; color: {c['accent_text']}; border-color: {c['accent']}; }}
    QPushButton#link {{ border: none; background: transparent; color: {c['accent']}; text-align: left; padding: 4px 2px; }}
    QPushButton#link:hover {{ text-decoration: underline; background: transparent; }}
    QPushButton[tour="true"] {{ border: 2px solid {c['accent']}; }}
    QFrame#tourcard {{ background: {c['surface']}; border: 2px solid {c['accent']}; border-radius: 12px; }}
    QFrame#tourcard QLabel {{ background: transparent; }}
    QPushButton#dangerfill {{ background: {c['bad']}; color: #ffffff; border: 1px solid {c['bad']}; font-weight: 600; }}
    QPushButton#dangerfill:disabled {{ background: {c['track']}; border-color: {c['track']}; color: {c['muted']}; }}
    QFrame#riskhead {{ background: {c['bad']}; border-radius: 10px; }}
    QFrame#riskhead QLabel {{ color: #ffffff; background: transparent; }}
    QFrame#riskbox {{ border: 2px solid {c['bad']}; border-radius: 10px; background: {c['surface']}; }}
    QFrame#riskbox QLabel {{ background: transparent; }}
    QCheckBox {{ spacing: 8px; }}
    QPushButton#danger {{ color: {c['bad']}; }}
    QPushButton#choice {{ text-align: left; padding: 12px 14px; border-radius: 10px; }}
    QPushButton#choice:checked {{ border: 2px solid {c['accent']}; background: {c['surface']}; }}

    QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: {c['track']}; }}
    QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ background: {c['surface']}; border: 2px solid {c['accent']};
                                  width: 16px; height: 16px; margin: -7px 0; border-radius: 9px; }}
    QSlider::handle:horizontal:hover {{ background: {c['hover']}; }}

    QComboBox {{ padding: 6px 10px; border-radius: 8px; border: 1px solid {c['border']}; background: {c['surface']}; }}
    QComboBox QAbstractItemView {{ background: {c['surface']}; selection-background-color: {c['accent']};
                                   selection-color: {c['accent_text']}; border: 1px solid {c['border']}; }}
    QPlainTextEdit {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 8px;
                      font-family: monospace; padding: 6px; }}
    QScrollBar:vertical {{ width: 10px; background: transparent; }}
    QScrollBar::handle:vertical {{ background: {c['track']}; border-radius: 5px; min-height: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QToolTip {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
    """
