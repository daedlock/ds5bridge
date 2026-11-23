import glob
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ds5bridge import descriptor, reports, uhid  # noqa: E402


def make_bt_input(common: bytes) -> bytes:
    body = bytes([reports.BT_INPUT_ID, 0x01]) + common + bytes(reports.BT_INPUT_SIZE - 4 - 2 - len(common))
    return body + reports.bt_crc(reports.INPUT_CRC_SEED, body).to_bytes(4, "little")


def report_ids(rdesc: bytes) -> set[int]:
    ids, i = set(), 0
    while i < len(rdesc):
        prefix = rdesc[i]
        size = (0, 1, 2, 4)[prefix & 3]
        if prefix & 0xFC == 0x84:
            ids.add(rdesc[i + 1])
        i += 1 + size
    return ids


class InputTranslation(unittest.TestCase):
    def test_bt_input_becomes_usb_report(self):
        common = bytes(range(reports.COMMON_INPUT_SIZE))
        usb = reports.bt_input_to_usb(make_bt_input(common))
        self.assertEqual(len(usb), reports.USB_INPUT_SIZE)
        self.assertEqual(usb[0], reports.USB_INPUT_ID)
        self.assertEqual(usb[1:], common)

    def test_corrupt_crc_is_dropped(self):
        report = bytearray(make_bt_input(bytes(reports.COMMON_INPUT_SIZE)))
        report[10] ^= 0xFF
        self.assertIsNone(reports.bt_input_to_usb(bytes(report)))

    def test_other_reports_are_ignored(self):
        self.assertIsNone(reports.bt_input_to_usb(bytes([0x01]) + bytes(9)))  # BT "simple" mode report
        self.assertIsNone(reports.bt_input_to_usb(bytes(reports.BT_INPUT_SIZE)))


class OutputTranslation(unittest.TestCase):
    def test_usb_output_becomes_bt_report(self):
        common = bytes(range(1, reports.COMMON_OUTPUT_SIZE + 1))
        bt = reports.OutputTranslator().usb_to_bt(bytes([reports.USB_OUTPUT_ID]) + common + bytes(15))
        self.assertEqual(len(bt), reports.BT_OUTPUT_SIZE)
        self.assertEqual(bt[0], reports.BT_OUTPUT_ID)
        self.assertEqual(bt[2], reports.BT_OUTPUT_TAG)
        self.assertEqual(bt[3 : 3 + reports.COMMON_OUTPUT_SIZE], common)
        self.assertEqual(bt[3 + reports.COMMON_OUTPUT_SIZE : -4], bytes(24))
        self.assertTrue(reports.bt_crc_ok(reports.OUTPUT_CRC_SEED, bt))

    def test_sequence_number_wraps_after_16(self):
        tr = reports.OutputTranslator()
        seqs = [tr.usb_to_bt(bytes([reports.USB_OUTPUT_ID]))[1] >> 4 for _ in range(18)]
        self.assertEqual(seqs, list(range(16)) + [0, 1])

    def test_short_output_is_padded(self):
        bt = reports.OutputTranslator().usb_to_bt(bytes([reports.USB_OUTPUT_ID, 0xFF, 0x01]))
        self.assertEqual(bt[3:5], b"\xff\x01")
        self.assertEqual(bt[5 : 3 + reports.COMMON_OUTPUT_SIZE], bytes(reports.COMMON_OUTPUT_SIZE - 2))

    def test_non_output_reports_are_rejected(self):
        self.assertIsNone(reports.OutputTranslator().usb_to_bt(bytes([0x05, 0x00])))


class Features(unittest.TestCase):
    def test_crc_tail_is_zeroed(self):
        bt = bytes([0x05]) + bytes(range(36)) + b"\xde\xad\xbe\xef"
        self.assertEqual(reports.bt_feature_to_usb(bt), bt[:-4] + bytes(4))

    def test_set_feature_gets_bt_crc(self):
        usb = bytes([0x08]) + bytes(range(43)) + bytes(4)
        bt = reports.usb_feature_to_bt(usb)
        self.assertEqual(bt[:-4], usb[:-4])
        self.assertTrue(reports.bt_crc_ok(reports.SET_FEATURE_CRC_SEED, bt))

    def test_mac_spoof_flips_locally_administered_bit_only(self):
        pairing = bytes.fromhex("09bc9a7856341208250066554433221100000000")
        spoofed = reports.spoof_pairing_mac(pairing)
        self.assertEqual(spoofed[6], 0x10)
        self.assertEqual(spoofed[:6] + spoofed[7:], pairing[:6] + pairing[7:])
        self.assertEqual(reports.spoof_pairing_mac(b"\x05" + bytes(40)), b"\x05" + bytes(40))


class Layouts(unittest.TestCase):
    def test_uhid_event_size_matches_kernel(self):
        self.assertEqual(uhid.EVENT_SIZE, 4376)

    def test_descriptor_declares_usb_input_and_output_reports(self):
        ids = report_ids(descriptor.USB_REPORT_DESCRIPTOR)
        self.assertIn(reports.USB_INPUT_ID, ids)
        self.assertIn(reports.USB_OUTPUT_ID, ids)
        self.assertIn(reports.PAIRING_INFO_ID, ids)
        self.assertNotIn(reports.BT_INPUT_ID, ids)


CAPTURES = ROOT / "captures"


@unittest.skipUnless(glob.glob(str(CAPTURES / "bluetooth-*.json")) and glob.glob(str(CAPTURES / "usb-*.json")),
                     "no local hardware captures")
class RealHardware(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bt = json.loads(pathlib.Path(sorted(glob.glob(str(CAPTURES / "bluetooth-*.json")))[-1]).read_text())
        cls.usb = json.loads(pathlib.Path(sorted(glob.glob(str(CAPTURES / "usb-*.json")))[-1]).read_text())

    def test_every_captured_bt_input_translates(self):
        for h in self.bt["inputs"]:
            usb = reports.bt_input_to_usb(bytes.fromhex(h))
            self.assertIsNotNone(usb)
            self.assertEqual(len(usb), len(bytes.fromhex(self.usb["inputs"][0])))

    def test_bt_features_match_usb_after_translation(self):
        for rid in ("0x05", "0x09", "0x20"):
            bt = reports.bt_feature_to_usb(bytes.fromhex(self.bt["features"][rid]))
            self.assertEqual(bt, bytes.fromhex(self.usb["features"][rid]), rid)

    def test_descriptor_module_matches_capture(self):
        self.assertEqual(descriptor.USB_REPORT_DESCRIPTOR, bytes.fromhex(self.usb["report_descriptor"]))


if __name__ == "__main__":
    unittest.main()


class OutputRateLimit(unittest.TestCase):
    """Bridge must not flood Bluetooth: identical reports dropped, newest state wins, <= 100 Hz."""

    def make_bridge(self):
        import os
        from ds5bridge import bridge
        b = bridge.Bridge.__new__(bridge.Bridge)
        rfd, b.phys = os.pipe()
        self.addCleanup(os.close, rfd)
        self.addCleanup(os.close, b.phys)
        b.output = reports.OutputTranslator()
        b.stats = dict.fromkeys(("out", "out_dup", "out_merged"), 0)
        b.pending_output = b.last_output = None
        b.last_output_time = 0.0
        return b, rfd

    def test_duplicates_and_bursts_are_coalesced(self):
        import os
        import time
        from ds5bridge import bridge
        b, rfd = self.make_bridge()
        state = lambda n: bytes([reports.USB_OUTPUT_ID, n]) + bytes(46)
        for n in (1, 1, 2, 3):  # same frame twice, then a burst inside one interval
            b.forward_output(state(n))
        time.sleep(bridge.OUTPUT_INTERVAL * 1.5)
        b.flush_output()
        b.forward_output(state(3))  # unchanged state is never resent
        sent = os.read(rfd, 4096)
        self.assertEqual(len(sent), 2 * reports.BT_OUTPUT_SIZE)
        self.assertEqual([sent[3], sent[reports.BT_OUTPUT_SIZE + 3]], [1, 3])
        self.assertEqual(b.stats["out"], 2)
