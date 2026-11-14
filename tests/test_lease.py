import unittest

from crawllease import claim, release, renew
from crawllease import lease


class LeaseTest(unittest.TestCase):
    def setUp(self):
        lease._leases.clear()

    def test_claim_returns_owner(self):
        self.assertEqual(claim("t1", "a", 10)["owner"], "a")

    def test_second_claim_overwrites(self):
        claim("t1", "a", 10)
        claim("t1", "b", 10)
        self.assertFalse(renew("t1", "a", 10))

    def test_no_expiry(self):
        claim("t1", "a", 0)
        self.assertTrue(renew("t1", "a", 10))

    def test_release_only_owner(self):
        claim("t1", "a", 10)
        release("t1", "b")
        self.assertTrue(renew("t1", "a", 10))
