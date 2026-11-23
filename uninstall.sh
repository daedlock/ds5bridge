#!/bin/sh
# Remove ds5bridge and give any connected controller back to users. Run as root.
set -eu
[ "$(id -u)" = 0 ] || { echo "run as root: sudo ./uninstall.sh" >&2; exit 1; }
rm -f /etc/ds5bridge/enabled
systemctl stop 'ds5bridge@*.service' 2>/dev/null || true
rm -f /etc/udev/rules.d/72-ds5bridge.rules /etc/systemd/system/ds5bridge@.service
rm -rf /usr/local/lib/ds5bridge /run/ds5bridge
rmdir /etc/ds5bridge 2>/dev/null || true
systemctl daemon-reload
udevadm control --reload
for dev in /sys/bus/hid/devices/0005:054C:0CE6.* /sys/bus/hid/devices/0005:054C:0DF2.*; do
    [ -e "$dev" ] && udevadm trigger --action=change --parent-match="$dev"
done
echo "ds5bridge removed."
