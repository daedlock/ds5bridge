#!/bin/sh
# Install ds5bridge system-wide and start it for any connected Bluetooth DualSense. Run as root.
set -eu
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "run as root: sudo ./install.sh" >&2; exit 1; }
install -d /usr/local/lib/ds5bridge/ds5bridge /etc/ds5bridge
install -m 0644 ds5bridge/*.py /usr/local/lib/ds5bridge/ds5bridge/
install -m 0755 contrib/ds5bridge-restore /usr/local/lib/ds5bridge/
install -m 0644 contrib/ds5bridge@.service /etc/systemd/system/
install -m 0644 contrib/72-ds5bridge.rules /etc/udev/rules.d/
touch /etc/ds5bridge/enabled
systemctl daemon-reload
udevadm control --reload
# Pick up a controller that is already connected.
for dev in /sys/bus/hid/devices/0005:054C:0CE6.* /sys/bus/hid/devices/0005:054C:0DF2.*; do
    [ -e "$dev" ] && udevadm trigger --action=add --parent-match="$dev"
done
echo "ds5bridge installed; it starts whenever a Bluetooth DualSense connects."
