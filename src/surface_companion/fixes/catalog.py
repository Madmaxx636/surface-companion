"""The hotfix catalogue, collected from the linux-surface wiki and repository.

Each entry cites the page it comes from. Fixes that need hardware or firmware we cannot
safely change from here are advisories: the app tells you, it does not guess.
"""
from __future__ import annotations

import os
import re
import urllib.request
from pathlib import Path

from .. import sysfs
from ..device import REBOOT_PCI_MODELS, MARVELL_MODELS, INTEL_12TH, PINCTRL_ICELAKE, PINCTRL_TIGERLAKE
from ..hw import camera as hwcamera
from . import bootloader
from .base import Ctx, Fix, Log, is_managed, remove_managed, sh, write_managed

WIKI = "https://github.com/linux-surface/linux-surface/wiki/"
RAW = "https://raw.githubusercontent.com/linux-surface/linux-surface/master/"


def _file_state(path: str):
    return lambda ctx: "applied" if is_managed(path) else "available"


def _systemctl(*args: str, log: Log, check: bool = True) -> None:
    if check:
        sh(["systemctl", *args], log)
    else:
        sysfs.run(["systemctl", *args])


# ------------------------------------------------------------------ encrypted-disk keyboard

INITRAMFS_BEGIN = "# BEGIN surface-companion"
INITRAMFS_END = "# END surface-companion"


def _initramfs_modules(ctx: Ctx) -> list[str]:
    d = ctx.dev
    mods = ["surface_aggregator", "surface_aggregator_registry", "surface_aggregator_hub", "surface_hid_core", "8250_dw"]
    mods.append("surface_kbd" if d.model_in(("Surface Laptop", "Surface Laptop 2")) else "surface_hid")
    if d.cpu_vendor == "intel":
        mods += ["intel_lpss", "intel_lpss_pci"]
        if d.model_in(PINCTRL_ICELAKE):
            mods.append("pinctrl_icelake")
        elif d.model_in(PINCTRL_TIGERLAKE):
            mods.append("pinctrl_tigerlake")
    elif d.cpu_vendor == "amd":
        mods.append("pinctrl_amd")
    have = []
    for m in mods:
        r = sysfs.run(["modinfo", "-F", "name", m])
        if r.returncode == 0 and r.stdout.strip():
            have.append(m)
    return have


def _encrypted(ctx: Ctx) -> bool:
    return any(ln.strip() and not ln.lstrip().startswith("#")
               for ln in (sysfs.read("/etc/crypttab", "") or "").splitlines())


_DRACUT_CONF = "/etc/dracut.conf.d/50-surface-companion.conf"
_MKINITCPIO_CONF = "/etc/mkinitcpio.conf.d/50-surface-companion.conf"
_TOOLS_FILE = "/etc/initramfs-tools/modules"


def _initramfs_conf(ctx: Ctx) -> tuple[str, str]:
    return {"dracut": (_DRACUT_CONF, "dracut"), "mkinitcpio": (_MKINITCPIO_CONF, "mkinitcpio"),
            "initramfs-tools": (_TOOLS_FILE, "initramfs-tools")}.get(ctx.distro.initramfs, ("", ""))


def _tools_block_present() -> bool:
    return INITRAMFS_BEGIN in (sysfs.read(_TOOLS_FILE, "") or "")


def _initramfs_detect(ctx: Ctx) -> str:
    # any of our marks counts, including one left by an earlier run that used a different tool
    return "applied" if (_tools_block_present() or is_managed(_DRACUT_CONF) or is_managed(_MKINITCPIO_CONF)) \
        else "available"


def _initramfs_note(ctx: Ctx) -> str:
    return ("" if _encrypted(ctx) else
            "Only needed with an encrypted disk. This system has none configured (/etc/crypttab is empty), "
            "so you can skip this.")


def _usable_kernels() -> list[str]:
    """Kernels with a real module tree and image; leftover folders from removed kernels are ignored."""
    out = []
    for d in sysfs.glob("/usr/lib/modules/*") + sysfs.glob("/lib/modules/*"):
        k = d.name
        if k in out or not sysfs.p(d / "modules.dep").exists():
            continue
        if sysfs.p(f"/boot/vmlinuz-{k}").exists() or sysfs.p(d / "vmlinuz").exists():
            out.append(k)
    return sorted(out)


def _dracut_target(k: str) -> str:
    for cand in (f"/boot/initramfs-{k}.img", f"/boot/initrd.img-{k}"):
        if sysfs.p(cand).exists():
            return cand
    return ""


def _rebuild_initramfs(ctx: Ctx, kind: str, log: Log) -> None:
    if kind == "initramfs-tools":
        sh(["update-initramfs", "-u", "-k", "all"], log, 900)
    elif kind == "mkinitcpio":
        sh(["mkinitcpio", "-P"], log, 900)
    elif kind == "dracut":
        running = ctx.dev.kernel
        failed, rebuilt = [], 0
        for k in _usable_kernels():
            target = _dracut_target(k)
            if not target:
                log(f"skipping {k}: no existing initramfs to replace")
                continue
            try:
                sh(["dracut", "--force", "--kver", k, target], log, 900)
                rebuilt += 1
            except RuntimeError:
                failed.append(k)
                log(f"{k}: rebuild failed; carrying on with the others")
        if not rebuilt:
            raise RuntimeError("No installed kernel with an existing initramfs was found to rebuild")
        if running in failed:
            raise RuntimeError("Could not rebuild the initramfs for the running kernel: " + running)


def _initramfs_write_config(kind: str, mods: list[str]) -> None:
    if kind == "initramfs-tools":
        txt = sysfs.read(_TOOLS_FILE, "") or ""
        txt = re.sub(rf"\n?{INITRAMFS_BEGIN}.*?{INITRAMFS_END}\n?", "\n", txt, flags=re.S)
        txt += f"\n{INITRAMFS_BEGIN}\n" + "\n".join(mods) + f"\n{INITRAMFS_END}\n"
        sysfs.write(_TOOLS_FILE, txt)
    elif kind == "dracut":
        write_managed(_DRACUT_CONF, 'add_drivers+=" ' + " ".join(mods) + ' "\n')
    elif kind == "mkinitcpio":
        write_managed(_MKINITCPIO_CONF, "MODULES+=(" + " ".join(mods) + ")\n")


def _initramfs_remove_config() -> None:
    txt = sysfs.read(_TOOLS_FILE)
    if txt is not None and INITRAMFS_BEGIN in txt:
        sysfs.write(_TOOLS_FILE, re.sub(rf"\n?{INITRAMFS_BEGIN}.*?{INITRAMFS_END}\n?", "\n", txt, flags=re.S))
    remove_managed(_DRACUT_CONF)
    remove_managed(_MKINITCPIO_CONF)


def _initramfs_apply(ctx: Ctx, log: Log) -> None:
    kind = ctx.distro.initramfs
    if not kind:
        raise RuntimeError("No supported initramfs tool found")
    mods = _initramfs_modules(ctx)
    if not mods:
        raise RuntimeError("None of the Surface input modules were found for the running kernel")
    if not ctx.dev.surface_kernel:
        log("note: you are not running the linux-surface kernel; only modules this kernel has are added")
    log(f"initramfs tool: {kind}")
    log("modules: " + " ".join(mods))
    _initramfs_write_config(kind, mods)
    try:
        _rebuild_initramfs(ctx, kind, log)
    except Exception:
        _initramfs_remove_config()          # don't leave a config behind that the images were never built with
        log("The rebuild failed, so the change was rolled back.")
        raise


def _initramfs_revert(ctx: Ctx, log: Log) -> None:
    kind = ctx.distro.initramfs
    leftover_other = (is_managed(_DRACUT_CONF) and kind != "dracut") or (is_managed(_MKINITCPIO_CONF) and kind != "mkinitcpio")
    _initramfs_remove_config()
    if leftover_other and not _tools_block_present() and kind:
        log("Removed a leftover setting from an earlier run; no images needed rebuilding.")
        return
    if kind:
        _rebuild_initramfs(ctx, kind, log)


# ------------------------------------------------------------------ audio hiss

AUTOMUTE_UNIT = "/etc/systemd/system/surface-companion-automute.service"


def _automute_present(ctx: Ctx) -> bool:
    return sysfs.have("amixer") and sysfs.run(["amixer", "-c", "0", "sget", "Auto-Mute Mode"]).returncode == 0


def _automute_apply(ctx: Ctx, log: Log) -> None:
    amixer = sysfs.run(["which", "amixer"]).stdout.strip() or "/usr/bin/amixer"
    write_managed(AUTOMUTE_UNIT, f"""[Unit]
Description=Disable speaker auto-mute (stops hiss with headphones on Surface)
After=sound.target suspend.target hibernate.target hybrid-sleep.target

[Service]
Type=oneshot
ExecStart={amixer} -c 0 sset "Auto-Mute Mode" Disabled

[Install]
WantedBy=multi-user.target suspend.target hibernate.target hybrid-sleep.target
""")
    _systemctl("daemon-reload", log=log)
    _systemctl("enable", "--now", "surface-companion-automute.service", log=log)


def _automute_revert(ctx: Ctx, log: Log) -> None:
    _systemctl("disable", "--now", "surface-companion-automute.service", log=log, check=False)
    remove_managed(AUTOMUTE_UNIT)
    _systemctl("daemon-reload", log=log)
    sysfs.run(["amixer", "-c", "0", "sset", "Auto-Mute Mode", "Enabled"])


# ------------------------------------------------------------------ wake sources

GPE_CONF = "/etc/modprobe.d/50-surface-companion-gpe.conf"


def _gpe_apply(ctx: Ctx, log: Log) -> None:
    write_managed(GPE_CONF, "# Closing the lid while suspended wakes the device\nblacklist surface_gpe\n")
    sysfs.run(["modprobe", "-r", "surface_gpe"])
    log("surface_gpe blacklisted")


NOWAKE_SCRIPT = "/usr/local/lib/surface-companion/disable-wake.sh"
NOWAKE_UNIT = "/etc/systemd/system/surface-companion-nowake.service"


def _nowake_apply(ctx: Ctx, log: Log) -> None:
    write_managed(NOWAKE_SCRIPT, """#!/bin/sh
# Stop the USB controller and the lid from waking the device (keys waking it in a bag, lid events).
if grep -q '^XHC.*enabled' /proc/acpi/wakeup; then echo XHC > /proc/acpi/wakeup; fi
for w in /sys/bus/acpi/devices/PNP0C0D:*/power/wakeup; do
  [ -e "$w" ] && echo disabled > "$w"
done
exit 0
""", 0o755)
    write_managed(NOWAKE_UNIT, f"""[Unit]
Description=Disable accidental wake sources on Surface
After=suspend.target hibernate.target hybrid-sleep.target

[Service]
Type=oneshot
ExecStart={NOWAKE_SCRIPT}

[Install]
WantedBy=multi-user.target suspend.target hibernate.target hybrid-sleep.target
""")
    _systemctl("daemon-reload", log=log)
    _systemctl("enable", "--now", "surface-companion-nowake.service", log=log)


def _nowake_revert(ctx: Ctx, log: Log) -> None:
    _systemctl("disable", "--now", "surface-companion-nowake.service", log=log, check=False)
    remove_managed(NOWAKE_UNIT)
    remove_managed(NOWAKE_SCRIPT)
    _systemctl("daemon-reload", log=log)
    log("Wake sources return to their defaults after the next reboot.")


# ------------------------------------------------------------------ wifi

NM_CONF = "/etc/NetworkManager/conf.d/99-surface-companion.conf"


def _wifi_apply(ctx: Ctx, log: Log) -> None:
    write_managed(NM_CONF, "[connection]\nwifi.powersave = 2\n\n[device]\nwifi.scan-rand-mac-address=false\n")
    _systemctl("reload", "NetworkManager", log=log)


def _wifi_revert(ctx: Ctx, log: Log) -> None:
    remove_managed(NM_CONF)
    _systemctl("reload", "NetworkManager", log=log)


def _marvell(ctx: Ctx) -> bool:
    mods = sysfs.read("/proc/modules", "") or ""
    return ctx.dev.model_in(MARVELL_MODELS) or "mwifiex_pcie" in mods


# ------------------------------------------------------------------ reboot=pci

def _pci_apply(ctx: Ctx, log: Log) -> None:
    bootloader.add("reboot=pci", log)


def _pci_revert(ctx: Ctx, log: Log) -> None:
    bootloader.remove("reboot=pci", log)


# ------------------------------------------------------------------ GNOME right click (user scope)

TOUCHPAD = ["org.gnome.desktop.peripherals.touchpad", "click-method"]


def _gnome(ctx: Ctx) -> bool:
    return sysfs.have("gsettings") and "gnome" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


def _gnome_detect(ctx: Ctx) -> str:
    return "applied" if "areas" in sysfs.run(["gsettings", "get", *TOUCHPAD]).stdout else "available"


def _gnome_apply(ctx: Ctx, log: Log) -> None:
    sh(["gsettings", "set", *TOUCHPAD, "areas"], log)


def _gnome_revert(ctx: Ctx, log: Log) -> None:
    sh(["gsettings", "set", *TOUCHPAD, "fingers"], log)


# ------------------------------------------------------------------ thermald model configuration

THERMAL_DIR = "/etc/thermald"
THERMAL_MANIFEST = THERMAL_DIR + "/.surface-companion"
THERMAL_DROPIN = "/etc/systemd/system/thermald.service.d/50-surface-companion.conf"
THERMAL_MODELS = [
    ("Surface Laptop Go 2", "surface_laptop_go_2"), ("Surface Laptop Go", "surface_laptop_go_1"),
    ("Surface Laptop 4", None), ("Surface Laptop", "surface_laptop_1"),
    ("Surface Pro 7+", None), ("Surface Pro 7", "surface_pro_7"),
]


def _thermal_dir(ctx: Ctx):
    model = ctx.dev.model
    for prefix, sub in THERMAL_MODELS:
        if model.startswith(prefix):
            return sub
    return ""   # generic example at the top of contrib/thermald


def _thermal_apply(ctx: Ctx, log: Log) -> None:
    sub = _thermal_dir(ctx)
    base = RAW + "contrib/thermald/" + (sub + "/" if sub else "")
    saved = []
    sysfs.p(THERMAL_DIR).mkdir(parents=True, exist_ok=True)
    for name in ("thermal-conf.xml", "thermal-cpu-cdev-order.xml"):
        for url in (base + name, RAW + "contrib/thermald/" + name):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    data = r.read()
                break
            except OSError:
                data = None
        if data is None:
            log(f"{name}: not available upstream, skipped")
            continue
        dest = sysfs.p(f"{THERMAL_DIR}/{name}")
        if dest.exists() and not (sysfs.p(THERMAL_MANIFEST).exists() and name in (sysfs.read(THERMAL_MANIFEST) or "")):
            dest.rename(str(dest) + ".before-surface-companion")
            log(f"existing {name} kept as {name}.before-surface-companion")
        dest.write_bytes(data)
        saved.append(name)
        log(f"installed {name} from {url}")
    sysfs.p(THERMAL_MANIFEST).write_text("\n".join(saved) + "\n")
    # Newer thermald loads the OEM config from ACPI with --adaptive and ignores ours.
    show = sysfs.run(["systemctl", "show", "-p", "ExecStart", "--value", "thermald.service"]).stdout
    m = re.search(r"argv\[\]=([^;]*?)\s*;", show)
    if m and "--adaptive" in m.group(1):
        cmd = m.group(1).replace("--adaptive", "").strip()
        sysfs.p(THERMAL_DROPIN).parent.mkdir(parents=True, exist_ok=True)
        write_managed(THERMAL_DROPIN, f"[Service]\nExecStart=\nExecStart={cmd}\n")
        log("removed --adaptive from the thermald service so the manual configuration is used")
    _systemctl("daemon-reload", log=log)
    _systemctl("enable", "--now", "thermald.service", log=log, check=False)
    _systemctl("restart", "thermald.service", log=log, check=False)


def _thermal_revert(ctx: Ctx, log: Log) -> None:
    names = (sysfs.read(THERMAL_MANIFEST, "") or "").split()
    for n in names:
        dest = sysfs.p(f"{THERMAL_DIR}/{n}")
        dest.unlink(missing_ok=True)
        bak = sysfs.p(f"{THERMAL_DIR}/{n}.before-surface-companion")
        if bak.exists():
            bak.rename(dest)
    sysfs.p(THERMAL_MANIFEST).unlink(missing_ok=True)
    remove_managed(THERMAL_DROPIN)
    _systemctl("daemon-reload", log=log)
    _systemctl("restart", "thermald.service", log=log, check=False)


def _thermal_detect(ctx: Ctx) -> str:
    return "applied" if sysfs.p(THERMAL_MANIFEST).exists() else "available"


# ------------------------------------------------------------------ IPU3 firmware

def _ipu3(ctx: Ctx) -> bool:
    return hwcamera.lookup(ctx.dev.model)[0] == "IPU3"


def _ipu3_have(ctx: Ctx) -> bool:
    return any(sysfs.p(f).exists() for f in hwcamera.FW)


def _ipu3_apply(ctx: Ctx, log: Log) -> None:
    d = ctx.distro
    if d.family == "debian" and d.id == "ubuntu":
        sh(["apt-get", "install", "-y", "linux-firmware"], log, 900)
    elif d.family == "debian":
        sh(["apt-get", "install", "-y", "firmware-misc-nonfree"], log, 900)
        src = next(iter(sysfs.glob("/lib/firmware/**/irci_irci_ecr-master_20161208_0213_20170112_1500.bin")), None)
        if src and not _ipu3_have(ctx):
            sysfs.p("/lib/firmware/intel").mkdir(parents=True, exist_ok=True)
            sysfs.p("/lib/firmware/intel/ipu3-fw.bin").symlink_to(src)
            log(f"linked {src} as /lib/firmware/intel/ipu3-fw.bin")
    elif d.family == "fedora":
        sh(["dnf", "install", "-y", "intel-vsc-firmware"], log, 900)
    elif d.family == "arch":
        sh(["pacman", "-S", "--needed", "--noconfirm", "linux-firmware-intel"], log, 900)
    else:
        raise RuntimeError("Install the IPU3 firmware (ipu3-fw.bin) for your distribution")


# ------------------------------------------------------------------ the catalogue

def build() -> list[Fix]:
    surface = lambda c: c.dev.is_surface   # noqa: E731
    return [
        Fix("initramfs-input", "Keyboard at the disk-unlock screen",
            "Adds the Surface keyboard drivers to the initramfs so you can type your disk-encryption passphrase "
            "on the built-in keyboard.",
            "Boot", WIKI + "Disk-Encryption", "optional",
            applies=lambda c: surface(c) and bool(c.distro.initramfs),
            detect=_initramfs_detect, apply=_initramfs_apply, revert=_initramfs_revert,
            recommend=_encrypted, note=_initramfs_note),
        Fix("reboot-pci", "Reboot hangs at the Surface logo",
            "Adds reboot=pci to the kernel command line.",
            "Boot", WIKI + "Surface-Laptop-3", "recommended",
            applies=lambda c: c.dev.model_in(REBOOT_PCI_MODELS) and bootloader.can_manage(),
            detect=lambda c: "applied" if bootloader.in_use("reboot=pci") else "available",
            apply=_pci_apply, revert=_pci_revert, reboot=True),
        Fix("audio-automute", "Hiss from speakers with headphones plugged in",
            "Disables the audio chip's Auto-Mute Mode, which causes hiss on many Surface models.",
            "Audio", WIKI + "Known-Issues-and-FAQ", "optional",
            applies=lambda c: surface(c) and _automute_present(c),
            detect=_file_state(AUTOMUTE_UNIT), apply=_automute_apply, revert=_automute_revert),
        Fix("gpe-lid", "Closing the lid wakes a sleeping device",
            "Blacklists the surface_gpe module. Needed on Surface Pro 4 and 5. Lid events may stop being "
            "reported on other models.",
            "Sleep", WIKI + "Known-Issues-and-FAQ", "optional",
            applies=lambda c: surface(c) and bool(sysfs.run(["modinfo", "-F", "name", "surface_gpe"]).stdout.strip()),
            detect=_file_state(GPE_CONF), apply=_gpe_apply, revert=lambda c, l: remove_managed(GPE_CONF),
            recommend=lambda c: c.dev.model_in(("Surface Pro 4", "Surface Pro 5", "Surface Pro (5th Gen)")),
            reboot=True),
        Fix("no-wake", "Device wakes up in a bag",
            "Stops the USB controller and lid from waking the device. Wake it with the power button instead.",
            "Sleep", WIKI + "Known-Issues-and-FAQ", "optional",
            applies=lambda c: surface(c) and sysfs.p("/proc/acpi/wakeup").exists() and sysfs.have("systemctl"),
            detect=_file_state(NOWAKE_UNIT), apply=_nowake_apply, revert=_nowake_revert,
            recommend=lambda c: c.dev.family == "go"),
        Fix("wifi-powersave", "Wi-Fi drops (Marvell 88W8897 cards)",
            "Turns off Wi-Fi power saving and MAC randomisation while scanning in NetworkManager. "
            "Reported to help on some devices, not all.",
            "Wi-Fi", WIKI + "Marvell-88W8897-quirks", "optional",
            applies=lambda c: surface(c) and _marvell(c) and sysfs.p("/etc/NetworkManager").is_dir(),
            detect=_file_state(NM_CONF), apply=_wifi_apply, revert=_wifi_revert),
        Fix("gnome-rightclick", "Right-click on the Type Cover (GNOME)",
            "Switches the touchpad to click areas so the bottom-right corner is a right-click.",
            "Input", WIKI + "Known-Issues-and-FAQ", "optional", scope="user",
            applies=lambda c: surface(c) and _gnome(c),
            detect=_gnome_detect, apply=_gnome_apply, revert=_gnome_revert),
        Fix("thermald-config", "CPU throttles hard when hot",
            "Installs the linux-surface thermald configuration for this model. Newer thermald may already do "
            "the right thing from ACPI; try that first.",
            "Thermals", WIKI + "Thermald-setup-and-configuration", "optional",
            applies=lambda c: surface(c) and c.dev.cpu_vendor == "intel" and sysfs.have("thermald"),
            detect=_thermal_detect, apply=_thermal_apply, revert=_thermal_revert),
        Fix("ipu3-firmware", "Camera firmware missing (IPU3)",
            "Installs ipu3-fw.bin, which the Intel camera processor needs to load at boot.",
            "Camera", WIKI + "Camera-Support", "recommended",
            applies=lambda c: surface(c) and _ipu3(c),
            detect=lambda c: "applied" if _ipu3_have(c) else "available",
            apply=_ipu3_apply, reboot=True),
        # --- advisories: real issues the wiki documents but that cannot be fixed safely from software
        Fix("s5-shutdown", "Shutdown does not power off (12th gen Intel)",
            "On this generation a normal shutdown can leave the device on. The workaround boots a tiny EFI "
            "shell to power off, and needs Secure Boot off or a signed shell.",
            "Boot", WIKI + "Intel-12th-Generation-Devices-Issues", "advisory",
            applies=lambda c: c.dev.model_in(INTEL_12TH), detect=lambda c: "available",
            steps="Follow the 'Power state/operation workaround via EFI Shell' section of the wiki page."),
        Fix("tlp-warning", "TLP can slow Surface devices",
            "TLP is installed and running. The linux-surface project warns that it can cause slowdowns and "
            "hangs unless configured carefully. The performance profile control here may be overridden by it.",
            "Thermals", "https://github.com/linux-surface/linux-surface#notes", "advisory",
            applies=lambda c: surface(c) and sysfs.have("tlp") and sysfs.run(["systemctl", "is-active", "tlp"]).stdout.strip() == "active",
            detect=lambda c: "available",
            steps="Disable TLP (sudo systemctl disable --now tlp) and use power-profiles-daemon, or review its config."),
        Fix("book3-nvidia", "nvidia-smi cannot talk to the GPU (Surface Book 3)",
            "Usually Secure Boot refusing the unsigned NVIDIA module.",
            "Graphics", WIKI + "Surface-Book-3", "advisory",
            applies=lambda c: c.dev.model_in(("Surface Book 3",)), detect=lambda c: "available",
            steps="Turn Secure Boot off, or sign the NVIDIA module and enrol your key, then reinstall the driver."),
    ]
