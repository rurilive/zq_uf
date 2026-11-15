import unittest

from tablecopy2 import copy, verify


class CopyTest(unittest.TestCase):
    def test_copy_rows(self):
        self.assertEqual(copy({"name": "a", "rows": [1, 2]}, "b")["rows"], 2)

    def test_no_snapshot(self):
        self.assertIsNone(copy({"name": "a", "rows": []}, "c")["snapshot"])

    def test_verify_matches(self):
        self.assertTrue(verify()["matched"])
