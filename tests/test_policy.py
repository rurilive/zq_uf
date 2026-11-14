import unittest

from fieldpolicy import grant, resolve
from fieldpolicy import policy


class PolicyTest(unittest.TestCase):
    def setUp(self):
        policy._global["masked"] = False

    def test_global_applies(self):
        policy._global["masked"] = True
        self.assertTrue(resolve("orders", "phone")["masked"])

    def test_grant_not_active(self):
        self.assertFalse(grant("orders", "phone", 100)["active"])

    def test_no_inheritance(self):
        self.assertNotIn("source", resolve("orders", "phone"))
