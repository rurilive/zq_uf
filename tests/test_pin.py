import unittest

from sessionpin2 import bind, rebind


class PinTest(unittest.TestCase):
    def test_bind_uses_first_proxy(self):
        self.assertEqual(bind("s1", "a")["proxy"], "proxy-a")

    def test_bind_has_no_expiry(self):
        self.assertEqual(bind("s2", "a")["expires_in"], 0)

    def test_rebind_records_reason(self):
        self.assertEqual(rebind("s1", "failed")["reason"], "failed")
