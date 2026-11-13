import unittest

from signparam import sign


class SignTest(unittest.TestCase):
    def test_stable_for_same_params(self):
        params = {"b": "2", "a": "1"}
        self.assertEqual(sign(params)["sign"], sign({"a": "1", "b": "2"})["sign"])

    def test_raw_uses_sorted_keys(self):
        self.assertEqual(sign({"b": "2", "a": "1"})["raw"], "a=1&b=2")

    def test_missing_key_changes_sign(self):
        self.assertNotEqual(sign({"a": "1"})["sign"], sign({"a": "1", "b": "2"})["sign"])
