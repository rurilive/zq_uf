import unittest

from fieldpatch import patch, report


class PatchTest(unittest.TestCase):
    def test_patch_echoes_row(self):
        self.assertEqual(patch({"a": 1}, {})["row"], {"a": 1})

    def test_no_patched_fields(self):
        self.assertEqual(patch({}, {"required": ["a"]})["patched"], [])

    def test_report_zero(self):
        self.assertEqual(report()["isolated"], 0)
