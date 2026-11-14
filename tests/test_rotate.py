import unittest

from proxyrotate import PROXIES, acquire, release


class RotateTest(unittest.TestCase):
    def test_acquire_in_pool(self):
        self.assertIn(acquire("s1"), PROXIES)

    def test_release_reports(self):
        self.assertTrue(release("s1", PROXIES[0], True)["ok"])

    def test_release_keeps_session(self):
        self.assertEqual(release("s1", PROXIES[0], False)["session"], "s1")
