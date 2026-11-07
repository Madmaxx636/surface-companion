import subprocess

import pytest

from conftest import put
from surface_companion import device, sysfs
from surface_companion.fixes import base, bootloader, catalog, evaluate
from surface_companion.fixes.base import Ctx
from surface_companion.pkg import distro as dist, planner


def mkdev(model, cpu="intel", surface_kernel=False, vendor="Microsoft Corporation"):
    d = device.Device(vendor=vendor, model=model, cpu_vendor=cpu, surface_kernel=surface_kernel, is_surface=True)
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


def test_dtx_only_on_book_and_thermald_only_intel(root):
    d = mkdistro("ubuntu", "debian", "24.04")
    plan = planner.plan_install(["dtx", "thermald"], mkdev("Surface Pro 9", cpu="amd"), d)
    assert plan.blockers and not plan.steps
    plan = planner.plan_install(["dtx"], mkdev("Surface Book 2"), d)
    assert "surface-dtx-daemon" in plan.steps[-1].argv


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
