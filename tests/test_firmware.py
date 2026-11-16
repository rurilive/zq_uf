import unittest

from firmware import inspect, report, unpack
from firmware import firmware

IMAGE = {
    "name": "fw-a100",
    "format": "squashfs",
    "children": [
        {
            "name": "bin",
            "type": "dir",
            "children": [{"name": "busybox", "type": "file", "size": 900}],
        },
        {"name": "etc", "type": "dir", "children": [{"name": "passwd", "type": "file", "size": 40}]},
    ],
}


class FirmwareTest(unittest.TestCase):
    def setUp(self):
        firmware._IMAGES.clear()

    def test_files_are_flat_paths(self):
        self.assertEqual(unpack(IMAGE)["files"], ["/bin/busybox", "/etc/passwd"])

    def test_layers_is_one(self):
        self.assertEqual(unpack(IMAGE)["layers"], 1)

    def test_rootfs_is_unknown(self):
        self.assertIsNone(unpack(IMAGE)["rootfs"])

    def test_inspect_finds_file(self):
        unpack(IMAGE)
        self.assertEqual(inspect("fw-a100", "/etc/passwd")["layer"], 1)

    def test_report_counts_files(self):
        unpack(IMAGE)
        self.assertEqual(report()["files"], 2)
