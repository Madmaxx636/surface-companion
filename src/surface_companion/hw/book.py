"""Surface Book: clipboard detach (DTX) and the discrete GPU."""
from __future__ import annotations

import fcntl
import os
import struct

from .. import sysfs

DTX = "/dev/surface/dtx"
_MODES = {0: "tablet", 1: "laptop", 2: "studio"}
_LATCH = {0: "closed", 1: "opened"}


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (0xA5 << 8) | nr


IOC_NONE, IOC_READ = 0, 2
LATCH_REQUEST = _ioc(IOC_NONE, 0x25, 0)
GET_DEVICE_MODE = _ioc(IOC_READ, 0x2A, 2)
GET_LATCH_STATUS = _ioc(IOC_READ, 0x2B, 2)
GET_BASE_INFO = _ioc(IOC_READ, 0x29, 4)


def _open(flags=os.O_RDWR):
    return os.open(sysfs.p(DTX), flags)


def _read_u16(fd, req) -> int:
    buf = bytearray(2)
    fcntl.ioctl(fd, req, buf, True)
    return struct.unpack("<H", buf)[0]


def dgpu() -> dict | None:
    for d in sysfs.glob("/sys/bus/pci/devices/*"):
        cls = sysfs.read(d / "class", "") or ""
        if sysfs.read(d / "vendor") == "0x10de" and cls.startswith("0x03"):
            drv = d / "driver"
            return {
                "address": d.name,
                "device_id": sysfs.read_int(d / "device"),        # PCI device id, e.g. 0x1c8d (Pascal) or 0x2191 (Turing)
                "driver": os.path.basename(os.path.realpath(sysfs.p(drv))) if sysfs.p(drv).exists() else None,
                "power_state": sysfs.read(d / "power_state"),
                "runtime_status": sysfs.read(d / "power/runtime_status"),
                "power_control": sysfs.read(d / "power/control"),
            }
    return None


def state() -> dict:
    if not sysfs.p(DTX).exists():
        return {"dtx": False, "dgpu": dgpu()}
    out = {"dtx": True, "dgpu": dgpu(), "mode": None, "latch": None, "base_attached": None}
    try:
        fd = _open(os.O_RDONLY)
        try:
            out["mode"] = _MODES.get(_read_u16(fd, GET_DEVICE_MODE))
            out["latch"] = _LATCH.get(_read_u16(fd, GET_LATCH_STATUS))
            buf = bytearray(4)
            fcntl.ioctl(fd, GET_BASE_INFO, buf, True)
            out["base_attached"] = struct.unpack("<HH", buf)[0] == 1   # SDTX_BASE_ATTACHED
        finally:
            os.close(fd)
    except OSError as e:
        out["error"] = str(e)
    return out


def detach() -> None:
    """Ask the embedded controller to release the clipboard latch."""
    st = state()
    if st.get("mode") == "tablet":
        raise RuntimeError("The screen is already detached")
    fd = _open()
    try:
        fcntl.ioctl(fd, LATCH_REQUEST)
    finally:
        os.close(fd)


def set_dgpu_power(always_on: bool) -> None:
    g = dgpu()
    if not g:
        raise RuntimeError("No discrete GPU found")
    sysfs.write(f"/sys/bus/pci/devices/{g['address']}/power/control", "on" if always_on else "auto")
