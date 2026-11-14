import unittest

from hostshield import INTERVAL, acquire, release
from hostshield import shield


class ShieldTest(unittest.TestCase):
    def setUp(self):
        shield._last[0] = 0.0

    def test_fixed_interval(self):
        self.assertEqual(acquire("shop")["at"], INTERVAL)

    def test_sites_share_schedule(self):
        first = acquire("shop")["at"]
        second = acquire("news")["at"]
        self.assertEqual(second - first, INTERVAL)

    def test_release_ignores_signals(self):
        self.assertEqual(release("shop", 5.0, 429)["rate"], 1.0)
