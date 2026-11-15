import unittest

from smallfile import compact, plan


class SmallTest(unittest.TestCase):
    def test_plan_selects_all(self):
        self.assertEqual(plan({"partitions": ["p1", "p2"]})["selected"], ["p1", "p2"])

    def test_compact_all(self):
        self.assertEqual(compact({"partitions": ["p1"]})["compacted"], ["p1"])

    def test_always_switched(self):
        self.assertTrue(compact({"partitions": []})["switched"])
