# ds5bridge

Use a **Bluetooth** DualSense on Linux as if it were plugged in over **USB**.

Many Windows games only enable their native PlayStation support (PS button prompts, adaptive
triggers, lightbar) for a wired DualSense, and under Proton a Bluetooth DualSense isn't recognised
at all. ds5bridge reads the Bluetooth controller and presents a virtual wired DualSense through
`/dev/uhid`. The kernel, Steam, Proton and SDL see it as a real USB controller.

Proven with Lies of P (Proton, Steam Input off): PS5 button prompts and working controls, wireless.

## How it works

```
DualSense ─Bluetooth─▶ hidraw ─▶ ds5bridge ─▶ /dev/uhid ─▶ "USB" DualSense ─▶ Steam / Proton / SDL
```

- **Input**: Bluetooth report `0x31` (78 bytes, CRC32) becomes USB report `0x01` (64 bytes). The
  63-byte state block is identical on both transports.
- **Output** (rumble, adaptive triggers, lightbar, LEDs): USB `0x02` becomes Bluetooth `0x31` with a
  sequence number and CRC32.
- **Feature reports** (calibration `0x05`, pairing `0x09`, firmware `0x20`): identical apart from the
  Bluetooth CRC in the last 4 bytes, which is zeroed. They are cached at start so the kernel probe
  never waits on Bluetooth.
- The virtual pad reports a MAC with the locally-administered bit set, because the kernel's
  `hid-playstation` driver rejects two controllers with the same MAC.
- `SET_FEATURE 0x08` (sent by games/Steam to a wired pad) is acknowledged but **not forwarded**: a
  Bluetooth controller obeys it by dropping its link. Pairing/firmware/factory reports are refused.
- The physical Bluetooth controller is hidden from users (mode 0600, no session ACL, ignored by
  libinput), so games and Steam see exactly one controller.

Bluetooth CRC seeds are the HID transaction header: `0xA1` input, `0xA2` output, `0xA3` feature
reply, `0x53` set-feature.

## Install

```sh
sudo ./install.sh
```

The bridge then starts automatically whenever a DualSense (or DualSense Edge) connects over
Bluetooth, and stops when it disconnects. If the bridge fails, the physical controller is handed
back to users (`/run/ds5bridge/bypass`) so it keeps working without the bridge.

- Turn the bridge off without uninstalling: `sudo rm /etc/ds5bridge/enabled` and reconnect the controller.
- Uninstall: `sudo ./uninstall.sh`
- Logs: `journalctl -u 'ds5bridge@*'`

## Steam Input

- Games with native DualSense support: set Steam Input to **off** for the game to get PS prompts and
  DualSense features.
- Games without native DualSense support still need Steam Input **on** (it translates the
  controller to an Xbox pad for them). With the bridge, Steam simply sees a wired DualSense.

## Limits

- HD haptics, the speaker and the headset jack use the USB audio interface, which can't be carried
  over Bluetooth this way.
- Per-game button roles follow the game's PlayStation layout (e.g. Lies of P opens the menu with the
  touchpad, not Options).

## Development

```sh
python3 -m unittest discover -s tests   # translation tests (plus real-hardware checks if captures/ exists)
tools/capture.py --bus bluetooth        # dump descriptor, feature and input reports of a connected pad
sudo PYTHONPATH=. python3 -m ds5bridge -v --hide-physical   # run in the foreground
```

Standard library only; Python 3.10+.
