"""Thin wrapper around /dev/uhid (struct layouts from <linux/uhid.h>, all packed little-endian)."""

import os
import struct

UHID_DESTROY = 1
UHID_START = 2
UHID_STOP = 3
UHID_OPEN = 4
UHID_CLOSE = 5
UHID_OUTPUT = 6
UHID_GET_REPORT = 9
UHID_GET_REPORT_REPLY = 10
UHID_CREATE2 = 11
UHID_INPUT2 = 12
UHID_SET_REPORT = 13
UHID_SET_REPORT_REPLY = 14

UHID_FEATURE_REPORT = 0
UHID_OUTPUT_REPORT = 1
UHID_INPUT_REPORT = 2

EVENT_NAMES = {
    UHID_START: "start",
    UHID_STOP: "stop",
    UHID_OPEN: "open",
    UHID_CLOSE: "close",
    UHID_OUTPUT: "output",
    UHID_GET_REPORT: "get_report",
    UHID_SET_REPORT: "set_report",
}

DATA_MAX = 4096
_CREATE2 = struct.Struct("<I128s64s64sHHIIII")
EVENT_SIZE = _CREATE2.size + DATA_MAX  # sizeof(struct uhid_event) == 4376


class Uhid:
    def __init__(self, path: str = "/dev/uhid") -> None:
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)

    def create(self, *, name: str, phys: str, uniq: str, descriptor: bytes, bus: int, vendor: int,
               product: int, version: int = 0, country: int = 0) -> None:
        head = _CREATE2.pack(UHID_CREATE2, name.encode(), phys.encode(), uniq.encode(), len(descriptor),
                             bus, vendor, product, version, country)
        os.write(self.fd, head + descriptor.ljust(DATA_MAX, b"\0"))

    def input(self, data: bytes) -> None:
        os.write(self.fd, struct.pack("<IH", UHID_INPUT2, len(data)) + data)

    def get_report_reply(self, request_id: int, err: int, data: bytes = b"") -> None:
        os.write(self.fd, struct.pack("<IIHH", UHID_GET_REPORT_REPLY, request_id, err, len(data)) + data)

    def set_report_reply(self, request_id: int, err: int) -> None:
        os.write(self.fd, struct.pack("<IIH", UHID_SET_REPORT_REPLY, request_id, err))

    def destroy(self) -> None:
        os.write(self.fd, struct.pack("<I", UHID_DESTROY))

    def read_event(self) -> tuple[int, dict]:
        buf = os.read(self.fd, EVENT_SIZE)
        (etype,) = struct.unpack_from("<I", buf, 0)
        payload = buf[4:]
        if etype == UHID_OUTPUT:
            size, rtype = struct.unpack_from("<HB", payload, DATA_MAX)
            return etype, {"data": payload[:size], "rtype": rtype}
        if etype == UHID_GET_REPORT:
            request_id, rnum, rtype = struct.unpack_from("<IBB", payload, 0)
            return etype, {"id": request_id, "rnum": rnum, "rtype": rtype}
        if etype == UHID_SET_REPORT:
            request_id, rnum, rtype, size = struct.unpack_from("<IBBH", payload, 0)
            return etype, {"id": request_id, "rnum": rnum, "rtype": rtype, "data": payload[8 : 8 + size]}
        if etype == UHID_START:
            (dev_flags,) = struct.unpack_from("<Q", payload, 0)
            return etype, {"dev_flags": dev_flags}
        return etype, {}

    def close(self) -> None:
        os.close(self.fd)
