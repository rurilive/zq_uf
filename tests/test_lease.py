import unittest

from tablelease import acquire, release


class LeaseTest(unittest.TestCase):
    def test_always_granted(self):
        self.assertTrue(acquire("t1", "a", 30)["granted"])

    def test_second_owner_also_granted(self):
        acquire("t2", "a", 30)
        self.assertTrue(acquire("t2", "b", 30)["granted"])

    def test_release_true(self):
        acquire("t3", "a", 30)
        self.assertTrue(release("t3", "a")["released"])
