import subprocess

import pytest

from conftest import put
from surface_companion import device, sysfs
from surface_companion.fixes import base, bootloader, catalog, evaluate
from surface_companion.fixes.base import Ctx
from surface_companion.pkg import distro as dist, planner


def mkdev(model, cpu="intel", surface_kernel=False, vendor="Microsoft Corporation", sku=""):
    d = device.Device(vendor=vendor, model=model, cpu_vendor=cpu, surface_kernel=surface_kernel, is_surface=True, sku=sku)
    for prefix, fam in device.FAMILIES:
        if model.startswith(prefix):
            d.family = fam
            break
    return d


def mkdistro(id_, family, version="", immutable=False, initramfs=""):
    return dist.Distro(id=id_, name=id_, version=version, family=family, immutable=immutable, initramfs=initramfs)


# ------------------------------------------------------------------ distro

def test_os_release_parsing():
    kv = dist.parse_os_release('ID=ubuntu\nID_LIKE="debian"\nVERSION_ID="26.04"\n# c\nPRETTY_NAME="Ubuntu 26.04"\n')
    assert kv["ID"] == "ubuntu" and kv["VERSION_ID"] == "26.04"


def test_detect_family_from_id_like(root):
    put(root, "/etc/os-release", 'ID=linuxmint\nID_LIKE="ubuntu debian"\nVERSION_ID="22"\n')
    assert dist.detect().family == "debian"
    put(root, "/etc/os-release", "ID=nobara\nID_LIKE=fedora\n")
    assert dist.detect().family == "fedora"
    put(root, "/etc/os-release", "ID=endeavouros\nID_LIKE=arch\n")
    assert dist.detect().family == "arch"


def test_libwacom_rule():
    assert not dist.libwacom_surface_ok(mkdistro("ubuntu", "debian", "26.04"))
    assert dist.libwacom_surface_ok(mkdistro("ubuntu", "debian", "24.04"))
    assert not dist.libwacom_surface_ok(mkdistro("debian", "debian", ""))      # testing/sid
    assert dist.libwacom_surface_ok(mkdistro("debian", "debian", "12"))
    assert dist.libwacom_surface_ok(mkdistro("fedora", "fedora", "43"))


# ------------------------------------------------------------------ install planning

def test_debian_plan_adds_repo_then_installs(root):
    plan = planner.plan_install(["kernel", "iptsd"], mkdev("Surface Pro 7"), mkdistro("ubuntu", "debian", "24.04"))
    kinds = [s.kind for s in plan.steps]
    assert kinds == ["key", "write", "cmd", "cmd"]
    assert plan.steps[1].path == "/etc/apt/sources.list.d/linux-surface.list"
    assert "signed-by=" in plan.steps[1].content
    assert plan.steps[-1].argv[:3] == ["apt-get", "install", "-y"]
    assert "linux-surface" in plan.steps[-1].argv and "iptsd" in plan.steps[-1].argv
    assert plan.reboot


def test_repo_not_added_twice(root):
    put(root, "/etc/apt/sources.list.d/linux-surface.list", "deb x")
    plan = planner.plan_install(["iptsd"], mkdev("Surface Pro 7"), mkdistro("ubuntu", "debian", "24.04"))
    assert [s.kind for s in plan.steps] == ["cmd"]


def test_libwacom_skipped_on_ubuntu_2604(root):
    plan = planner.plan_install(["iptsd", "libwacom"], mkdev("Surface Pro 7"), mkdistro("ubuntu", "debian", "26.04"))
    assert "libwacom-surface" not in plan.steps[-1].argv
    assert any("libwacom" in n.lower() for n in plan.notes)


def test_fedora_kernel_allows_erasing(root):
    plan = planner.plan_install(["kernel"], mkdev("Surface Pro 7"), mkdistro("fedora", "fedora", "43"))
    assert plan.steps[0].kind == "download"
    assert "--allowerasing" in plan.steps[-1].argv


def test_arch_does_full_upgrade_and_marvell_firmware(root):
    plan = planner.plan_install(["kernel"], mkdev("Surface Pro 5"), mkdistro("arch", "arch"))
    last = plan.steps[-1].argv
    assert last[:2] == ["pacman", "-Syu"] and "linux-firmware-marvell" in last
    assert any(s.argv[:2] == ["pacman-key", "--lsign-key"] for s in plan.steps if s.kind == "cmd")


def test_dtx_only_on_book_and_thermald_only_intel(root, monkeypatch):
    d = mkdistro("ubuntu", "debian", "24.04")
    plan = planner.plan_install(["dtx", "thermald"], mkdev("Surface Pro 9", cpu="amd"), d)
    assert plan.blockers and not plan.steps
    monkeypatch.setattr(planner, "active_session_users", lambda: [])
    plan = planner.plan_install(["dtx"], mkdev("Surface Book 2"), d)
    install = next(s for s in plan.steps if s.argv[:2] == ["apt-get", "install"])
    assert "surface-dtx-daemon" in install.argv


def test_unknown_package_manager_is_blocked(root):
    plan = planner.plan_install(["kernel"], mkdev("Surface Go 3"), mkdistro("void", "unknown"))
    assert plan.level == "blocked" and plan.blockers and not plan.steps


def test_non_x86_is_blocked(root, monkeypatch):
    monkeypatch.setattr(planner.platform, "machine", lambda: "aarch64")
    plan = planner.plan_install(["kernel"], mkdev("Surface Pro 9"), mkdistro("ubuntu", "debian", "26.04"))
    assert plan.level == "blocked" and "x86-64" in plan.blockers[0]


def test_supported_systems_need_no_warning(root):
    assert planner.assess(mkdev("Surface Go 3"), mkdistro("ubuntu", "debian", "26.04"))["level"] == "supported"
    assert planner.assess(mkdev("Surface Go 3"), mkdistro("linuxmint", "debian", "22"))["level"] == "supported"


def test_guessed_distro_is_unsupported_but_plans_steps(root):
    d = mkdistro("odd", "debian")
    d.guessed = True
    plan = planner.plan_install(["iptsd"], mkdev("Surface Pro 7"), d)
    assert plan.level == "unsupported" and plan.steps and any("not recognised" in r for r in plan.risks)


def test_tumbleweed_plan_and_leap_blocked(root):
    tw = mkdistro("opensuse-tumbleweed", "suse")
    tw.tumbleweed = True
    plan = planner.plan_install(["kernel", "iptsd"], mkdev("Surface Pro 7"), tw)
    assert plan.level == "unsupported"
    cmds = [" ".join(s.argv) for s in plan.steps]
    assert any("addrepo" in c and "TaivasJumala" in c for c in cmds)
    assert any("-r Linux Surface (openSUSE_Tumbleweed) kernel-default" in c for c in cmds)
    assert any(c.endswith("install iptsd") for c in cmds)
    assert any("volunteer" in r for r in plan.risks)
    leap = planner.plan_install(["kernel"], mkdev("Surface Pro 7"), mkdistro("opensuse-leap", "suse", "15.6"))
    assert leap.level == "blocked"


def test_immutable_fedora_uses_wiki_recipe_with_warning(root):
    plan = planner.plan_install(["kernel", "secureboot"], mkdev("Surface Book 2"),
                                mkdistro("bazzite", "fedora", immutable=True))
    assert plan.level == "unsupported"
    cmds = [" ".join(s.argv) for s in plan.steps if s.kind == "cmd"]
    assert cmds[0].startswith("rpm-ostree override replace /var/tmp/surface-companion-kernel.rpm")
    assert "--install kernel-surface" in cmds[0] and "--remove kernel-core" in cmds[0]
    assert cmds[-1] == "rpm-ostree install surface-secureboot"        # on its own, as the wiki asks
    assert any("Surface Book 2" in r for r in plan.risks)


def test_daemon_refuses_unsupported_install_without_acceptance(root, monkeypatch):
    from surface_companion import actions
    monkeypatch.setattr(actions, "get_device", lambda: mkdev("Surface Pro 7"))
    d = mkdistro("odd", "debian")
    d.guessed = True
    monkeypatch.setattr(actions, "get_distro", lambda: d)
    act = actions.build(actions.Settings(path=root / "s.json"))["install"]
    with pytest.raises(ValueError, match="warning must be accepted"):
        act.fn({"ids": ["iptsd"]}, lambda l: None)
    assert act.fn({"ids": ["iptsd"], "accept_unsupported": True, "dry_run": True}, lambda l: None)


def test_component_status_marks_everything_unavailable_when_blocked(root):
    rows = planner.component_status(mkdev("Surface Go 3"), mkdistro("void", "unknown"))
    assert rows and not any(r["installable"] for r in rows)


def test_update_plans(root):
    assert "--only-upgrade" in planner.plan_update(mkdistro("a", "debian"), ["iptsd"]).steps[0].argv
    assert planner.plan_update(mkdistro("a", "arch"), ["iptsd"]).steps[0].argv == ["pacman", "-Syu", "--noconfirm"]
    assert planner.plan_update(mkdistro("a", "debian"), []).blockers


def test_dry_run_changes_nothing(root, tmp_path):
    plan = planner.plan_install(["iptsd"], mkdev("Surface Pro 7"), mkdistro("ubuntu", "debian", "24.04"))
    lines = []
    assert planner.run_plan(plan, lines.append, dry_run=True)
    assert not (tmp_path / "etc/apt").exists()
    assert any("apt-get install" in l for l in lines)


def test_write_step_and_append_step(tmp_path):
    f = tmp_path / "x" / "y.list"
    assert planner.run_step(planner.Step("w", "write", path=str(f), content="hi\n"), lambda l: None)
    assert f.read_text() == "hi\n"
    conf = tmp_path / "pacman.conf"
    conf.write_text("[core]\n")
    s = planner.Step("a", "append", path=str(conf), content="\n[linux-surface]\nServer = x\n")
    planner.run_step(s, lambda l: None)
    planner.run_step(s, lambda l: None)
    assert conf.read_text().count("[linux-surface]") == 1


def test_signing_key_with_wrong_fingerprint_is_refused(monkeypatch, tmp_path):
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self): return b"not a key"
    monkeypatch.setattr(planner.urllib.request, "urlopen", lambda *a, **k: R())
    monkeypatch.setattr(planner, "_fingerprint_ok", lambda f: False)
    out = tmp_path / "k.gpg"
    lines = []
    assert not planner.run_step(planner.Step("k", "key", path=str(out)), lines.append)
    assert not out.exists() and any("refusing" in l for l in lines)


# ------------------------------------------------------------------ fixes

def test_managed_files_are_never_clobbered(root):
    put(root, "/etc/x.conf", "mine")
    with pytest.raises(RuntimeError):
        base.write_managed("/etc/x.conf", "ours")
    assert (root / "etc/x.conf").read_text() == "mine"
    base.write_managed("/etc/y.conf", "ours")
    assert base.is_managed("/etc/y.conf")
    base.remove_managed("/etc/x.conf")                       # not ours: untouched
    assert (root / "etc/x.conf").exists()
    base.remove_managed("/etc/y.conf")
    assert not (root / "etc/y.conf").exists()


def _fake_run(monkeypatch, known=()):
    def run(argv, timeout=10, **kw):
        out = "ok" if argv[:1] == ["modinfo"] and argv[-1] in known else ""
        rc = 0 if (argv[:1] != ["modinfo"] or argv[-1] in known) else 1
        return subprocess.CompletedProcess(argv, rc, out + "\n" if out else "", "")
    monkeypatch.setattr(sysfs, "run", run)
    monkeypatch.setattr(sysfs, "have", lambda c: False)


def test_catalog_selects_by_model(root, monkeypatch):
    _fake_run(monkeypatch)
    ids = lambda m: {f["id"] for f in evaluate(Ctx(mkdev(m), mkdistro("ubuntu", "debian", "24.04")))}
    assert "s5-shutdown" in ids("Surface Pro 9")
    assert "s5-shutdown" not in ids("Surface Pro 7")
    assert "ipu3-firmware" in ids("Surface Go 3") and "ipu3-firmware" not in ids("Surface Pro 8")
    assert not {f["id"] for f in evaluate(Ctx(device.Device(vendor="Dell", model="XPS"), mkdistro("x", "debian")))}


def test_reboot_pci_only_on_listed_models_and_needs_bootloader(root, monkeypatch):
    _fake_run(monkeypatch)
    put(root, "/etc/default/grub.d/.keep", "")
    monkeypatch.setattr(sysfs, "have", lambda c: c == "update-grub")
    c = lambda m: [f["id"] for f in evaluate(Ctx(mkdev(m), mkdistro("ubuntu", "debian", "24.04")))]
    assert "reboot-pci" in c("Surface Laptop 3") and "reboot-pci" in c("Surface Book 3")
    assert "reboot-pci" not in c("Surface Pro 7")


def test_reboot_pci_grub_dropin_roundtrip(root, monkeypatch):
    put(root, "/etc/default/grub.d/.keep", "")
    cmds = []
    monkeypatch.setattr(bootloader, "sh", lambda argv, log, timeout=300: cmds.append(argv))
    monkeypatch.setattr(sysfs, "have", lambda c: c == "update-grub")
    bootloader.add("reboot=pci", lambda l: None)
    f = root / "etc/default/grub.d/50-surface-companion.cfg"
    assert "reboot=pci" in f.read_text() and cmds == [["update-grub"]]
    bootloader.remove("reboot=pci", lambda l: None)
    assert not f.exists()


def test_initramfs_tools_block_added_and_removed_cleanly(root, monkeypatch):
    put(root, "/etc/initramfs-tools/modules", "# user stuff\nvfat\n")
    monkeypatch.setattr(catalog, "sh", lambda *a, **k: None)
    monkeypatch.setattr(catalog, "_initramfs_modules", lambda ctx: ["surface_aggregator", "8250_dw"])
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("debian", "debian", "12", initramfs="initramfs-tools"))
    catalog._initramfs_apply(ctx, lambda l: None)
    catalog._initramfs_apply(ctx, lambda l: None)           # idempotent
    txt = (root / "etc/initramfs-tools/modules").read_text()
    assert txt.count("BEGIN surface-companion") == 1 and "8250_dw" in txt and "vfat" in txt
    assert catalog._initramfs_detect(ctx) == "applied"
    catalog._initramfs_revert(ctx, lambda l: None)
    txt = (root / "etc/initramfs-tools/modules").read_text()
    assert "surface_aggregator" not in txt and "vfat" in txt


def test_initramfs_modules_adapt_to_cpu_and_model(root, monkeypatch):
    _fake_run(monkeypatch, known={"surface_aggregator", "surface_hid", "surface_hid_core", "intel_lpss_pci",
                                  "pinctrl_icelake", "pinctrl_amd", "8250_dw", "surface_kbd"})
    m = catalog._initramfs_modules(Ctx(mkdev("Surface Laptop 3"), mkdistro("a", "debian")))
    assert "pinctrl_icelake" in m and "pinctrl_amd" not in m and "surface_hid" in m
    m = catalog._initramfs_modules(Ctx(mkdev("Surface Laptop 4", cpu="amd"), mkdistro("a", "debian")))
    assert "pinctrl_amd" in m and "intel_lpss_pci" not in m
    m = catalog._initramfs_modules(Ctx(mkdev("Surface Laptop 2"), mkdistro("a", "debian")))
    assert "surface_kbd" in m and "surface_hid" not in m


def _kernel(root, name, with_image=True, with_initrd=None):
    put(root, f"/usr/lib/modules/{name}/modules.dep", "")
    if with_image:
        put(root, f"/boot/vmlinuz-{name}", "")
    if with_initrd:
        put(root, with_initrd.format(k=name), "")


def test_initramfs_tool_is_the_one_that_builds_the_images_not_just_an_installed_one(root, monkeypatch):
    from surface_companion.pkg import distro as dist
    both = lambda c: c in ("dracut", "update-initramfs")
    monkeypatch.setattr(sysfs, "have", both)
    put(root, "/boot/initrd.img-7.0.0-43-generic", "")          # Ubuntu layout: built by update-initramfs
    assert dist.detect_initramfs() == "initramfs-tools"
    (root / "boot/initrd.img-7.0.0-43-generic").unlink()
    put(root, "/boot/initramfs-6.9.0.fc41.img", "")             # Fedora layout
    monkeypatch.setattr(sysfs, "have", lambda c: c == "dracut")
    assert dist.detect_initramfs() == "dracut"
    monkeypatch.setattr(sysfs, "have", lambda c: c == "mkinitcpio")
    assert dist.detect_initramfs() == "mkinitcpio"


def test_dracut_skips_leftover_kernel_folders_and_rebuilds_each_real_kernel(root, monkeypatch):
    _kernel(root, "6.14.0-15-generic", with_image=False)         # leftover from a removed kernel: no image
    _kernel(root, "7.2.15-1-surface", with_initrd="/boot/initramfs-{k}.img")
    _kernel(root, "7.0.0-43-generic", with_initrd="/boot/initramfs-{k}.img")
    calls = []
    monkeypatch.setattr(catalog, "sh", lambda argv, log, t=300: calls.append(argv))
    ctx = Ctx(mkdev("Surface Go 3", surface_kernel=True), mkdistro("f", "fedora", initramfs="dracut"))
    ctx.dev.kernel = "7.2.15-1-surface"
    catalog._rebuild_initramfs(ctx, "dracut", lambda l: None)
    assert all(c[:3] == ["dracut", "--force", "--kver"] for c in calls)
    assert [c[3] for c in calls] == ["7.0.0-43-generic", "7.2.15-1-surface"]
    assert not any("6.14" in " ".join(c) for c in calls) and "--regenerate-all" not in sum(calls, [])


def test_dracut_failure_on_one_old_kernel_does_not_fail_the_fix(root, monkeypatch):
    _kernel(root, "7.2.15-1-surface", with_initrd="/boot/initramfs-{k}.img")
    _kernel(root, "7.0.0-43-generic", with_initrd="/boot/initramfs-{k}.img")

    def sh(argv, log, t=300):
        if "7.0.0-43-generic" in argv:
            raise RuntimeError("boom")
    monkeypatch.setattr(catalog, "sh", sh)
    ctx = Ctx(mkdev("Surface Go 3"), mkdistro("f", "fedora", initramfs="dracut"))
    ctx.dev.kernel = "7.2.15-1-surface"
    catalog._rebuild_initramfs(ctx, "dracut", lambda l: None)           # no raise
    ctx.dev.kernel = "7.0.0-43-generic"
    with pytest.raises(RuntimeError, match="running kernel"):          # the running kernel failing is fatal
        catalog._rebuild_initramfs(ctx, "dracut", lambda l: None)


def test_failed_rebuild_rolls_the_config_back(root, monkeypatch):
    monkeypatch.setattr(catalog, "_initramfs_modules", lambda ctx: ["a", "b"])

    def sh(argv, log, t=300):
        raise RuntimeError("update-initramfs failed")
    monkeypatch.setattr(catalog, "sh", sh)
    ctx = Ctx(mkdev("Surface Go 3"), mkdistro("d", "debian", initramfs="initramfs-tools"))
    put(root, "/etc/initramfs-tools/modules", "vfat\n")
    with pytest.raises(RuntimeError):
        catalog._initramfs_apply(ctx, lambda l: None)
    assert "surface-companion" not in (root / "etc/initramfs-tools/modules").read_text()
    assert catalog._initramfs_detect(ctx) == "available"
    ctx = Ctx(mkdev("Surface Go 3"), mkdistro("f", "fedora", initramfs="dracut"))
    with pytest.raises(RuntimeError):
        catalog._initramfs_apply(ctx, lambda l: None)
    assert not (root / "etc/dracut.conf.d/50-surface-companion.conf").exists()


def test_dracut_and_mkinitcpio_config_written(root, monkeypatch):
    monkeypatch.setattr(catalog, "_rebuild_initramfs", lambda ctx, kind, log: None)
    monkeypatch.setattr(catalog, "_initramfs_modules", lambda ctx: ["a", "b"])
    catalog._initramfs_apply(Ctx(mkdev("Surface Go 3"), mkdistro("f", "fedora", initramfs="dracut")), lambda l: None)
    assert 'add_drivers+=" a b "' in (root / "etc/dracut.conf.d/50-surface-companion.conf").read_text()
    catalog._initramfs_apply(Ctx(mkdev("Surface Go 3"), mkdistro("a", "arch", initramfs="mkinitcpio")), lambda l: None)
    assert "MODULES+=(a b)" in (root / "etc/mkinitcpio.conf.d/50-surface-companion.conf").read_text()


def test_undo_cleans_a_leftover_from_a_different_tool_without_rebuilding(root, monkeypatch):
    base.write_managed("/etc/dracut.conf.d/50-surface-companion.conf", 'add_drivers+=" a "\n')
    calls = []
    monkeypatch.setattr(catalog, "sh", lambda argv, log, t=300: calls.append(argv))
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("u", "debian", initramfs="initramfs-tools"))
    assert catalog._initramfs_detect(ctx) == "applied"                   # shows up so it can be undone
    catalog._initramfs_revert(ctx, lambda l: None)
    assert not (root / "etc/dracut.conf.d/50-surface-companion.conf").exists() and calls == []
    assert catalog._initramfs_detect(ctx) == "available"


def test_note_says_the_fix_is_not_needed_without_an_encrypted_disk(root):
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("u", "debian", initramfs="initramfs-tools"))
    put(root, "/etc/crypttab", "# nothing here\n")
    assert "Only needed with an encrypted disk" in catalog._initramfs_note(ctx)
    assert not catalog._encrypted(ctx)
    put(root, "/etc/crypttab", "# c\ncryptroot UUID=abc none luks\n")
    assert catalog._encrypted(ctx) and catalog._initramfs_note(ctx) == ""


# ------------------------------------------------------------------ repository detection and update check

def test_blank_leftover_repo_file_is_not_a_repo(root):
    put(root, "/etc/apt/sources.list.d/linux-surface.list", "\n\n\n\n")
    d = mkdistro("ubuntu", "debian", "26.04")
    assert not planner.repo_present(d)
    assert planner.system_status(mkdev("Surface Laptop 3"), d)["repo_leftover"]


def test_commented_out_repo_is_not_a_repo(root):
    put(root, "/etc/apt/sources.list.d/linux-surface.list", "# deb [arch=amd64] https://pkg.surfacelinux.com/debian release main\n")
    assert not planner.repo_present(mkdistro("ubuntu", "debian", "26.04"))


def test_active_list_line_and_deb822_source_count(root):
    d = mkdistro("ubuntu", "debian", "26.04")
    put(root, "/etc/apt/sources.list.d/a.list", "deb [arch=amd64] https://pkg.surfacelinux.com/debian release main\n")
    assert planner.repo_present(d)
    (root / "etc/apt/sources.list.d/a.list").unlink()
    put(root, "/etc/apt/sources.list.d/b.sources",
        "Types: deb\nURIs: https://pkg.surfacelinux.com/debian\nSuites: release\nComponents: main\n")
    assert planner.repo_present(d)
    put(root, "/etc/apt/sources.list.d/b.sources",
        "Types: deb\nURIs: https://pkg.surfacelinux.com/debian\nSuites: release\nComponents: main\nEnabled: no\n")
    assert not planner.repo_present(d)


def test_install_replans_repo_when_file_is_blank(root):
    put(root, "/etc/apt/sources.list.d/linux-surface.list", "\n")
    plan = planner.plan_install(["kernel"], mkdev("Surface Pro 7"), mkdistro("ubuntu", "debian", "26.04"))
    assert [s.kind for s in plan.steps][:2] == ["key", "write"]


def test_update_check_warns_when_repo_missing(root, monkeypatch):
    monkeypatch.setattr(sysfs, "run", lambda argv, timeout=10, **kw: subprocess.CompletedProcess(argv, 0, "", ""))
    res = planner.check_updates(mkdistro("ubuntu", "debian", "26.04"))
    assert res["updates"] == [] and "not set up" in res["warnings"][0]


def test_update_check_finds_new_kernel_and_reports_refresh_errors(root, monkeypatch):
    put(root, "/etc/apt/sources.list.d/linux-surface.list", "deb https://pkg.surfacelinux.com/debian release main\n")

    def run(argv, timeout=10, **kw):
        if argv[:2] == ["apt-get", "update"]:
            return subprocess.CompletedProcess(argv, 100, "", "W: Failed to fetch https://pkg.surfacelinux.com/debian/dists/release/InRelease")
        if argv[:2] == ["apt", "list"]:
            return subprocess.CompletedProcess(argv, 0, "Listing...\nlinux-surface/release 7.2.15-1 all [upgradable from: 6.19.8-surface-3]\n"
                                               "linux-image-surface/release 7.2.15-1 amd64 [upgradable from: 6.19.8-surface-3]\n"
                                               "vim/noble 9.1 amd64 [upgradable from: 9.0]\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(sysfs, "run", run)
    res = planner.check_updates(mkdistro("ubuntu", "debian", "26.04"))
    assert [u["name"] for u in res["updates"]] == ["linux-surface", "linux-image-surface"]
    assert res["updates"][0]["available"] == "7.2.15-1"
    assert any("surfacelinux" in w for w in res["warnings"])


def test_setup_repo_action_runs_repo_steps_and_is_admin_only(root, monkeypatch):
    from surface_companion import actions
    monkeypatch.setattr(actions, "get_device", lambda: mkdev("Surface Laptop 3"))
    monkeypatch.setattr(actions, "get_distro", lambda: mkdistro("ubuntu", "debian", "26.04"))
    ran = []
    monkeypatch.setattr(planner, "run_plan", lambda plan, log, dry_run=False: ran.append([s.kind for s in plan.steps]) or True)
    act = actions.build(actions.Settings(path=root / "s.json"))["setup_repo"]
    assert act.polkit == actions.POLKIT_ADMIN and act.job
    act.fn({}, lambda l: None)
    assert ran == [["key", "write", "cmd"]]


# ================================================================== wake, PSR and keyboard-wake fixes

import os  # noqa: E402

from surface_companion import fixes as fixes_pkg  # noqa: E402


def ids_of(model, root, monkeypatch, sku="", cpu="intel", have=("update-grub",), modules=("surface_gpe",)):
    """Fix ids offered for a model, in a fake root where surface_gpe exists and a grub drop-in directory is present."""
    _fake_run(monkeypatch, known=modules)
    put(root, "/etc/default/grub.d/.keep", "")
    monkeypatch.setattr(sysfs, "have", lambda c: c in have)
    ctx = Ctx(mkdev(model, cpu=cpu, sku=sku), mkdistro("ubuntu", "debian", "26.04"))
    return {r["id"]: r for r in fixes_pkg.evaluate(ctx)}


def load_gpe(root, loaded=True, blacklist=None, wakeup=None):
    put(root, "/proc/modules", "surface_gpe 16384 0 - Live 0x0\n" if loaded else "")
    if blacklist:
        put(root, "/etc/modprobe.d/zz-user.conf", blacklist)
    if wakeup is not None:
        put(root, "/sys/bus/platform/devices/surface_gpe/power/wakeup", wakeup)


# ---------------------------------------------------------------- 1. gpe-lid only for Pro 4 / Pro 5

def test_gpe_lid_is_not_offered_on_the_surface_laptop_3(root, monkeypatch):
    for model in ("Surface Laptop 3", "Surface Book 2", "Surface Pro 6", "Surface Pro 7", "Surface Go 3"):
        assert "gpe-lid" not in ids_of(model, root, monkeypatch), model


def test_gpe_lid_is_offered_and_recommended_on_pro_4_and_5(root, monkeypatch):
    for model, sku in (("Surface Pro 4", ""), ("Surface Pro 5", ""), ("Surface Pro (5th Gen)", ""),
                       ("Surface Pro", "Surface_Pro_1796"), ("Surface Pro", "Surface_Pro_1807")):
        r = ids_of(model, root, monkeypatch, sku=sku)["gpe-lid"]
        assert r["level"] == "recommended" and r["status"] == "available", (model, sku)
    assert "gpe-lid" not in ids_of("Surface Pro", root, monkeypatch, sku="Surface_Pro_1850")      # some other plain "Surface Pro"


def test_an_already_applied_gpe_lid_stays_visible_so_it_can_be_undone(root, monkeypatch):
    base.write_managed(catalog.GPE_CONF, "blacklist surface_gpe\n")
    r = ids_of("Surface Laptop 3", root, monkeypatch)["gpe-lid"]
    assert r["status"] == "applied" and r["revertable"] and "not a Surface Pro 4 or 5" in r["note"]
    catalog.remove_managed(catalog.GPE_CONF)
    assert "gpe-lid" not in ids_of("Surface Laptop 3", root, monkeypatch)


# ---------------------------------------------------------------- 2. gpe-lid-wake

def test_gpe_lid_wake_is_offered_everywhere_except_pro_4_and_5_and_recommended_on_the_listed_models(root, monkeypatch):
    for model in ("Surface Laptop", "Surface Laptop 2", "Surface Laptop 3", "Surface Book 2", "Surface Book 3",
                  "Surface Pro 6", "Surface Pro 7"):
        assert ids_of(model, root, monkeypatch)["gpe-lid-wake"]["level"] == "recommended", model
    for model in ("Surface Laptop 4", "Surface Go 3", "Surface Pro 8"):
        assert ids_of(model, root, monkeypatch)["gpe-lid-wake"]["level"] == "optional", model
    for model in ("Surface Pro 4", "Surface Pro 5"):
        assert "gpe-lid-wake" not in ids_of(model, root, monkeypatch), model
    assert "gpe-lid-wake" not in ids_of("Surface Laptop 3", root, monkeypatch, modules=())        # no surface_gpe module


def test_gpe_lid_and_gpe_lid_wake_exclude_each_other(root, monkeypatch):
    load_gpe(root, loaded=False)
    base.write_managed(catalog.GPE_CONF, "blacklist surface_gpe\n")                   # gpe-lid applied
    rows = ids_of("Surface Laptop 3", root, monkeypatch)
    assert rows["gpe-lid"]["status"] == "applied"
    assert rows["gpe-lid-wake"]["status"] == "conflict" and "Undo that one first" in rows["gpe-lid-wake"]["note"]
    catalog.remove_managed(catalog.GPE_CONF)
    rows = ids_of("Surface Laptop 3", root, monkeypatch)
    assert rows["gpe-lid-wake"]["status"] == "available"


def test_conflict_is_reported_from_either_side_and_only_when_the_other_is_applied(root, monkeypatch):
    mk = lambda i, status, conflicts: base.Fix(i, i.upper(), "s", "c", "u", "optional", applies=lambda c: True,
                                               detect=lambda c: status, apply=lambda c, l: None, conflicts=conflicts)
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    for a_status, b_status, want_a, want_b in (("applied", "available", "applied", "conflict"),
                                               ("available", "applied", "conflict", "applied"),
                                               ("available", "available", "available", "available"),
                                               ("applied", "applied", "applied", "applied")):
        monkeypatch.setattr(fixes_pkg, "all_fixes", lambda: [mk("a", a_status, ("b",)), mk("b", b_status, ("a",))])
        rows = {r["id"]: r["status"] for r in fixes_pkg.evaluate(ctx)}
        assert (rows["a"], rows["b"]) == (want_a, want_b), (a_status, b_status)


def test_daemon_side_conflict_check(root, monkeypatch):
    base.write_managed(catalog.GPE_CONF, "blacklist surface_gpe\n")
    load_gpe(root, loaded=False)
    _fake_run(monkeypatch, known=("surface_gpe",))
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    assert "Undo that one first" in fixes_pkg.conflict_for("gpe-lid-wake", ctx)
    assert fixes_pkg.conflict_for("reboot-pci", ctx) == ""
    with pytest.raises(RuntimeError, match="Undo .*first"):
        catalog._gpe_wake_apply(ctx, lambda l: None)               # and the fix itself refuses too


def test_gpe_lid_wake_detect_counts_a_loaded_unblacklisted_module_even_if_the_files_are_not_ours(root, monkeypatch):
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    load_gpe(root, loaded=True)                                    # this SL3: loaded, kernel offers no wakeup file
    assert catalog._gpe_wake_detect(ctx) == "applied"
    put(root, "/sys/bus/platform/devices/surface_gpe/power/wakeup", "enabled")
    assert catalog._gpe_wake_detect(ctx) == "applied"
    put(root, "/sys/bus/platform/devices/surface_gpe/power/wakeup", "disabled")
    assert catalog._gpe_wake_detect(ctx) == "available"
    (root / "sys/bus/platform/devices/surface_gpe/power/wakeup").unlink()
    (root / "proc/modules").write_text("")
    assert catalog._gpe_wake_detect(ctx) == "available"           # not loaded
    load_gpe(root, loaded=True, blacklist="# blacklist surface_gpe\nblacklist surface_gpe\n")
    assert catalog._gpe_wake_detect(ctx) == "available"           # loaded by hand but blacklisted: gone after a reboot
    (root / "etc/modprobe.d/zz-user.conf").write_text("# blacklist surface_gpe\n")        # commented out does not count
    assert catalog._gpe_wake_detect(ctx) == "applied"


def test_gpe_lid_wake_apply_writes_both_files_loads_the_module_and_sets_the_flag(root, monkeypatch):
    calls, logs = [], []
    monkeypatch.setattr(catalog, "sh", lambda argv, log, timeout=300: calls.append(argv))
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    put(root, "/sys/bus/platform/devices/surface_gpe/power/wakeup", "disabled")
    catalog._gpe_wake_apply(ctx, logs.append)
    assert calls == [["modprobe", "surface_gpe"]]
    assert (root / "etc/modules-load.d/50-surface-companion-gpe.conf").read_text().endswith("surface_gpe\n")
    rule = (root / "etc/udev/rules.d/50-surface-companion-lid-wake.rules").read_text()
    assert 'ACTION=="add", SUBSYSTEM=="platform", KERNEL=="surface_gpe", ATTR{power/wakeup}="enabled"' in rule
    assert base.is_managed(catalog.GPE_LOAD_CONF) and base.is_managed(catalog.GPE_UDEV)
    assert (root / "sys/bus/platform/devices/surface_gpe/power/wakeup").read_text() == "enabled"


def test_gpe_lid_wake_apply_without_a_wakeup_file_still_succeeds_and_says_why(root, monkeypatch):
    logs = []
    monkeypatch.setattr(catalog, "sh", lambda argv, log, timeout=300: None)
    catalog._gpe_wake_apply(Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04")), logs.append)
    assert any("no power/wakeup" in l for l in logs)


def test_gpe_lid_wake_rolls_back_if_modprobe_fails_and_never_clobbers_a_foreign_file(root, monkeypatch):
    def boom(argv, log, timeout=300):
        raise RuntimeError("modprobe failed (1)")
    monkeypatch.setattr(catalog, "sh", boom)
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    with pytest.raises(RuntimeError, match="modprobe failed"):
        catalog._gpe_wake_apply(ctx, lambda l: None)
    assert not (root / "etc/modules-load.d/50-surface-companion-gpe.conf").exists()
    assert not (root / "etc/udev/rules.d/50-surface-companion-lid-wake.rules").exists()
    put(root, "/etc/udev/rules.d/50-surface-companion-lid-wake.rules", "# my own rule\n")      # someone else's file, same name
    monkeypatch.setattr(catalog, "sh", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="not overwriting"):
        catalog._gpe_wake_apply(ctx, lambda l: None)
    assert (root / "etc/udev/rules.d/50-surface-companion-lid-wake.rules").read_text() == "# my own rule\n"
    assert not (root / "etc/modules-load.d/50-surface-companion-gpe.conf").exists()               # rolled back too


def test_gpe_lid_wake_revert_removes_only_our_files(root, monkeypatch):
    monkeypatch.setattr(catalog, "sh", lambda *a, **k: None)
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    catalog._gpe_wake_apply(ctx, lambda l: None)
    put(root, "/etc/udev/rules.d/99-surface-lid-wake.rules", "# the one I made by hand\n")
    put(root, "/etc/modules-load.d/surface-gpe.conf", "surface_gpe\n")
    catalog._gpe_wake_revert(ctx, lambda l: None)
    assert not (root / "etc/modules-load.d/50-surface-companion-gpe.conf").exists()
    assert not (root / "etc/udev/rules.d/50-surface-companion-lid-wake.rules").exists()
    assert (root / "etc/udev/rules.d/99-surface-lid-wake.rules").exists() and (root / "etc/modules-load.d/surface-gpe.conf").exists()


# ---------------------------------------------------------------- 3. psr-off and the bootloader helper

def grub_world(root, monkeypatch, default_line='GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"', cmdline="quiet splash"):
    put(root, "/etc/default/grub", default_line + "\n")
    put(root, "/etc/default/grub.d/.keep", "")
    put(root, "/proc/cmdline", cmdline + "\n")
    monkeypatch.setattr(sysfs, "have", lambda c: c == "update-grub")
    cmds = []
    monkeypatch.setattr(bootloader, "sh", lambda argv, log, timeout=300: cmds.append(argv))
    return cmds


def test_psr_off_detect_from_the_running_cmdline_or_the_bootloader_config(root, monkeypatch):
    grub_world(root, monkeypatch)
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    assert catalog._psr_detect(ctx) == "available"
    put(root, "/proc/cmdline", "quiet splash i915.enable_psr=0\n")
    assert catalog._psr_detect(ctx) == "applied"                              # running now
    put(root, "/proc/cmdline", "quiet splash\n")
    put(root, "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash i915.enable_psr=0 "\n')   # added by hand
    assert catalog._psr_detect(ctx) == "applied"
    put(root, "/etc/default/grub", '#GRUB_CMDLINE_LINUX_DEFAULT="quiet i915.enable_psr=0"\nGRUB_CMDLINE_LINUX_DEFAULT="quiet"\n')
    assert catalog._psr_detect(ctx) == "available"                            # a commented-out line does not count
    put(root, "/etc/default/grub", 'GRUB_CMDLINE_LINUX_DEFAULT="quiet i915.enable_psr=01"\n')
    assert catalog._psr_detect(ctx) == "available"                            # a longer, different value does not count


def test_psr_off_applies_only_to_intel_with_a_manageable_bootloader_and_is_recommended_on_the_sl3(root, monkeypatch):
    grub_world(root, monkeypatch)
    _fake_run(monkeypatch)
    monkeypatch.setattr(sysfs, "have", lambda c: c == "update-grub")
    rows = lambda model, cpu="intel": {r["id"]: r for r in fixes_pkg.evaluate(
        Ctx(mkdev(model, cpu=cpu), mkdistro("ubuntu", "debian", "26.04")))}
    assert rows("Surface Laptop 3")["psr-off"]["level"] == "recommended"
    assert rows("Surface Laptop 3")["psr-off"]["reboot"] is True
    assert rows("Surface Pro 7")["psr-off"]["level"] == "optional"
    assert "psr-off" not in rows("Surface Laptop 3", cpu="amd")
    monkeypatch.setattr(sysfs, "have", lambda c: False)
    (root / "etc/default/grub.d/.keep").unlink()
    (root / "etc/default/grub.d").rmdir()
    (root / "etc/default/grub").unlink()
    assert "psr-off" not in rows("Surface Laptop 3")                           # no bootloader we know how to edit


def test_psr_off_apply_keeps_reboot_pci_in_the_shared_dropin_and_revert_removes_only_itself(root, monkeypatch):
    cmds = grub_world(root, monkeypatch)
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    bootloader.add("reboot=pci", lambda l: None)
    catalog._psr_apply(ctx, lambda l: None)
    drop = (root / "etc/default/grub.d/50-surface-companion.cfg").read_text()
    assert drop.count("GRUB_CMDLINE_LINUX_DEFAULT=") == 2 and "reboot=pci" in drop and "i915.enable_psr=0" in drop
    assert cmds == [["update-grub"], ["update-grub"]]
    catalog._psr_revert(ctx, lambda l: None)
    drop = (root / "etc/default/grub.d/50-surface-companion.cfg").read_text()
    assert "reboot=pci" in drop and "enable_psr" not in drop                   # reboot=pci survives
    bootloader.remove("reboot=pci", lambda l: None)
    assert not (root / "etc/default/grub.d/50-surface-companion.cfg").exists()  # last argument gone: file removed


def test_psr_off_does_not_duplicate_an_argument_you_already_set_and_will_not_undo_it(root, monkeypatch):
    cmds = grub_world(root, monkeypatch, default_line='GRUB_CMDLINE_LINUX_DEFAULT="quiet i915.enable_psr=0 "')
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    catalog._psr_apply(ctx, lambda l: None)
    assert cmds == [] and not (root / "etc/default/grub.d/50-surface-companion.cfg").exists()
    with pytest.raises(RuntimeError, match="edited yourself"):
        catalog._psr_revert(ctx, lambda l: None)
    assert "i915.enable_psr=0" in (root / "etc/default/grub").read_text()       # untouched


def test_dropin_files_we_did_not_write_are_never_overwritten(root, monkeypatch):
    grub_world(root, monkeypatch)
    put(root, "/etc/default/grub.d/50-surface-companion.cfg", 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT mine=1"\n')
    with pytest.raises(RuntimeError, match="not overwriting"):
        bootloader.add("i915.enable_psr=0", lambda l: None)
    assert "mine=1" in (root / "etc/default/grub.d/50-surface-companion.cfg").read_text()


# ---------------------------------------------------------------- 4. kbd-wake-sam

def serial_world(root, name="serial0-0", driver="surface_serial_hub", wakeup="disabled"):
    drv = root / "sys/bus/serial/drivers" / driver
    drv.mkdir(parents=True, exist_ok=True)
    dev = root / "sys/bus/serial/devices" / name
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "driver").symlink_to(drv)
    if wakeup is not None:
        put(root, f"/sys/bus/serial/devices/{name}/power/wakeup", wakeup)
    return name


def test_kbd_wake_sam_finds_the_device_by_its_driver_not_by_name(root):
    serial_world(root, name="serial0", driver="some_other_driver")
    assert catalog._serdev_keyboard() is None
    serial_world(root, name="serial3-0")
    assert catalog._serdev_keyboard() == "serial3-0"


def test_kbd_wake_sam_is_offered_on_sam_laptops_only_and_recommended_from_the_laptop_3(root, monkeypatch):
    serial_world(root)
    rows = lambda m: ids_of(m, root, monkeypatch)
    assert rows("Surface Laptop 3")["kbd-wake-sam"]["level"] == "recommended"
    assert rows("Surface Laptop 5")["kbd-wake-sam"]["level"] == "recommended"
    assert rows("Surface Laptop 2")["kbd-wake-sam"]["level"] == "optional"
    assert "kbd-wake-sam" not in rows("Surface Pro 7")
    assert "kbd-wake-sam" not in rows("Surface Book 2")
    (root / "sys/bus/serial/devices/serial0-0/driver").unlink()
    assert "kbd-wake-sam" not in rows("Surface Laptop 3")                           # no hub on this machine


def test_kbd_wake_sam_apply_writes_a_rule_for_the_found_device_and_enables_it_now(root):
    serial_world(root, name="serial2-0")
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    assert catalog._kbd_sam_detect(ctx) == "available"
    catalog._kbd_sam_apply(ctx, lambda l: None)
    rule = (root / "etc/udev/rules.d/50-surface-companion-kbd-wake.rules").read_text()
    assert 'ACTION=="add", SUBSYSTEM=="serial", KERNEL=="serial2-0", ATTR{power/wakeup}="enabled"' in rule
    assert "serial0-0" not in rule and base.is_managed(catalog.KBD_SAM_RULE)
    assert (root / "sys/bus/serial/devices/serial2-0/power/wakeup").read_text() == "enabled"
    assert catalog._kbd_sam_detect(ctx) == "applied"


def test_kbd_wake_sam_detect_counts_wakeup_that_is_already_enabled_by_someone_else(root):
    serial_world(root, wakeup="enabled")
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    assert catalog._kbd_sam_detect(ctx) == "applied"
    assert not base.is_managed(catalog.KBD_SAM_RULE)


def test_kbd_wake_sam_revert_undoes_ours_and_refuses_to_touch_what_it_did_not_make(root):
    serial_world(root)
    ctx = Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))
    catalog._kbd_sam_apply(ctx, lambda l: None)
    catalog._kbd_sam_revert(ctx, lambda l: None)
    assert not (root / "etc/udev/rules.d/50-surface-companion-kbd-wake.rules").exists()
    assert (root / "sys/bus/serial/devices/serial0-0/power/wakeup").read_text() == "disabled"
    put(root, "/sys/bus/serial/devices/serial0-0/power/wakeup", "enabled")             # now enabled by a rule you wrote
    put(root, "/etc/udev/rules.d/99-surface-kbd-wake.rules", "# mine\n")
    with pytest.raises(RuntimeError, match="something other than this app"):
        catalog._kbd_sam_revert(ctx, lambda l: None)
    assert (root / "etc/udev/rules.d/99-surface-kbd-wake.rules").exists()


def test_kbd_wake_sam_writes_no_rule_when_the_kernel_offers_no_wakeup_file(root):
    serial_world(root, wakeup=None)
    with pytest.raises(RuntimeError, match="no power/wakeup"):
        catalog._kbd_sam_apply(Ctx(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04")), lambda l: None)
    assert not (root / "etc/udev/rules.d/50-surface-companion-kbd-wake.rules").exists()


# ---------------------------------------------------------------- 5. kbd-wake-usb (untested on hardware)

def usb_world(root, vid="045e", pid="0924", removable="fixed", wakeup="disabled", name="1-4", with_hid=True, proto="01"):
    base_ = f"/sys/bus/usb/devices/{name}"
    put(root, f"{base_}/idVendor", vid)
    put(root, f"{base_}/idProduct", pid)
    put(root, f"{base_}/removable", removable)
    if wakeup is not None:
        put(root, f"{base_}/power/wakeup", wakeup)
    if with_hid:
        put(root, f"{base_}:1.0/bInterfaceClass", "03")
        put(root, f"{base_}:1.0/bInterfaceProtocol", proto)


def test_kbd_wake_usb_is_optional_never_recommended_and_says_untested(root, monkeypatch):
    for model in ("Surface Book 2", "Surface Book 3"):
        r = ids_of(model, root, monkeypatch)["kbd-wake-usb"]
        assert r["level"] == "optional" and "UNTESTED" in r["summary"], model
    assert "kbd-wake-usb" not in ids_of("Surface Laptop 3", root, monkeypatch)
    assert "kbd-wake-usb" not in ids_of("Surface Pro 7", root, monkeypatch)


def test_kbd_wake_usb_finds_the_builtin_hid_keyboard_and_ignores_the_rest(root):
    usb_world(root, name="1-1", pid="0001", with_hid=False)                  # a hub: no HID keyboard interface
    usb_world(root, name="1-2", pid="0002", proto="02")                      # a mouse (protocol 2)
    usb_world(root, name="1-3", vid="046d", pid="c31c")                       # somebody else's keyboard
    usb_world(root, name="1-5", pid="0003", removable="removable")           # an external Microsoft keyboard
    assert catalog._usb_keyboard() is None
    usb_world(root, name="1-4", pid="0924")
    k = catalog._usb_keyboard()
    assert (k["dev"], k["vid"], k["pid"]) == ("1-4", "045e", "0924")


def test_kbd_wake_usb_apply_detect_and_revert(root):
    ctx = Ctx(mkdev("Surface Book 2"), mkdistro("ubuntu", "debian", "26.04"))
    with pytest.raises(RuntimeError, match="keyboard base was not found"):
        catalog._kbd_usb_apply(ctx, lambda l: None)
    usb_world(root)
    assert catalog._kbd_usb_detect(ctx) == "available"
    catalog._kbd_usb_apply(ctx, lambda l: None)
    rule = (root / "etc/udev/rules.d/50-surface-companion-kbd-wake-usb.rules").read_text()
    assert 'ATTR{idVendor}=="045e", ATTR{idProduct}=="0924", ATTR{power/wakeup}="enabled"' in rule
    assert catalog._kbd_usb_detect(ctx) == "applied"
    catalog._kbd_usb_revert(ctx, lambda l: None)
    assert not (root / "etc/udev/rules.d/50-surface-companion-kbd-wake-usb.rules").exists()
    assert (root / "sys/bus/usb/devices/1-4/power/wakeup").read_text() == "disabled"


# ---------------------------------------------------------------- 8. advisories

def test_book_advisories_follow_the_existing_advisory_pattern(root, monkeypatch):
    b2 = ids_of("Surface Book 2", root, monkeypatch)
    b3 = ids_of("Surface Book 3", root, monkeypatch)
    sl3 = ids_of("Surface Laptop 3", root, monkeypatch)
    assert b2["book2-wifi-bluetooth"]["level"] == "advisory" and b2["book2-wifi-bluetooth"]["applicable"] is False
    assert "WPA2" in b2["book2-wifi-bluetooth"]["steps"] and "5 GHz" in b2["book2-wifi-bluetooth"]["steps"] \
        and "Bluetooth" in b2["book2-wifi-bluetooth"]["steps"]
    assert "book2-wifi-bluetooth" not in b3 and "book2-wifi-bluetooth" not in sl3
    for rows in (b2, b3):
        d = rows["book-detach-gpu"]
        assert d["level"] == "advisory" and d["applicable"] is False and "nvidia-smi" in d["steps"]
    assert "book-detach-gpu" not in sl3


# ---------------------------------------------------------------- conflicts in general

def test_a_fix_with_no_conflicts_is_unaffected_and_a_missing_partner_is_ignored(root, monkeypatch):
    rows = ids_of("Surface Pro 4", root, monkeypatch)
    assert rows["gpe-lid"]["status"] == "available"                       # partner gpe-lid-wake is not offered here
    assert rows["reboot-pci"]["status"] in ("available", "applied") if "reboot-pci" in rows else True


# ---------------------------------------------------------------- 7. surface-dtx-daemon for Surface Book 2 / 3

from surface_companion.pkg import catalog as pkgcat  # noqa: E402


def test_dtx_component_is_only_for_surface_book_2_and_3(root):
    c = pkgcat.by_id("dtx")
    d = mkdistro("ubuntu", "debian", "26.04")
    for model in ("Surface Book 2", "Surface Book 3"):
        assert planner.applicable(c, mkdev(model), d) == (True, "")
    for model in ("Surface Book", "Surface Pro 7", "Surface Laptop 3"):
        ok, why = planner.applicable(c, mkdev(model), d)
        assert not ok and why.startswith("Only for Surface Book 2"), model
    assert c.services == ("surface-dtx-daemon.service",) and c.user_services == ("surface-dtx-userd.service",)


def test_dtx_install_enables_the_system_service_and_the_user_service_for_whoever_is_logged_in(root, monkeypatch):
    monkeypatch.setattr(planner, "active_session_users", lambda: ["lane", "sam"])
    plan = planner.plan_install(["dtx"], mkdev("Surface Book 3"), mkdistro("ubuntu", "debian", "26.04"))
    cmds = [s.argv for s in plan.steps if s.kind == "cmd"]
    i = cmds.index(["apt-get", "install", "-y", "surface-dtx-daemon"])
    assert cmds[i + 1:] == [["systemctl", "enable", "--now", "surface-dtx-daemon.service"],
                            ["systemctl", "--user", "--machine=lane@.host", "enable", "--now", "surface-dtx-userd.service"],
                            ["systemctl", "--user", "--machine=sam@.host", "enable", "--now", "surface-dtx-userd.service"]]


def test_dtx_user_service_falls_back_to_the_next_login_when_nobody_is_logged_in(root, monkeypatch):
    monkeypatch.setattr(planner, "active_session_users", lambda: [])
    plan = planner.plan_install(["dtx"], mkdev("Surface Book 2"), mkdistro("fedora", "fedora", "43"))
    cmds = [s.argv for s in plan.steps if s.kind == "cmd"]
    assert ["systemctl", "--global", "enable", "surface-dtx-userd.service"] in cmds
    assert not any("--machine" in " ".join(c) for c in cmds)
    assert any("Nobody is logged in" in n for n in plan.notes)


def test_dtx_is_not_planned_for_other_models(root, monkeypatch):
    monkeypatch.setattr(planner, "active_session_users", lambda: ["lane"])
    plan = planner.plan_install(["dtx"], mkdev("Surface Book"), mkdistro("ubuntu", "debian", "26.04"))
    assert plan.blockers and not plan.steps


def test_active_session_users_only_counts_local_active_user_sessions(monkeypatch):
    sessions = {"1": "Name=lane\nActive=yes\nClass=user\nRemote=no\n",
                "2": "Name=sam\nActive=no\nClass=user\nRemote=no\n",              # locked / background
                "3": "Name=root\nActive=yes\nClass=user\nRemote=no\n",             # never root
                "4": "Name=gdm\nActive=yes\nClass=greeter\nRemote=no\n",           # login screen
                "5": "Name=bob\nActive=yes\nClass=user\nRemote=yes\n",             # ssh
                "6": "Name=lane\nActive=yes\nClass=user\nRemote=no\n"}             # same person twice

    def run(argv, timeout=10, **kw):
        if argv[:2] == ["loginctl", "list-sessions"]:
            return subprocess.CompletedProcess(argv, 0, "\n".join(f"{k} 1000 x seat0 tty1" for k in sessions) + "\n", "")
        if argv[:2] == ["loginctl", "show-session"]:
            return subprocess.CompletedProcess(argv, 0, sessions[argv[2]], "")
        return subprocess.CompletedProcess(argv, 1, "", "")
    monkeypatch.setattr(sysfs, "run", run)
    assert planner.active_session_users() == ["lane"]


def test_service_state_for_installed_dtx_reports_system_and_user_units(root, monkeypatch):
    c = pkgcat.by_id("dtx")

    def run(argv, timeout=10, **kw):
        unit = argv[-1]
        state = {"surface-dtx-daemon.service": "active", "surface-dtx-userd.service": "inactive"}[unit]
        return subprocess.CompletedProcess(argv, 0 if state == "active" else 3, state + "\n", "")
    monkeypatch.setattr(sysfs, "run", run)
    monkeypatch.setattr(planner, "active_session_users", lambda: ["lane"])
    st = planner.service_states(c)
    assert st == {"system": {"surface-dtx-daemon.service": "active"},
                  "user": {"lane": {"surface-dtx-userd.service": "inactive"}}}
    assert not planner.services_running(st)
    st["user"]["lane"]["surface-dtx-userd.service"] = "active"
    assert planner.services_running(st)


def test_component_status_adds_service_state_only_for_installed_components_with_services(root, monkeypatch):
    monkeypatch.setattr(planner.dist, "installed_version", lambda d, pkg: "0.3.12-1" if pkg == "surface-dtx-daemon" else None)
    monkeypatch.setattr(planner, "service_states", lambda c: {"system": {"surface-dtx-daemon.service": "failed"}, "user": {}})
    rows = {r["id"]: r for r in planner.component_status(mkdev("Surface Book 2"), mkdistro("ubuntu", "debian", "26.04"))}
    assert rows["dtx"]["installed"] == "0.3.12-1" and rows["dtx"]["services"]["running"] is False
    assert rows["iptsd"]["services"] is None
    rows = {r["id"]: r for r in planner.component_status(mkdev("Surface Laptop 3"), mkdistro("ubuntu", "debian", "26.04"))}
    assert rows["dtx"]["installable"] is False and rows["dtx"]["services"] is None
