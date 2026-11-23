#!/usr/bin/env python3
"""Capture a DualSense's HID identity: report descriptor, feature reports and sample input reports.

Usage: tools/capture.py [--bus usb|bluetooth] [--wait SECONDS] [--inputs N]
Writes captures/<bus>-<timestamp>.json and prints a summary.
"""

import argparse
import datetime
import json
import os
import pathlib
import select
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ds5bridge import hidraw  # noqa: E402

# Feature reports the Linux driver, Steam and games ask for: calibration, pairing info (MAC), firmware info.
FEATURES = {0x05: 41, 0x09: 20, 0x20: 64}
CAPTURE_DIR = pathlib.Path(__file__).resolve().parent.parent / "captures"


def capture(info: hidraw.HidrawInfo, n_inputs: int) -> dict:
    fd = os.open(info.node, os.O_RDWR | os.O_NONBLOCK)
    try:
        result = {
            "captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "node": info.node,
            "bus": info.bus_name,
            "vendor": f"{info.vendor:04x}",
            "product": f"{info.product:04x}",
            "name": info.name,
            "phys": info.phys,
            "uniq": info.uniq,
            "report_descriptor": hidraw.get_report_descriptor(fd).hex(),
            "features": {},
            "inputs": [],
        }
        for report_id, length in FEATURES.items():
            try:
                result["features"][f"0x{report_id:02x}"] = hidraw.get_feature(fd, report_id, length).hex()
            except OSError as e:
                result["features"][f"0x{report_id:02x}"] = f"error: {e}"
        deadline = time.monotonic() + 5
        while len(result["inputs"]) < n_inputs and time.monotonic() < deadline:
            if select.select([fd], [], [], 0.5)[0]:
                result["inputs"].append(os.read(fd, 512).hex())
    finally:
        os.close(fd)
    udev = subprocess.run(["udevadm", "info", "-q", "property", "-n", info.node], capture_output=True, text=True)
    result["udev"] = udev.stdout.splitlines()
    sysdev = pathlib.Path("/sys/class/hidraw") / pathlib.Path(info.node).name / "device"
    result["sysfs_path"] = str(sysdev.resolve())
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bus", choices=["usb", "bluetooth"], help="only capture a DualSense on this bus")
    ap.add_argument("--wait", type=float, default=0, help="seconds to wait for a matching device to appear")
    ap.add_argument("--inputs", type=int, default=20, help="number of input reports to sample")
    args = ap.parse_args()
    bus = {"usb": hidraw.BUS_USB, "bluetooth": hidraw.BUS_BLUETOOTH}.get(args.bus)

    deadline = time.monotonic() + args.wait
    while True:
        devices = hidraw.find_dualsense(bus)
        if devices or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    if not devices:
        print("no matching DualSense hidraw node found", file=sys.stderr)
        return 1

    CAPTURE_DIR.mkdir(exist_ok=True)
    for info in devices:
        data = capture(info, args.inputs)
        out = CAPTURE_DIR / f"{data['bus']}-{datetime.datetime.now():%Y%m%d-%H%M%S}.json"
        out.write_text(json.dumps(data, indent=2) + "\n")
        sizes = sorted({len(bytes.fromhex(x)) for x in data["inputs"]})
        ids = sorted({x[:2] for x in data["inputs"]})
        print(f"{info.node} {data['bus']} {data['name']!r} uniq={data['uniq']}")
        print(f"  descriptor: {len(data['report_descriptor']) // 2} bytes")
        for rid, val in data["features"].items():
            print(f"  feature {rid}: {val if val.startswith('error') else str(len(val) // 2) + ' bytes'}")
        print(f"  inputs: {len(data['inputs'])} reports, ids {ids}, sizes {sizes}")
        print(f"  saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
