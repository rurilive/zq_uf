import unittest

from backfill2 import apply, plan


class BackfillTest(unittest.TestCase):
    def test_single_slice(self):
        self.assertEqual(plan({"start": 1, "end": 9})["slices"], 1)

    def test_no_retraction(self):
        self.assertEqual(apply({})["retracted"], 0)

    def test_no_diff(self):
        self.assertIsNone(apply({})["diff"])
