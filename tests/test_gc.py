import unittest

from snapshotgc import gc, pin, reset


class GcTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_removes_nothing(self):
        self.assertEqual(gc(1)["removed"], 0)

    def test_pin_has_no_effect(self):
        self.assertFalse(pin("snap-1")["pinned"])

    def test_no_refcount(self):
        self.assertNotIn("refs", gc(1))
