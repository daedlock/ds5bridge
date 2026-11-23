import argparse
import logging
import os
import sys

from . import bridge, hidraw, system

__version__ = "0.1.0"


def cmd_status(_args) -> int:
    disabled = system.DISABLED_FLAG.exists()
    print(f"ds5bridge {__version__}: {'disabled' if disabled else 'enabled'}"
          f"{' (fallback: bridge failed, controller handed back)' if system.BYPASS_FLAG.exists() else ''}")
    physical = system.physical_controllers()
    virtual = {v.bridged_by.lower(): v for v in system.virtual_controllers()}
    if not physical:
        print("no Bluetooth DualSense connected")
    for c in physical:
        v = virtual.get(c.uniq.lower())
        state = system.service_state(c.hidraw) if c.hidraw else "no hidraw node"
        print(f"  {c.uniq}  bluetooth /dev/{c.hidraw}  bridge {state}"
              + (f"  ->  wired /dev/{v.hidraw}" if v else ""))
    return 0


def cmd_toggle(args) -> int:
    if not system.is_root():
        print(f"run as root: sudo ds5bridge {args.command}", file=sys.stderr)
        return 1
    system.set_enabled(args.command == "enable")
    return cmd_status(args)


def cmd_run(args) -> int:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s" if os.environ.get("INVOCATION_ID") else
                        "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    node = args.hidraw
    if node is None:
        found = hidraw.find_dualsense(hidraw.BUS_BLUETOOTH)
        if not found:
            logging.error("no Bluetooth DualSense found")
            return 1
        node = found[0].node
    if args.hide_physical:
        system.BYPASS_FLAG.unlink(missing_ok=True)
    return bridge.run(node, hide_physical=args.hide_physical)


def cmd_hand_back(args) -> int:
    # systemd ExecStopPost: only act when the bridge failed, not on a normal disconnect or stop.
    if os.environ.get("SERVICE_RESULT", "success") != "success":
        system.hand_back(args.hidraw)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="ds5bridge",
                                 description="Use a Bluetooth DualSense as if it were plugged in over USB.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("status", help="show connected controllers and bridge state (default)")
    sub.add_parser("enable", help="bridge Bluetooth DualSenses automatically (default after install)")
    sub.add_parser("disable", help="stop bridging and give controllers back to apps directly")
    run = sub.add_parser("run", help="bridge one controller in the foreground (used by the service)")
    run.add_argument("--hidraw", help="physical Bluetooth DualSense hidraw node (default: first one found)")
    run.add_argument("--hide-physical", action="store_true", help="make the physical controller's nodes root-only")
    run.add_argument("-v", "--verbose", action="store_true", help="log every feature request and output report")
    hand_back = sub.add_parser("hand-back", help=argparse.SUPPRESS)
    hand_back.add_argument("hidraw")
    args = ap.parse_args()
    handlers = {None: cmd_status, "status": cmd_status, "enable": cmd_toggle, "disable": cmd_toggle,
                "run": cmd_run, "hand-back": cmd_hand_back}
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
