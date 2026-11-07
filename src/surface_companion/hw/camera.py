"""Camera drivers and userspace stack."""
from __future__ import annotations

import re

from .. import sysfs

# Source: linux-surface wiki, Camera-Support. Matched by model prefix, longest first.
ISP = [
    ("Surface Go 4", "IPU6", "partial"), ("Surface Go", "IPU3", "ok"),
    ("Surface Pro 7+", "IPU6", "partial"), ("Surface Pro 7", "IPU4", "none"),
    ("Surface Pro 8", "IPU6", "partial"), ("Surface Pro 9", "IPU6", "partial"),
    ("Surface Pro 10", "IPU6", "none"), ("Surface Pro 11", "IPU7", "none"),
    ("Surface Pro 4", "IPU3", "ok"), ("Surface Pro 5", "IPU3", "ok"), ("Surface Pro 6", "IPU3", "ok"),
    ("Surface Pro (5th Gen)", "IPU3", "ok"),
    ("Surface Book 3", "IPU4", "none"), ("Surface Book", "IPU3", "ok"),
    ("Surface Laptop Studio", "UVC", "ok"), ("Surface Laptop Go", "UVC", "ok"),
    ("Surface Laptop 4", "UVC", "ok"), ("Surface Laptop 5", "UVC", "ok"),
    ("Surface Laptop 3", "UVC / IPU4", "ok"),
    ("Surface Laptop 2", "IPU3", "partial"), ("Surface Laptop", "IPU3", "partial"),
]
MODULES = ["ipu3_cio2", "ipu3_imgu", "intel_ipu6", "intel_ipu6_isys", "ov5693", "ov8865", "ov7251",
           "ov13858", "ov9734", "dw9719", "uvcvideo", "int3472_discrete", "int3472_tps68470"]
FW = ["/lib/firmware/intel/ipu3-fw.bin", "/lib/firmware/intel/ipu3-fw.bin.zst", "/lib/firmware/intel/ipu3-fw.bin.xz",
      "/usr/lib/firmware/intel/ipu3-fw.bin", "/usr/lib/firmware/intel/ipu3-fw.bin.zst"]


def lookup(model: str) -> tuple[str, str]:
    for prefix, isp, status in sorted(ISP, key=lambda t: -len(t[0])):
        if model.startswith(prefix):
            return isp, status
    return "unknown", "unknown"


def state(model: str = "") -> dict:
    loaded = set()
    for ln in (sysfs.read("/proc/modules", "") or "").splitlines():
        loaded.add(ln.split()[0])
    nodes = []
    for d in sysfs.glob("/sys/class/video4linux/video*"):
        nodes.append({"node": d.name, "name": sysfs.read(d / "name", d.name)})
    isp, status = lookup(model)
    return {
        "isp": isp,
        "wiki_status": status,
        "modules": sorted(m for m in MODULES if m in loaded),
        "video_nodes": nodes,
        "ipu3_firmware": any(sysfs.p(f).exists() for f in FW),
        "libcamera": sysfs.have("cam") or any(sysfs.glob("/usr/lib/*/libcamera.so*")) or any(sysfs.glob("/usr/lib64/libcamera.so*")),
        "pipewire_libcamera": any(sysfs.glob("/usr/lib/*/spa-0.2/libcamera/*")) or any(sysfs.glob("/usr/lib64/spa-0.2/libcamera/*")),
    }
