import unittest

from partitionlease import acquire, release, renew
from partitionlease import lease


class PartitionLeaseTest(unittest.TestCase):
    def setUp(self):
        lease._leases.clear()

    def test_acquire_owner(self):
        self.assertEqual(acquire("d", "p", "a", 10)["owner"], "a")

    def test_second_owner_overwrites(self):
        acquire("d", "p", "a", 10)
        acquire("d", "p", "b", 10)
        self.assertFalse(renew("d", "p", "a", 10))

    def test_no_fence(self):
        self.assertNotIn("fence", acquire("d", "p", "a", 10))
