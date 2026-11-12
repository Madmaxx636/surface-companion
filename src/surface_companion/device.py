"""Identify which Surface this is and what it can do."""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from . import sysfs

# Order matters: the first matching prefix wins.
FAMILIES = [
    ("Surface Laptop Studio", "laptop-studio"),
    ("Surface Laptop Go", "laptop-go"),
    ("Surface Laptop", "laptop"),
    ("Surface Book", "book"),
    ("Surface Pro", "pro"),
    ("Surface Go", "go"),
    ("Surface Studio", "studio"),
    ("Surface 3", "surface3"),
    ("Surface Hub", "hub"),
]

# Which pinctrl driver the initramfs needs for the built-in keyboard (disk encryption page).
PINCTRL_ICELAKE = ("Surface Laptop 3", "Surface Book 3")
PINCTRL_TIGERLAKE = ("Surface Laptop 4", "Surface Laptop 5", "Surface Laptop Studio",
                     "Surface Pro 8", "Surface Pro 9", "Surface Go 4")

# Models the wiki lists as hanging on reboot at the Surface logo.
REBOOT_PCI_MODELS = ("Surface Laptop 3", "Surface Book 3")
# Marvell 88W8897 wifi/bluetooth models (wiki: Marvell-88W8897-quirks).
MARVELL_MODELS = ("Surface Book", "Surface Book 2", "Surface 3", "Surface Laptop", "Surface Laptop 2",
                  "Surface Pro 3", "Surface Pro 4", "Surface Pro 5", "Surface Pro 6", "Surface Pro (5th Gen)")
# Intel 12th gen devices where a full shutdown does not power off (wiki: Intel-12th-Generation-Devices-Issues).
INTEL_12TH = ("Surface Pro 9", "Surface Laptop 5", "Surface Laptop Studio 2", "Surface Go 4")


@dataclass
class Device:
    vendor: str = ""
    model: str = ""          # e.g. "Surface Laptop 3"
    sku: str = ""
    family: str = "unknown"  # laptop, book, pro, go, ...
    cpu_vendor: str = ""     # intel / amd / other
    kernel: str = ""
    surface_kernel: bool = False
    is_surface: bool = False
    has_dtx: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def model_in(self, models: tuple[str, ...]) -> bool:
        """True when this model matches exactly, ignoring trailing 'for Business' style suffixes."""
        base = re.sub(r"\s+(for Business|Consumer|\(.*\))$", "", self.model).strip()
        return base in models

    @property
    def is_pro_4_or_5(self) -> bool:
        """Surface Pro 4 and Pro 5. The 2017 Pro 5 reports plain "Surface Pro" as its DMI name; its SKU gives it away."""
        if self.model_in(("Surface Pro 4", "Surface Pro 5")) or self.model.startswith("Surface Pro (5th Gen)"):
            return True
        return self.model == "Surface Pro" and bool(re.search(r"_(1796|1807)$", self.sku))

    @property
    def is_book(self) -> bool:
        return self.family == "book"


def detect() -> Device:
    d = Device()
    d.vendor = sysfs.read("/sys/class/dmi/id/sys_vendor", "") or ""
    d.model = sysfs.read("/sys/class/dmi/id/product_name", "") or ""
    d.sku = sysfs.read("/sys/class/dmi/id/product_sku", "") or ""
    d.is_surface = d.vendor.lower().startswith("microsoft") and "surface" in d.model.lower()
    for prefix, fam in FAMILIES:
        if d.model.startswith(prefix):
            d.family = fam
            break
    cpuinfo = sysfs.read("/proc/cpuinfo", "") or ""
    if "GenuineIntel" in cpuinfo:
        d.cpu_vendor = "intel"
    elif "AuthenticAMD" in cpuinfo:
        d.cpu_vendor = "amd"
    d.kernel = (sysfs.read("/proc/sys/kernel/osrelease", "") or "")
    d.surface_kernel = "surface" in d.kernel
    d.has_dtx = sysfs.p("/dev/surface/dtx").exists()
    return d
