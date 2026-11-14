import unittest

from dedupwindow import advance, configure, dumps, loads, reset, seen, stats
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


class BoundedWindowTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_window_cleanup(self):
        configure(window_size=10, allowed_lateness=10, max_size=100)
        for i in range(20):
            seen(f"k{i}", i)
        advance(15)
        self.assertEqual(len(window._seen), 15)
        advance(25)
        self.assertEqual(len(window._seen), 5)

    def test_late_still_deduped(self):
        configure(window_size=10, allowed_lateness=10, max_size=100)
        seen("k", 10)
        advance(15)
        self.assertTrue(seen("k", 8))
        self.assertEqual(stats()["late"], 1)
        self.assertEqual(stats()["hits"], 1)

    def test_beyond_lateness_expired(self):
        configure(window_size=10, allowed_lateness=5, max_size=100)
        seen("k", 1)
        advance(100)
        self.assertFalse(seen("k", 90))
        self.assertFalse(seen("new", 90))
        self.assertEqual(stats()["expired"], 2)
        self.assertNotIn("new", window._seen)

    def test_max_size_evicts_oldest(self):
        configure(window_size=1000, allowed_lateness=1000, max_size=3)
        seen("a", 1)
        seen("b", 2)
        seen("c", 3)
        seen("d", 4)
        self.assertEqual(len(window._seen), 3)
        self.assertNotIn("a", window._seen)
        self.assertEqual(stats()["dropped"], 1)

    def test_serialization_roundtrip(self):
        configure(window_size=10, allowed_lateness=5, max_size=10)
        seen("a", 1)
        seen("b", 2)
        advance(8)
        blob = dumps()
        reset()
        self.assertEqual(len(window._seen), 0)
        loads(blob)
        self.assertEqual(stats()["watermark"], 8)
        self.assertEqual(len(window._seen), 2)
        self.assertTrue(seen("a", 3))
