"""Hotfix catalogue."""
from __future__ import annotations

from .base import Ctx, Fix
from . import catalog


def all_fixes() -> list[Fix]:
    return catalog.build()


def evaluate(ctx: Ctx) -> list[dict]:
    rows: dict[str, dict] = {}
    fixes: dict[str, Fix] = {}
    for f in all_fixes():
        try:
            if not f.applies(ctx):
                continue
            status = f.detect(ctx)
            level = "recommended" if (f.level == "optional" and f.recommend and f.recommend(ctx)) else f.level
            note = f.note(ctx) if f.note else ""
        except Exception as e:  # noqa: BLE001 - one broken probe must not hide the others
            status, level, note = "unknown", f.level, ""
        fixes[f.id] = f
        rows[f.id] = {"id": f.id, "title": f.title, "summary": f.summary, "category": f.category,
                      "source": f.source, "level": level, "status": status, "scope": f.scope,
                      "reboot": f.reboot, "revertable": f.revert is not None, "steps": f.steps,
                      "applicable": f.apply is not None, "note": note}
    # Fixes that exclude each other: if one is applied, the other is not "available" but "conflict".
    for fid, row in rows.items():
        if row["status"] != "available":
            continue
        for other in fixes[fid].conflicts:
            if other in rows and rows[other]["status"] == "applied":
                row["status"] = "conflict"
                row["note"] = (row["note"] + " " if row["note"] else "") + \
                    f"Conflicts with “{rows[other]['title']}”, which is applied. Undo that one first."
                break
    return list(rows.values())


def conflict_for(fix_id: str, ctx: Ctx) -> str:
    """Empty string if the fix can be applied, otherwise the reason it conflicts with an applied fix."""
    for row in evaluate(ctx):
        if row["id"] == fix_id and row["status"] == "conflict":
            return row["note"]
    return ""


def get(fix_id: str) -> Fix:
    for f in all_fixes():
        if f.id == fix_id:
            return f
    raise KeyError(fix_id)
