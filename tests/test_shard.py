import unittest

from crawlshard import elect, write
from crawlshard import shard


class ShardTest(unittest.TestCase):
    def setUp(self):
        shard._owners.clear()

    def test_elect_records_owner(self):
        self.assertEqual(elect("s1", "n1")["node"], "n1")

    def test_second_elect_overwrites(self):
        elect("s1", "n1")
        self.assertEqual(elect("s1", "n2")["term"], 0)

    def test_non_owner_can_write(self):
        elect("s1", "n1")
        self.assertTrue(write("s1", "n2", {"id": 1})["written"])


class LeaseFailoverTest(unittest.TestCase):
    def setUp(self):
        shard._owners.clear()
        shard._partitions.clear()

    def test_standby_campaign_after_lease_expiry(self):
        elect("s2", "n1")
        self.assertEqual(elect("s2", "n2")["role"], "standby")
        shard.expire_lease("s2")
        result = elect("s2", "n2")
        self.assertEqual(result["role"], "primary")
        self.assertEqual(result["term"], 1)

    def test_stale_term_write_rejected(self):
        elect("s3", "n1")
        elect("s3", "n2")
        shard.expire_lease("s3")
        elect("s3", "n2")
        result = write("s3", "n1", {"id": 9})
        self.assertFalse(result["written"])
        self.assertEqual(result["reason"], "stale_term")
        self.assertEqual(result["rejected"], 1)

    def test_standby_write_rejected(self):
        elect("s4", "n1")
        elect("s4", "n2")
        result = write("s4", "n2", {"id": 1})
        self.assertFalse(result["written"])
        self.assertEqual(result["reason"], "standby_read_only")

    def test_split_brain_minority_rejects_and_records(self):
        elect("s5", "n1")
        elect("s5", "n2")
        elect("s5", "n3")
        shard.set_partition("s5", [["n1"], ["n2", "n3"]])
        result = write("s5", "n1", {"id": 1})
        self.assertFalse(result["written"])
        self.assertEqual(result["reason"], "minority_partition")
        events = shard.status("s5")["events"]
        self.assertTrue(any(e["reason"] == "minority_partition" for e in events))
        shard.expire_lease("s5")
        self.assertEqual(elect("s5", "n1")["role"], "standby")
        result = elect("s5", "n2")
        self.assertEqual(result["role"], "primary")
        self.assertEqual(result["term"], 1)

    def test_resume_from_confirmed_offset(self):
        elect("s6", "n1")
        for i in range(3):
            write("s6", "n1", {"id": i})
        self.assertEqual(elect("s6", "n2")["offset"], 3)
        shard.expire_lease("s6")
        self.assertEqual(elect("s6", "n2")["offset"], 3)
        result = write("s6", "n2", {"id": 3})
        self.assertTrue(result["written"])
        self.assertEqual(result["offset"], 4)
        self.assertFalse(write("s6", "n1", {"id": 4})["written"])
