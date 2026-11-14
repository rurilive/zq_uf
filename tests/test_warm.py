import unittest

from crawlwarm import current, warmup


class WarmTest(unittest.TestCase):
    def test_warmup_returns_concurrency(self):
        self.assertEqual(warmup("shop")["concurrency"], 8)

    def test_current_default(self):
        self.assertEqual(current("news"), 8)

    def test_current_after_warmup(self):
        warmup("shop")
        self.assertEqual(current("shop"), 8)
