import unittest

from snapalign import apply, snapshot


class AlignTest(unittest.TestCase):
    def test_snapshot_has_no_position(self):
        self.assertIsNone(snapshot({"rows": [1]})["position"])

    def test_apply_no_cache(self):
        self.assertEqual(apply([1, 2])["cached"], 0)

    def test_position_none(self):
        self.assertIsNone(apply([])["position"])
