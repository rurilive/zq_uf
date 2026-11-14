import unittest

from dedupwindow import advance, reset, seen
from dedupwindow import window


class WindowTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_dedup_within_run(self):
        self.assertFalse(seen("k", 1))
        self.assertTrue(seen("k", 2))

    def test_no_expiry(self):
        seen("k", 1)
        advance(1000)
        self.assertTrue(seen("k", 2))

    def test_state_grows(self):
        for i in range(50):
            seen(f"k{i}", i)
        self.assertEqual(len(window._seen), 50)
