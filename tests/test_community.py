import io
import tarfile

import pytest

from conftest import put
from surface_companion import device
from surface_companion.pkg import community


def mkdev(model, surface_kernel=True):
    return device.Device(vendor="Microsoft Corporation", model=model, is_surface=True,
                         surface_kernel=surface_kernel, kernel="6.19.8-surface-3")


def test_drivers_are_pinned_to_full_commit_hashes():
    for d in community.DRIVERS:
        assert len(d.commit) == 40 and set(d.commit) <= set("0123456789abcdef")
        assert d.commit in d.tarball and "/main" not in d.tarball and "/master" not in d.tarball
        assert d.risks, "every community driver must carry risks to show"


def test_drivers_match_only_their_models():
    ids = lambda m: [d.id for d in community.for_device(mkdev(m))]
    assert ids("Surface Pro 5") == ["sp5-cameras"]
    assert ids("Surface Pro (5th Gen)") == ["sp5-cameras"]
    assert ids("Surface Pro 7+") == ["sp7plus-cameras"]
    assert ids("Surface Pro 7") == []          # not the Pro 7+
    assert ids("Surface Laptop 3") == []


def _tar(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data, kind in members:
            ti = tarfile.TarInfo(name)
            if kind == "file":
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
            elif kind == "sym":
                ti.type, ti.linkname = tarfile.SYMTYPE, data
                tf.addfile(ti)
    buf.seek(0)
    return buf


def test_safe_extract_rejects_path_traversal_and_escaping_links(tmp_path):
    for members in ([("../evil", b"x", "file")], [("/abs", b"x", "file")],
                    [("top/l", "../../etc/passwd", "sym")]):
        with tarfile.open(fileobj=_tar(members), mode="r:gz") as tf:
            with pytest.raises(RuntimeError):
                community._safe_extract(tf, tmp_path / "x")


def test_fetch_downloads_pinned_commit_and_records_hash(tmp_path, monkeypatch):
    d = community.DRIVERS[0]
    data = _tar([("repo-abc/fix4-installer.sh", b"#!/bin/bash\n", "file")]).read()
    seen = {}

    class R:
        def __init__(self): self.b = io.BytesIO(data)
        def read(self, n=-1): return self.b.read(n)
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake(req, timeout=0):
        seen["url"] = req.full_url
        return R()
    monkeypatch.setattr(community.urllib.request, "urlopen", fake)
    logs = []
    src = community.fetch(d, tmp_path, logs.append)
    assert seen["url"] == d.tarball and (src / "fix4-installer.sh").exists()
    assert any("sha256" in l for l in logs)
    assert (tmp_path / d.id / d.commit / "SHA256").read_text().strip()
    # second call reuses the download
    community.fetch(d, tmp_path, logs.append)
    assert any("already downloaded" in l for l in logs)


def test_terminal_command_wraps_script_and_waits(monkeypatch):
    monkeypatch.delenv("TERMINAL", raising=False)
    monkeypatch.setattr(community.shutil, "which", lambda n: "/usr/bin/" + n if n == "konsole" else None)
    cmd = community.terminal_command(["bash", "/x/fix4-installer.sh"])
    assert cmd[:2] == ["konsole", "-e"] and cmd[-2:] == ["bash", "/x/fix4-installer.sh"] and "read -rp" in cmd[4]
    monkeypatch.setattr(community.shutil, "which", lambda n: None)
    assert community.terminal_command(["x"]) is None


def test_undo_uses_authors_flag():
    d = community.by_id("sp5-cameras")
    assert community.script_argv(d, community.Path("/s"), undo=True) == ["bash", "/s/fix4-installer.sh", "--rueckgaengig"]


def test_dkms_install_needs_surface_kernel_and_runs_each_module(tmp_path):
    d = community.by_id("sp7plus-cameras")
    calls = []
    run = lambda argv, log, cwd=None, check=True: calls.append((argv, cwd)) or 0
    with pytest.raises(RuntimeError, match="linux-surface kernel"):
        community.dkms_install(d, mkdev("Surface Pro 7+", surface_kernel=False), tmp_path, "debian", lambda l: None, run)
    community.dkms_install(d, mkdev("Surface Pro 7+"), tmp_path, "debian", lambda l: None, run)
    scripts = [a[0][1] for a in calls if a[0][0] == "bash"]
    assert [s.split("/")[-2] for s in scripts] == ["ov5693-surface", "ov7251-surface", "int3472-surface"]
    assert calls[0][0][:2] == ["apt-get", "install"]


def test_daemon_community_actions_enforce_model_and_acceptance(root, monkeypatch):
    from surface_companion import actions
    monkeypatch.setattr(actions, "get_device", lambda: mkdev("Surface Pro 7+"))
    a = actions.build(actions.Settings(path=root / "s.json"))
    with pytest.raises(ValueError, match="warning must be accepted"):
        a["install_community"].fn({"id": "sp7plus-cameras"}, lambda l: None)
    with pytest.raises(ValueError, match="not for this device"):
        a["install_community"].fn({"id": "sp5-cameras", "accept_risk": True}, lambda l: None)
    with pytest.raises(ValueError, match="runs as your user"):
        monkeypatch.setattr(actions, "get_device", lambda: mkdev("Surface Pro 5"))
        a["install_community"].fn({"id": "sp5-cameras", "accept_risk": True}, lambda l: None)
    with pytest.raises(KeyError):
        a["install_community"].fn({"id": "anything-else", "accept_risk": True}, lambda l: None)
    assert a["install_community"].polkit == actions.POLKIT_ADMIN
