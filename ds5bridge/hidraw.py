"""Minimal Linux hidraw helpers (ioctls from <linux/hidraw.h>), standard library only."""

import fcntl
import glob
import os
import struct
from dataclasses import dataclass

_IOC_WRITE = 1
_IOC_READ = 2


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


HIDIOCGRDESCSIZE = _ioc(_IOC_READ, 0x01, 4)
HIDIOCGRDESC = _ioc(_IOC_READ, 0x02, 4 + 4096)
HIDIOCGRAWINFO = _ioc(_IOC_READ, 0x03, 8)


def HIDIOCGRAWNAME(length: int) -> int:
    return _ioc(_IOC_READ, 0x04, length)


def HIDIOCGRAWPHYS(length: int) -> int:
    return _ioc(_IOC_READ, 0x05, length)


def HIDIOCSFEATURE(length: int) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, 0x06, length)


def HIDIOCGFEATURE(length: int) -> int:
    return _ioc(_IOC_READ | _IOC_WRITE, 0x07, length)


def HIDIOCGRAWUNIQ(length: int) -> int:
    return _ioc(_IOC_READ, 0x08, length)


BUS_USB = 0x03
BUS_BLUETOOTH = 0x05
BUS_VIRTUAL = 0x06
BUS_NAMES = {BUS_USB: "usb", BUS_BLUETOOTH: "bluetooth", BUS_VIRTUAL: "virtual"}

SONY_VID = 0x054C
DUALSENSE_PIDS = {0x0CE6: "DualSense", 0x0DF2: "DualSense Edge"}


@dataclass
class HidrawInfo:
    node: str
    bus: int
    vendor: int
    product: int
    name: str
    phys: str
    uniq: str

    @property
    def bus_name(self) -> str:
        return BUS_NAMES.get(self.bus, hex(self.bus))


def _ioctl_str(fd: int, request_for_len, length: int = 256) -> str:
    buf = bytearray(length)
    n = fcntl.ioctl(fd, request_for_len(length), buf, True)
    return bytes(buf[:n]).split(b"\0", 1)[0].decode(errors="replace")


def get_info(fd: int, node: str) -> HidrawInfo:
    raw = bytearray(8)
    fcntl.ioctl(fd, HIDIOCGRAWINFO, raw, True)
    bus, vendor, product = struct.unpack("<Ihh", raw)
    return HidrawInfo(
        node=node,
        bus=bus,
        vendor=vendor & 0xFFFF,
        product=product & 0xFFFF,
        name=_ioctl_str(fd, HIDIOCGRAWNAME),
        phys=_ioctl_str(fd, HIDIOCGRAWPHYS),
        uniq=_ioctl_str(fd, HIDIOCGRAWUNIQ),
    )


def get_report_descriptor(fd: int) -> bytes:
    size = bytearray(4)
    fcntl.ioctl(fd, HIDIOCGRDESCSIZE, size, True)
    (length,) = struct.unpack("<I", size)
    buf = bytearray(struct.pack("<I", length) + bytes(4096))
    fcntl.ioctl(fd, HIDIOCGRDESC, buf, True)
    return bytes(buf[4 : 4 + length])


def get_feature(fd: int, report_id: int, length: int) -> bytes:
    buf = bytearray(length)
    buf[0] = report_id
    n = fcntl.ioctl(fd, HIDIOCGFEATURE(length), buf, True)
    return bytes(buf[:n])


def set_feature(fd: int, data: bytes) -> int:
    buf = bytearray(data)
    return fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf, True)


def find_dualsense(bus: int | None = None) -> list[HidrawInfo]:
    """Return every hidraw node we can open that belongs to a DualSense (optionally on one bus)."""
    found = []
    for node in sorted(glob.glob("/dev/hidraw*")):
        try:
            fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            info = get_info(fd, node)
        except OSError:
            continue
        finally:
            os.close(fd)
        if info.vendor == SONY_VID and info.product in DUALSENSE_PIDS and (bus is None or info.bus == bus):
            found.append(info)
    return found
