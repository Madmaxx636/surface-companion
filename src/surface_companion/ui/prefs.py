"""Small, failure-tolerant wrapper around QSettings (the window must still work if storage is unavailable)."""
from __future__ import annotations

from PyQt6.QtCore import QSettings


def _s() -> QSettings:
    return QSettings("SurfaceCompanion", "SurfaceCompanion")


def get(key: str, default=None):
    try:
        v = _s().value(key, default)
        if isinstance(default, bool) and isinstance(v, str):
            return v.lower() == "true"
        return v
    except Exception:  # noqa: BLE001
        return default


def put(key: str, value) -> None:
    try:
        s = _s()
        s.setValue(key, value)
        s.sync()
    except Exception:  # noqa: BLE001
        pass
