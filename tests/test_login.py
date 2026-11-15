import unittest

from loginkeep import lease, refresh


class LoginTest(unittest.TestCase):
    def test_each_lease_is_new(self):
        self.assertNotEqual(lease("a")["token"], lease("a")["token"])

    def test_lease_is_not_shared(self):
        self.assertFalse(lease("b")["shared"])

    def test_refresh_is_noop(self):
        self.assertFalse(refresh("t-1")["refreshed"])
