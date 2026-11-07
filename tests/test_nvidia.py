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


def book(model="Surface Book 2"):
    return device.Device(vendor="Microsoft Corporation", model=model, family="book", is_surface=True)


def distro(family, id_="x", name="x", immutable=False):
    return dist.Distro(id=id_, name=name, family=family, immutable=immutable)


@pytest.fixture
def gpu(root, monkeypatch):
    """A fake NVIDIA dGPU on the PCI bus, driver not loaded."""
    put(root, "/sys/bus/pci/devices/0000:01:00.0/vendor", "0x10de")
    put(root, "/sys/bus/pci/devices/0000:01:00.0/class", "0x030200")
    return root


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
