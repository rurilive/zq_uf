import unittest

from siteallow import allowed, block
from siteallow import allow


class AllowTest(unittest.TestCase):
    def setUp(self):
        allow._blocked.clear()

    def test_block(self):
        block("shop", "403", 60)
        self.assertFalse(allowed("shop"))

    def test_reason_saved(self):
        self.assertEqual(block("shop", "403", 60)["reason"], "403")

    def test_other_allowed(self):
        block("shop", "403", 60)
        self.assertTrue(allowed("news"))
