import argparse
import logging
import sys

from . import bridge, hidraw


def main() -> int:
    ap = argparse.ArgumentParser(prog="ds5bridge",
                                 description="Present a Bluetooth DualSense to the system as a wired (USB) DualSense.")
    ap.add_argument("--hidraw", help="physical Bluetooth DualSense hidraw node (default: first one found)")
    ap.add_argument("--hide-physical", action="store_true",
                    help="make the physical controller's device nodes root-only while bridging")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every feature request and output report")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    node = args.hidraw
    if node is None:
        found = hidraw.find_dualsense(hidraw.BUS_BLUETOOTH)
        if not found:
            logging.error("no Bluetooth DualSense found")
            return 1
        node = found[0].node
    return bridge.run(node, hide_physical=args.hide_physical)


if __name__ == "__main__":
    sys.exit(main())
