"""Hotfix catalogue."""
from __future__ import annotations

from .base import Ctx, Fix
from . import catalog


def all_fixes() -> list[Fix]:
    return catalog.build()


def evaluate(ctx: Ctx) -> list[dict]:
    out = []
    for f in all_fixes():
        try:
            if not f.applies(ctx):
                continue
            status = f.detect(ctx)
            level = "recommended" if (f.level == "optional" and f.recommend and f.recommend(ctx)) else f.level
        except Exception as e:  # noqa: BLE001 - one broken probe must not hide the others
            status, level = "unknown", f.level
        out.append({"id": f.id, "title": f.title, "summary": f.summary, "category": f.category,
                    "source": f.source, "level": level, "status": status, "scope": f.scope,
                    "reboot": f.reboot, "revertable": f.revert is not None, "steps": f.steps,
                    "applicable": f.apply is not None,
                    "note": (f.note(ctx) if f.note else "")})
    return out


def get(fix_id: str) -> Fix:
    for f in all_fixes():
        if f.id == fix_id:
            return f
    raise KeyError(fix_id)
