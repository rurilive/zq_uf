import unittest

from tabledesens import mask, reveal


class DesensTest(unittest.TestCase):
    def test_mask_returns_same(self):
        self.assertEqual(mask({"phone": "13800000000"})["phone"], "13800000000")

    def test_reveal_returns_same(self):
        self.assertEqual(reveal({"phone": "13800000000"}, None)["phone"], "13800000000")

    def test_no_token_needed(self):
        self.assertEqual(reveal({"a": 1}, "any"), {"a": 1})
