"""Bridge loop: physical Bluetooth DualSense (hidraw) <-> virtual USB DualSense (uhid)."""

import errno
import logging
import os
import pathlib
import select
import signal
import subprocess
import time

from . import descriptor, hidraw, reports, uhid

log = logging.getLogger("ds5bridge")

USB_NAME = "Sony Interactive Entertainment DualSense Wireless Controller"
USB_VERSION = 0x0100
# Asked for by the kernel driver at probe time (and by Steam/games); served from a cache so probing
# never waits on a Bluetooth round trip.
CACHED_FEATURES = {0x05: 41, 0x09: 20, 0x20: 64}
# Wired-only connection commands: acknowledged but never forwarded. Games and Steam send 0x08 02 to a
# USB DualSense; a Bluetooth controller obeys it by dropping its link.
SWALLOWED_SET_FEATURES = {0x08}
# Pairing (0x0A writes a new host + link key), USB audio/haptics test commands (0x80/0x81 drop the
# Bluetooth link), factory and firmware commands: refused outright.
BLOCKED_SET_FEATURES = {0x0A, 0x0C, 0x21, 0x80, 0x81, 0x84, 0x85, 0xA0, 0xE0, *range(0xF0, 0xF8)}
STATS_INTERVAL = 30.0
# Minimum spacing of output reports sent over Bluetooth (100 Hz).
OUTPUT_INTERVAL = 0.01


class PhysicalGone(Exception):
    pass


def physical_device_nodes(hidraw_node: str) -> list[str]:
    """The hidraw node plus every /dev/input node the kernel created for the same HID device."""
    hid_dev = (pathlib.Path("/sys/class/hidraw") / pathlib.Path(hidraw_node).name / "device").resolve()
    nodes = [hidraw_node]
    for child in sorted(hid_dev.glob("input/input*/*")):
        if (child / "dev").exists() and (pathlib.Path("/dev/input") / child.name).exists():
            nodes.append(f"/dev/input/{child.name}")
    return nodes


def hide_nodes(nodes: list[str]) -> None:
    for node in nodes:
        os.chmod(node, 0o600)
        try:
            os.removexattr(node, "system.posix_acl_access")
        except OSError:
            pass
        log.info("hid physical node %s from users", node)


def restore_nodes(nodes: list[str]) -> None:
    """Let udev re-apply its normal permissions and session ACLs."""
    for node in nodes:
        subprocess.run(["udevadm", "trigger", "--action=change", "--name-match", node], check=False)
    subprocess.run(["udevadm", "settle", "--timeout=5"], check=False)
    log.info("restored permissions on %d physical nodes", len(nodes))


class Bridge:
    def __init__(self, node: str) -> None:
        self.node = node
        self.phys = os.open(node, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        self.info = hidraw.get_info(self.phys, node)
        if (self.info.bus != hidraw.BUS_BLUETOOTH or self.info.vendor != hidraw.SONY_VID
                or self.info.product not in hidraw.DUALSENSE_PIDS):
            raise SystemExit(f"{node} is not a Bluetooth DualSense ({self.info})")
        self.features = {}
        for report_id, length in CACHED_FEATURES.items():
            report = reports.bt_feature_to_usb(hidraw.get_feature(self.phys, report_id, length))
            if report_id == reports.PAIRING_INFO_ID:
                report = reports.spoof_pairing_mac(report)
            self.features[report_id] = report
        self.output = reports.OutputTranslator()
        self.dev = uhid.Uhid()
        self.stats = dict.fromkeys(("in", "in_bad", "out", "out_dup", "out_merged", "get", "set"), 0)
        self.pending_output: bytes | None = None
        self.last_output: bytes | None = None
        self.last_output_time = 0.0
        self.swallowed: set[int] = set()

    def start(self) -> None:
        self.dev.create(name=USB_NAME, phys=f"ds5bridge/{self.info.uniq}", uniq="",
                        descriptor=descriptor.USB_REPORT_DESCRIPTOR, bus=hidraw.BUS_USB,
                        vendor=self.info.vendor, product=self.info.product, version=USB_VERSION)
        log.info("bridging %s (%s, %s) -> virtual USB DualSense", self.node, self.info.name, self.info.uniq)

    def close(self) -> None:
        try:
            self.dev.destroy()
        except OSError:
            pass
        self.dev.close()
        os.close(self.phys)

    def on_physical_readable(self) -> None:
        try:
            report = os.read(self.phys, 128)
        except BlockingIOError:
            return
        except OSError as e:
            raise PhysicalGone(str(e)) from e
        if not report:
            raise PhysicalGone("end of file")
        usb = reports.bt_input_to_usb(report)
        if usb is None:
            self.stats["in_bad"] += 1
            return
        self.dev.input(usb)
        self.stats["in"] += 1

    def forward_output(self, report: bytes) -> int:
        """Queue an output report; flush_output() sends it (deduplicated, rate-limited)."""
        if not report or report[0] != reports.USB_OUTPUT_ID:
            log.debug("ignoring output report %s", report[:4].hex())
            return errno.EINVAL
        if self.pending_output is not None:
            self.stats["out_merged"] += 1
        self.pending_output = report
        self.flush_output()
        return 0

    def flush_output(self) -> None:
        # Games in native mode send output state every frame. Over USB that's free; over Bluetooth it
        # fills bluetoothd's socket buffer and the HID link is dropped. Send only changes, newest
        # state wins, at most once per OUTPUT_INTERVAL.
        report = self.pending_output
        if report is None:
            return
        if report == self.last_output:
            self.pending_output = None
            self.stats["out_dup"] += 1
            return
        if time.monotonic() - self.last_output_time < OUTPUT_INTERVAL:
            return
        self.pending_output = None
        try:
            os.write(self.phys, self.output.usb_to_bt(report))
        except OSError as e:
            log.warning("output report to controller failed: %s", e)
            return
        self.last_output = report
        self.last_output_time = time.monotonic()
        self.stats["out"] += 1

    def on_virtual_event(self) -> None:
        try:
            etype, ev = self.dev.read_event()
        except BlockingIOError:
            return
        name = uhid.EVENT_NAMES.get(etype, str(etype))
        if etype == uhid.UHID_OUTPUT:
            if ev["rtype"] == uhid.UHID_OUTPUT_REPORT:
                self.forward_output(ev["data"])
            return
        if etype == uhid.UHID_GET_REPORT:
            self.stats["get"] += 1
            self.reply_get_report(ev)
            return
        if etype == uhid.UHID_SET_REPORT:
            self.stats["set"] += 1
            self.reply_set_report(ev)
            return
        log.debug("virtual device event: %s %s", name, ev)

    def reply_get_report(self, ev: dict) -> None:
        rnum = ev["rnum"]
        if ev["rtype"] == uhid.UHID_FEATURE_REPORT and rnum in self.features:
            log.debug("get feature 0x%02x (cached)", rnum)
            self.dev.get_report_reply(ev["id"], 0, self.features[rnum])
            return
        try:
            report = hidraw.get_feature(self.phys, rnum, 64)
        except OSError as e:
            log.debug("get feature 0x%02x failed on controller: %s", rnum, e)
            self.dev.get_report_reply(ev["id"], e.errno or errno.EIO)
            return
        if reports.bt_crc_ok(reports.FEATURE_CRC_SEED, report):
            report = reports.bt_feature_to_usb(report)
        log.debug("get feature 0x%02x (live, %d bytes)", rnum, len(report))
        self.dev.get_report_reply(ev["id"], 0, report)

    def reply_set_report(self, ev: dict) -> None:
        rnum, data = ev["rnum"], ev["data"]
        if ev["rtype"] == uhid.UHID_OUTPUT_REPORT:
            self.dev.set_report_reply(ev["id"], self.forward_output(data))
            return
        if ev["rtype"] != uhid.UHID_FEATURE_REPORT or rnum in BLOCKED_SET_FEATURES:
            log.info("refused set report 0x%02x (type %d)", rnum, ev["rtype"])
            self.dev.set_report_reply(ev["id"], errno.EPERM)
            return
        if rnum in SWALLOWED_SET_FEATURES:
            if rnum not in self.swallowed:
                log.info("acknowledged wired-only set feature 0x%02x without forwarding (%s)", rnum, data[:4].hex(" "))
                self.swallowed.add(rnum)
            self.dev.set_report_reply(ev["id"], 0)
            return
        try:
            hidraw.set_feature(self.phys, reports.usb_feature_to_bt(data))
            err = 0
        except OSError as e:
            err = e.errno or errno.EIO
        log.info("set feature 0x%02x (%d bytes) -> %s", rnum, len(data), "ok" if not err else os.strerror(err))
        log.debug("  data %s", data.hex(" "))
        self.dev.set_report_reply(ev["id"], err)

    def log_stats(self, level: int = logging.DEBUG) -> None:
        log.log(level, "stats: %s", " ".join(f"{k}={v}" for k, v in self.stats.items()))

    def run(self, stop: list) -> None:
        poller = select.epoll()
        poller.register(self.phys, select.EPOLLIN)
        poller.register(self.dev.fd, select.EPOLLIN)
        next_stats = time.monotonic() + STATS_INTERVAL
        while not stop:
            for fd, _ in poller.poll(OUTPUT_INTERVAL if self.pending_output is not None else 1.0):
                if fd == self.phys:
                    self.on_physical_readable()
                else:
                    self.on_virtual_event()
            self.flush_output()
            if time.monotonic() >= next_stats:
                self.log_stats()
                next_stats += STATS_INTERVAL
        poller.close()


def run(node: str, hide_physical: bool = False) -> int:
    stop: list = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.append(True))
    bridge = Bridge(node)
    hidden = physical_device_nodes(node) if hide_physical else []
    try:
        bridge.start()
        if hidden:
            hide_nodes(hidden)
        bridge.run(stop)
        return 0
    except PhysicalGone as e:
        log.info("controller disconnected (%s)", e)
        return 0
    finally:
        bridge.log_stats(logging.INFO)
        bridge.close()
        if hidden:
            restore_nodes([n for n in hidden if os.path.exists(n)])
