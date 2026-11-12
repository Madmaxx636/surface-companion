import subprocess

import pytest

from conftest import put
from surface_companion import device, sysfs
from surface_companion.pkg import distro as dist, nvidia

UBUNTU_DRIVERS = """== /sys/devices/pci0000:00/0000:01:00.0 ==
modalias : pci:v000010DEd00001D10sv00001414sd00000000bc03sc02i00
vendor   : NVIDIA Corporation
driver   : nvidia-driver-570 - distro non-free
driver   : nvidia-driver-580-open - distro non-free recommended
driver   : xserver-xorg-video-nouveau - distro free builtin
"""


def book(model="Surface Book 3"):             # Turing-era by default; Pascal cases below say so explicitly
    return device.Device(vendor="Microsoft Corporation", model=model, family="book", is_surface=True)


def distro(family, id_="x", name="x", immutable=False):
    return dist.Distro(id=id_, name=name, family=family, immutable=immutable)


@pytest.fixture
def gpu(root, monkeypatch):
    """A fake NVIDIA dGPU on the PCI bus, driver not loaded."""
    put(root, "/sys/bus/pci/devices/0000:01:00.0/vendor", "0x10de")
    put(root, "/sys/bus/pci/devices/0000:01:00.0/class", "0x030200")
    put(root, "/sys/bus/pci/devices/0000:01:00.0/device", "0x2191")        # TU116M (GTX 1660 Ti): Turing, not Pascal
    return root


def set_gpu_id(root, device_id):
    put(root, "/sys/bus/pci/devices/0000:01:00.0/device", device_id)


def fake_run(monkeypatch, sb="SecureBoot disabled", drivers=UBUNTU_DRIVERS, candidates=("nvidia-driver", "nvidia-kernel-dkms"),
             have=("ubuntu-drivers",)):
    def run(argv, timeout=10, **kw):
        if argv[:1] == ["mokutil"]:
            return subprocess.CompletedProcess(argv, 0, sb + "\n", "")
        if argv[:2] == ["ubuntu-drivers", "devices"]:
            return subprocess.CompletedProcess(argv, 0, drivers, "")
        if argv[:2] == ["apt-cache", "policy"]:
            c = argv[2] in candidates
            return subprocess.CompletedProcess(argv, 0, f"{argv[2]}:\n  Candidate: {'1.0' if c else '(none)'}\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(sysfs, "run", run)
    monkeypatch.setattr(sysfs, "have", lambda c: c in have)


def test_parse_prefers_recommended_driver():
    assert nvidia.parse_ubuntu_drivers(UBUNTU_DRIVERS) == "nvidia-driver-580-open"
    assert nvidia.parse_ubuntu_drivers("driver : nvidia-driver-535 - distro non-free\n") == "nvidia-driver-535"
    assert nvidia.parse_ubuntu_drivers("driver : xserver-xorg-video-nouveau - free\n") is None
    assert nvidia.dkms_package_for("nvidia-driver-580-open") == "nvidia-dkms-580-open"


def test_ubuntu_plan_installs_driver_and_dkms_for_the_custom_kernel(gpu, monkeypatch):
    fake_run(monkeypatch)
    p = nvidia.plan(book(), distro("debian", "ubuntu", "Ubuntu 26.04"))
    assert not p["blockers"] and not p["interactive"] and p["reboot"]
    assert p["steps"][-1]["argv"] == ["apt-get", "install", "-y", "nvidia-driver-580-open", "nvidia-dkms-580-open"]
    assert any("prime-run" in n for n in p["notes"]) and p["risks"]


def test_ubuntu_without_finder_installs_it_first(gpu, monkeypatch):
    fake_run(monkeypatch, have=())
    p = nvidia.plan(book(), distro("debian", "ubuntu", "Ubuntu 26.04"))
    assert p["steps"][0]["argv"] == ["apt-get", "install", "-y", "ubuntu-drivers-common"]


def test_secure_boot_forces_the_terminal_route_with_a_clear_note(gpu, monkeypatch):
    fake_run(monkeypatch, sb="SecureBoot enabled")
    p = nvidia.plan(book(), distro("debian", "ubuntu", "Ubuntu 26.04"))
    assert p["interactive"] and p["secure_boot"] and any("Secure Boot is on" in n for n in p["notes"])


def test_debian_needs_non_free(gpu, monkeypatch):
    fake_run(monkeypatch, candidates=())
    p = nvidia.plan(book(), distro("debian", "debian", "Debian 12"))
    assert p["blockers"] and "non-free" in p["blockers"][0] and not p["steps"]
    fake_run(monkeypatch)
    p = nvidia.plan(book(), distro("debian", "debian", "Debian 12"))
    assert p["steps"][-1]["argv"] == ["apt-get", "install", "-y", "nvidia-driver", "nvidia-kernel-dkms"]


def test_arch_uses_dkms(gpu, monkeypatch):
    fake_run(monkeypatch)
    p = nvidia.plan(book(), distro("arch"))
    assert p["steps"][-1]["argv"] == ["pacman", "-S", "--needed", "--noconfirm", "nvidia-dkms", "nvidia-utils"]


def test_fedora_is_explained_not_automated(gpu, monkeypatch):
    fake_run(monkeypatch)
    p = nvidia.plan(book(), distro("fedora", "fedora"))
    assert p["blockers"] and "RPM Fusion" in p["blockers"][0] and not p["steps"]


def test_no_gpu_or_already_loaded_or_immutable_are_blocked(root, monkeypatch):
    fake_run(monkeypatch)
    assert "No NVIDIA GPU" in nvidia.plan(book(), distro("debian", "ubuntu"))["blockers"][0]
    put(root, "/sys/bus/pci/devices/0000:01:00.0/vendor", "0x10de")
    put(root, "/sys/bus/pci/devices/0000:01:00.0/class", "0x030200")
    put(root, "/proc/modules", "nvidia 1000 0 - Live 0x0\n")
    assert "already loaded" in nvidia.plan(book(), distro("debian", "ubuntu"))["blockers"][0]
    (root / "proc/modules").write_text("nouveau 1 0 - Live 0x0\n")
    assert nvidia.info()["loaded"] == "nouveau"
    assert nvidia.plan(book(), distro("fedora", immutable=True))["blockers"]


def test_terminal_script_runs_steps_with_sudo_and_quotes():
    s = nvidia.terminal_script([{"kind": "cmd", "argv": ["apt-get", "install", "-y", "nvidia-driver-580-open"]},
                                {"kind": "write", "argv": []}])
    assert s == "set -e\nsudo apt-get install -y nvidia-driver-580-open"
    assert "sudo echo 'a b'" in nvidia.terminal_script([{"kind": "cmd", "argv": ["echo", "a b"]}])


def test_daemon_refuses_noninteractive_install_when_secure_boot_is_on(gpu, monkeypatch):
    from surface_companion import actions
    fake_run(monkeypatch, sb="SecureBoot enabled")
    monkeypatch.setattr(actions, "get_device", lambda: book())
    monkeypatch.setattr(actions, "get_distro", lambda: distro("debian", "ubuntu", "Ubuntu 26.04"))
    act = actions.build(actions.Settings(path=gpu / "s.json"))["install_nvidia"]
    assert act.polkit == actions.POLKIT_ADMIN and act.job
    with pytest.raises(RuntimeError, match="Secure Boot is on"):
        act.fn({}, lambda l: None)


def test_daemon_runs_the_planned_steps_when_secure_boot_is_off(gpu, monkeypatch):
    from surface_companion import actions
    from surface_companion.pkg import planner
    fake_run(monkeypatch)
    monkeypatch.setattr(actions, "get_device", lambda: book())
    monkeypatch.setattr(actions, "get_distro", lambda: distro("debian", "ubuntu", "Ubuntu 26.04"))
    ran = []
    monkeypatch.setattr(planner, "run_plan", lambda plan, log, dry_run=False: ran.append([s.argv for s in plan.steps]) or True)
    act = actions.build(actions.Settings(path=gpu / "s.json"))["install_nvidia"]
    assert "Restart" in act.fn({}, lambda l: None)
    assert ran == [[["apt-get", "install", "-y", "nvidia-driver-580-open", "nvidia-dkms-580-open"]]]


# ------------------------------------------------------------------ Pascal and the Surface Book 2: proprietary 580

RECOMMENDS_NEWER = UBUNTU_DRIVERS.replace("nvidia-driver-580-open - distro non-free recommended",
                                          "nvidia-driver-595-open - distro non-free recommended") \
    + "driver   : nvidia-driver-580 - distro non-free\n"


def apt_candidates(*names):
    return tuple(names)


def test_pascal_device_id_drops_open_and_pins_580_even_if_a_newer_driver_is_recommended(gpu, monkeypatch):
    set_gpu_id(gpu, "0x1c8d")                                          # GP107M (GTX 1050 Mobile)
    fake_run(monkeypatch, drivers=RECOMMENDS_NEWER, candidates=("nvidia-driver-580", "nvidia-dkms-580"))
    p = nvidia.plan(book("Surface Book 3"), distro("debian", "ubuntu", "Ubuntu 26.04"))
    assert not p["blockers"] and p["pre_turing"]
    assert p["steps"][-1]["argv"] == ["apt-get", "install", "-y", "nvidia-driver-580", "nvidia-dkms-580"]
    assert not any("-open" in a for a in p["steps"][-1]["argv"])
    assert all(s["argv"][3:] != ["ubuntu-drivers-common"] for s in p["steps"])           # no finder needed when pinned
    assert any("Pascal" in n and "open" in n and "580" in n for n in p["notes"]), "the reason must be shown"


def test_surface_book_2_is_pinned_even_when_the_pci_id_cannot_be_read(gpu, monkeypatch):
    (gpu / "sys/bus/pci/devices/0000:01:00.0/device").unlink()
    fake_run(monkeypatch, drivers=RECOMMENDS_NEWER, candidates=("nvidia-driver-580", "nvidia-dkms-580"))
    p = nvidia.plan(book("Surface Book 2"), distro("debian", "ubuntu", "Ubuntu 26.04"))
    assert p["pre_turing"] and p["steps"][-1]["argv"][-2:] == ["nvidia-driver-580", "nvidia-dkms-580"]


def test_turing_and_newer_keep_what_ubuntu_drivers_recommends(gpu, monkeypatch):
    fake_run(monkeypatch, drivers=RECOMMENDS_NEWER)
    for dev_id in ("0x1e00", "0x2191", "0x2560"):                      # boundary, Turing, Ampere
        set_gpu_id(gpu, dev_id)
        p = nvidia.plan(book("Surface Book 3"), distro("debian", "ubuntu", "Ubuntu 26.04"))
        assert not p["pre_turing"], dev_id
        assert p["steps"][-1]["argv"] == ["apt-get", "install", "-y", "nvidia-driver-595-open", "nvidia-dkms-595-open"]
        assert not any("Pascal" in n for n in p["notes"])


def test_the_pascal_boundary_is_exactly_0x1e00(gpu):
    dev = book("Surface Book 3")
    set_gpu_id(gpu, "0x1dff")
    assert nvidia.pre_turing(dev, nvidia.book.dgpu())
    set_gpu_id(gpu, "0x1e00")
    assert not nvidia.pre_turing(dev, nvidia.book.dgpu())
    assert nvidia.pre_turing(book("Surface Book 2"), None)
    assert not nvidia.pre_turing(book("Surface Book 3"), None)


def test_pinned_580_missing_from_apt_is_a_blocker_not_a_fallback_to_something_newer(gpu, monkeypatch):
    set_gpu_id(gpu, "0x1c8d")
    fake_run(monkeypatch, drivers=RECOMMENDS_NEWER, candidates=())
    p = nvidia.plan(book("Surface Book 2"), distro("debian", "ubuntu", "Ubuntu 26.04"))
    assert p["blockers"] and "nvidia-driver-580" in p["blockers"][0] and not p["steps"]


def test_arch_pascal_points_to_the_aur_instead_of_a_pacman_step_that_would_fail(gpu, monkeypatch):
    set_gpu_id(gpu, "0x1c8d")
    fake_run(monkeypatch)
    p = nvidia.plan(book("Surface Book 2"), distro("arch"))
    assert not p["steps"] and len(p["blockers"]) == 1
    msg = p["blockers"][0]
    assert "AUR" in msg and "nvidia-580xx-dkms" in msg and "nvidia-580xx-utils" in msg
    set_gpu_id(gpu, "0x2191")                                           # Turing on Arch is unchanged
    p = nvidia.plan(book("Surface Book 3"), distro("arch"))
    assert p["steps"][-1]["argv"][-2:] == ["nvidia-dkms", "nvidia-utils"]


def test_debian_pascal_is_blocked_when_the_distro_driver_is_newer_than_580(gpu, monkeypatch):
    set_gpu_id(gpu, "0x1c8d")

    def run(argv, timeout=10, **kw):
        if argv[:2] == ["apt-cache", "policy"]:
            ver = {"nvidia-driver": "595.58.03-1", "nvidia-kernel-dkms": "595.58.03-1"}.get(argv[2], "(none)")
            return subprocess.CompletedProcess(argv, 0, f"{argv[2]}:\n  Candidate: {ver}\n", "")
        return subprocess.CompletedProcess(argv, 0, "SecureBoot disabled\n" if argv[0] == "mokutil" else "", "")
    monkeypatch.setattr(sysfs, "run", run)
    monkeypatch.setattr(sysfs, "have", lambda c: False)
    p = nvidia.plan(book("Surface Book 2"), distro("debian", "debian", "Debian 13"))
    assert p["blockers"] and "595" in p["blockers"][0] and "580" in p["blockers"][0] and not p["steps"]
    monkeypatch.setattr(sysfs, "run", lambda argv, timeout=10, **kw: subprocess.CompletedProcess(
        argv, 0, f"{argv[2] if argv[0] == 'apt-cache' else ''}:\n  Candidate: 570.1-1\n" if argv[0] == "apt-cache"
        else "SecureBoot disabled\n", ""))
    p = nvidia.plan(book("Surface Book 2"), distro("debian", "debian", "Debian 12"))
    assert not p["blockers"] and p["steps"][-1]["argv"][-2:] == ["nvidia-driver", "nvidia-kernel-dkms"]


def test_dgpu_reports_its_pci_device_id(gpu):
    set_gpu_id(gpu, "0x1c8d")
    assert nvidia.book.dgpu()["device_id"] == 0x1C8D
