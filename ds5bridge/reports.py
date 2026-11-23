"""DualSense report translation between Bluetooth and USB framing.

Layouts follow the Linux hid-playstation driver and were verified against captures of real hardware:
the 63-byte input state and 47-byte output block are identical on both transports; Bluetooth wraps
them in a different header and appends a CRC32 (seeded with 0xA1 input / 0xA2 output / 0xA3 feature).
"""

import zlib

USB_INPUT_ID = 0x01
USB_INPUT_SIZE = 64
USB_OUTPUT_ID = 0x02

BT_INPUT_ID = 0x31
BT_INPUT_SIZE = 78
BT_OUTPUT_ID = 0x31
BT_OUTPUT_SIZE = 78
BT_OUTPUT_TAG = 0x10

COMMON_INPUT_SIZE = 63
COMMON_OUTPUT_SIZE = 47

# Each seed is the Bluetooth HID transaction header the report travels with.
INPUT_CRC_SEED = 0xA1  # DATA | INPUT
OUTPUT_CRC_SEED = 0xA2  # DATA | OUTPUT
FEATURE_CRC_SEED = 0xA3  # DATA | FEATURE (GET_REPORT replies)
SET_FEATURE_CRC_SEED = 0x53  # SET_REPORT | FEATURE

PAIRING_INFO_ID = 0x09


def bt_crc(seed: int, data: bytes) -> int:
    return zlib.crc32(bytes([seed]) + data) & 0xFFFFFFFF


def bt_crc_ok(seed: int, report: bytes) -> bool:
    return len(report) > 4 and bt_crc(seed, report[:-4]) == int.from_bytes(report[-4:], "little")


def bt_input_to_usb(report: bytes, verify_crc: bool = True) -> bytes | None:
    """Turn a Bluetooth 0x31 input report into the USB 0x01 report, or None if it isn't one."""
    if len(report) != BT_INPUT_SIZE or report[0] != BT_INPUT_ID:
        return None
    if verify_crc and not bt_crc_ok(INPUT_CRC_SEED, report):
        return None
    return bytes([USB_INPUT_ID]) + report[2 : 2 + COMMON_INPUT_SIZE]


class OutputTranslator:
    """Turns USB 0x02 output reports into Bluetooth 0x31 ones (sequence number + CRC)."""

    def __init__(self) -> None:
        self.seq = 0

    def usb_to_bt(self, report: bytes) -> bytes | None:
        if not report or report[0] != USB_OUTPUT_ID:
            return None
        out = bytearray(BT_OUTPUT_SIZE)
        out[0] = BT_OUTPUT_ID
        out[1] = self.seq << 4
        out[2] = BT_OUTPUT_TAG
        out[3 : 3 + COMMON_OUTPUT_SIZE] = report[1 : 1 + COMMON_OUTPUT_SIZE].ljust(COMMON_OUTPUT_SIZE, b"\0")
        out[-4:] = bt_crc(OUTPUT_CRC_SEED, bytes(out[:-4])).to_bytes(4, "little")
        self.seq = (self.seq + 1) & 0x0F
        return bytes(out)


def bt_feature_to_usb(report: bytes) -> bytes:
    """Bluetooth feature reports carry a CRC32 in their last 4 bytes; over USB those bytes are zero."""
    return report[:-4] + bytes(4) if len(report) > 4 else report


def usb_feature_to_bt(report: bytes) -> bytes:
    """A USB SET_FEATURE report with its last 4 bytes replaced by the CRC32 Bluetooth requires."""
    if len(report) <= 4:
        return report
    return report[:-4] + bt_crc(SET_FEATURE_CRC_SEED, report[:-4]).to_bytes(4, "little")


def spoof_pairing_mac(report: bytes) -> bytes:
    """Give the virtual pad a different MAC (locally administered bit) in feature report 0x09.

    The physical controller stays bound to the kernel driver, which rejects a second device with the
    same MAC. Bytes 1..6 hold the MAC least-significant octet first, so byte 6 is the first octet.
    """
    if len(report) < 7 or report[0] != PAIRING_INFO_ID:
        return report
    patched = bytearray(report)
    patched[6] ^= 0x02
    return bytes(patched)
