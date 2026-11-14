import unittest

from tablelock import acquire, release
from tablelock import lock


class LockTest(unittest.TestCase):
    def setUp(self):
        lock._locks.clear()

    def test_acquire_ok(self):
        self.assertTrue(acquire("t", "a", 10)["ok"])

    def test_second_blocked(self):
        acquire("t", "a", 10)
        self.assertFalse(acquire("t", "b", 10)["ok"])

    def test_release_only_owner(self):
        acquire("t", "a", 10)
        self.assertFalse(release("t", "b"))
