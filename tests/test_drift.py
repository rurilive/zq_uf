import unittest

from confdrift import diff, snapshot


class DriftTest(unittest.TestCase):
    def test_value_change(self):
        base = snapshot("prod", {"workers": 4})
        target = snapshot("prod", {"workers": 8})
        self.assertEqual(diff(base, target)["changed"][0]["key"], "workers")

    def test_added_key_ignored(self):
        base = snapshot("prod", {"workers": 4})
        target = snapshot("prod", {"workers": 4, "token": "abc"})
        self.assertEqual(diff(base, target)["changed"], [])

    def test_sensitive_plain(self):
        base = snapshot("prod", {"token": "abc"})
        target = snapshot("prod", {"token": "xyz"})
        self.assertEqual(diff(base, target)["changed"][0]["from"], "abc")
