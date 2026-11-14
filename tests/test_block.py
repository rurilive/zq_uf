import unittest

from blockvote import allow, observe
from blockvote import block


class BlockTest(unittest.TestCase):
    def setUp(self):
        block._blocked.clear()

    def test_403_blocks(self):
        self.assertTrue(observe("shop", "403"))

    def test_captcha_ignored(self):
        self.assertFalse(observe("shop", "captcha"))

    def test_no_recovery(self):
        observe("shop", "403")
        observe("shop", "200")
        self.assertFalse(allow("shop"))
