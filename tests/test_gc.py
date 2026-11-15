import unittest

from partgc2 import gc, pin


class GcTest(unittest.TestCase):
    def test_gc_deletes_unlisted(self):
        result = gc({"existing": ["p1", "p2"], "keep": ["p2"]})
        self.assertEqual(result["deleted"], ["p1"])

    def test_gc_has_no_skip(self):
        self.assertEqual(gc({"existing": ["p1"], "keep": []})["skipped"], [])

    def test_pin_has_no_expiry(self):
        self.assertIsNone(pin("p1")["until"])
