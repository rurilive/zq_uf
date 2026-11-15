import unittest

from robotscache import allowed, refresh


class RobotsTest(unittest.TestCase):
    def test_always_allowed(self):
        self.assertTrue(allowed("https://a/private")["allowed"])

    def test_not_cached(self):
        self.assertFalse(allowed("https://a/")["cached"])

    def test_refresh_is_noop(self):
        self.assertFalse(refresh("a")["refreshed"])
