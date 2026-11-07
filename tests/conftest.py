import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from surface_companion import sysfs  # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A fake filesystem root; everything in the app reads through sysfs.ROOT."""
    monkeypatch.setattr(sysfs, "ROOT", tmp_path)
    return tmp_path


def put(root: Path, path: str, text: str):
    f = root / path.lstrip("/")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    return f
