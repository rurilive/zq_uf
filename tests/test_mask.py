import unittest

from maskctl import mask, reveal


class MaskTest(unittest.TestCase):
    def test_plaintext_passthrough(self):
        self.assertEqual(mask({"phone": "123"})["row"], {"phone": "123"})

    def test_no_masked_fields(self):
        self.assertEqual(mask({"phone": "123"})["masked"], [])

    def test_reveal_is_false(self):
        self.assertFalse(reveal({"phone": "***"}, "t")["revealed"])
