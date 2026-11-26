"""System integration: controller discovery via sysfs, enable/disable, and handing the controller back."""

import os
import pathlib
import subprocess
from dataclasses import dataclass

HID_DEVICES = pathlib.Path("/sys/bus/hid/devices")
DISABLED_FLAG = pathlib.Path("/etc/ds5bridge/disabled")  # managed by `ds5bridge disable/enable`
BYPASS_FLAG = pathlib.Path("/run/ds5bridge/bypass")  # set when the bridge fails
IDLE_TIMEOUT_FILE = pathlib.Path("/etc/ds5bridge/idle-timeout")  # managed by `ds5bridge idle-timeout`
DEFAULT_IDLE_MINUTES = 5
PHYSICAL_GLOBS = ("0005:054C:0CE6.*", "0005:054C:0DF2.*")  # Bluetooth DualSense / DualSense Edge
VIRTUAL_PHYS_PREFIX = "ds5bridge/"


@dataclass
class Controller:
    sysfs: pathlib.Path
    hidraw: str | None
    uniq: str
    bridged_by: str | None  # for a virtual pad: the MAC of the physical controller it mirrors

    @property
    def is_virtual(self) -> bool:
        return self.bridged_by is not None


def _uevent(dev: pathlib.Path) -> dict[str, str]:
    try:
        lines = (dev / "uevent").read_text().splitlines()
    except OSError:
        return {}
    return dict(line.split("=", 1) for line in lines if "=" in line)


def _controller(dev: pathlib.Path) -> Controller:
    props = _uevent(dev)
    hidraw = next((p.name for p in (dev / "hidraw").glob("hidraw*")), None) if (dev / "hidraw").is_dir() else None
    phys = props.get("HID_PHYS", "")
    return Controller(dev, hidraw, props.get("HID_UNIQ", ""),
                      phys[len(VIRTUAL_PHYS_PREFIX):] if phys.startswith(VIRTUAL_PHYS_PREFIX) else None)


def physical_controllers() -> list[Controller]:
    return [_controller(d) for pattern in PHYSICAL_GLOBS for d in sorted(HID_DEVICES.glob(pattern))]


def virtual_controllers() -> list[Controller]:
    found = [_controller(d) for d in sorted(HID_DEVICES.glob("0003:054C:*"))]
    return [c for c in found if c.is_virtual]


def service_name(hidraw: str) -> str:
    return f"ds5bridge@{hidraw}.service"


def service_state(hidraw: str) -> str:
    out = subprocess.run(["systemctl", "is-active", service_name(hidraw)], capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


def retrigger(action: str) -> None:
    """Re-run udev rules for connected Bluetooth DualSenses (applies or lifts hiding, starts the bridge)."""
    subprocess.run(["udevadm", "control", "--reload"], check=False)
    for c in physical_controllers():
        subprocess.run(["udevadm", "trigger", f"--action={action}", f"--parent-match={c.sysfs.resolve()}"], check=False)
    subprocess.run(["udevadm", "settle", "--timeout=5"], check=False)


def set_enabled(enabled: bool) -> None:
    if enabled:
        DISABLED_FLAG.unlink(missing_ok=True)
        try:
            DISABLED_FLAG.parent.rmdir()
        except OSError:
            pass
        BYPASS_FLAG.unlink(missing_ok=True)
        retrigger("add")
    else:
        DISABLED_FLAG.parent.mkdir(parents=True, exist_ok=True)
        DISABLED_FLAG.touch()
        subprocess.run(["systemctl", "stop", "ds5bridge@*.service"], check=False)
        retrigger("change")


def idle_minutes() -> int:
    try:
        return max(0, int(IDLE_TIMEOUT_FILE.read_text()))
    except (OSError, ValueError):
        return DEFAULT_IDLE_MINUTES


def set_idle_minutes(minutes: int) -> None:
    if minutes == DEFAULT_IDLE_MINUTES:
        IDLE_TIMEOUT_FILE.unlink(missing_ok=True)
        try:
            IDLE_TIMEOUT_FILE.parent.rmdir()
        except OSError:
            pass
        return
    IDLE_TIMEOUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    IDLE_TIMEOUT_FILE.write_text(f"{minutes}\n")


def hand_back(hidraw_node: str) -> None:
    """After a failed bridge run: let users use the physical controller directly again."""
    BYPASS_FLAG.parent.mkdir(parents=True, exist_ok=True)
    BYPASS_FLAG.touch()
    sysfs = pathlib.Path("/sys/class/hidraw") / pathlib.Path(hidraw_node).name / "device"
    if sysfs.exists():
        subprocess.run(["udevadm", "trigger", "--action=change", f"--parent-match={sysfs.resolve()}"], check=False)


def is_root() -> bool:
    return os.geteuid() == 0
