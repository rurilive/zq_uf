import unittest

from crawllease import acquire, renew


class LeaseTest(unittest.TestCase):
    def test_acquire_always_granted(self):
        self.assertTrue(acquire("t1", "a")["granted"])

    def test_second_owner_also_granted(self):
        acquire("t2", "a")
        self.assertTrue(acquire("t2", "b")["granted"])

    def test_renew_always_true(self):
        self.assertTrue(renew("t1", "a")["renewed"])
