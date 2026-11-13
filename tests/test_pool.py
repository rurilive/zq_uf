import random
import unittest

from proxysticky import PROXIES, acquire


class PoolTest(unittest.TestCase):
    def test_returns_configured_proxy(self):
        self.assertIn(acquire("shop", "s1"), PROXIES)

    def test_same_session_can_switch(self):
        random.seed(3)
        picked = {acquire("shop", "s1") for _ in range(20)}
        self.assertGreater(len(picked), 1)

    def test_sites_share_pool(self):
        random.seed(5)
        left = {acquire("shop", "s1") for _ in range(20)}
        right = {acquire("news", "s2") for _ in range(20)}
        self.assertTrue(left & right)
