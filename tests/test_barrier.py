import unittest

from crawlbarrier import arrive, wait
from crawlbarrier import barrier


class BarrierTest(unittest.TestCase):
    def setUp(self):
        barrier._arrived.clear()

    def test_arrive_counts(self):
        arrive("b1", "s1")
        self.assertEqual(arrive("b1", "s2"), 2)

    def test_wait_returns_arrived_subsets(self):
        arrive("b1", "s1")
        self.assertEqual(wait("b1"), ["s1"])

    def test_failure_not_tracked(self):
        arrive("b1", "s1", ok=False)
        self.assertEqual(wait("b1"), ["s1"])
