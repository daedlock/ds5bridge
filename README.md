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

## Install (Arch Linux)

```sh
git clone https://github.com/daedlock/ds5bridge && cd ds5bridge/packaging/arch
makepkg -si
```

That's it: there is nothing to configure. From then on every Bluetooth DualSense (and DualSense
Edge) is bridged automatically when it connects; updates restart the bridge and removing the package
(`sudo pacman -R ds5bridge-git`) hands controllers straight back to apps.

```sh
ds5bridge status          # connected controllers and bridge state
sudo ds5bridge disable    # stop bridging (controllers go back to normal)
sudo ds5bridge enable     # turn it back on
journalctl -u 'ds5bridge@*'
```

If the bridge ever fails, the controller is handed back to apps automatically so it keeps working.

The package also ships a fix for a Proton bug that makes Steam Input's virtual gamepad show up twice
(a dead copy in XInput slot 0 makes games ignore the controller): see
`data/72-steam-virtual-gamepad-no-js.rules`.

### Files

| Path | Purpose |
|---|---|
| `/usr/bin/ds5bridge` | CLI and bridge |
| `/usr/lib/udev/rules.d/72-ds5bridge.rules` | hide the Bluetooth original, start the bridge on connect |
| `/usr/lib/udev/rules.d/72-steam-virtual-gamepad-no-js.rules` | Proton duplicate-controller fix |
| `/usr/lib/systemd/system/ds5bridge@.service` | one bridge per connected controller |

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
sudo PYTHONPATH=. python3 -m ds5bridge run -v --hide-physical   # run in the foreground
```

Standard library only; Python 3.10+.
