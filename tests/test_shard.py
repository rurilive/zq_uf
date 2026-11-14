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


class LeaseTest(unittest.TestCase):
    def setUp(self):
        shard._owners.clear()
        shard._down.clear()
        shard._isolated.clear()

    def tearDown(self):
        shard._down.clear()
        shard._isolated.clear()

    def test_campaign_after_primary_loss_bumps_term(self):
        elect("c1", "n1")
        self.assertEqual(elect("c1", "n2")["role"], "standby")
        shard._down.add("n1")
        won = elect("c1", "n2")
        self.assertEqual(won["role"], "primary")
        self.assertEqual(won["term"], 1)

    def test_campaign_after_lease_expires(self):
        elect("c2", "n1")
        shard._owners["c2"]["lease_until"] = 0.0
        won = elect("c2", "n2")
        self.assertEqual(won["role"], "primary")
        self.assertEqual(won["term"], 1)

    def test_stale_term_write_rejected(self):
        elect("t1", "n1")
        shard._down.add("n1")
        elect("t1", "n2")
        shard._down.discard("n1")
        denied = write("t1", "n1", {"id": 9})
        self.assertFalse(denied["written"])
        self.assertEqual(denied["reason"], "stale_term")
        self.assertEqual(denied["rejected"], 1)

    def test_minority_rejects_during_split_brain(self):
        elect("sb", "n1")
        elect("sb", "n2")
        elect("sb", "n3")
        shard._isolated.add("n1")
        denied = write("sb", "n1", {"id": 1})
        self.assertFalse(denied["written"])
        self.assertEqual(denied["reason"], "minority")
        self.assertEqual(denied["rejected"], 1)
        shard._owners["sb"]["lease_until"] = 0.0
        self.assertEqual(elect("sb", "n2")["term"], 1)
        self.assertTrue(write("sb", "n2", {"id": 2})["written"])
        shard._isolated.clear()
        self.assertFalse(write("sb", "n1", {"id": 3})["written"])

    def test_resume_from_confirmed_position(self):
        elect("r1", "n1")
        for i in range(3):
            write("r1", "n1", {"id": i})
        shard._down.add("n1")
        elect("r1", "n2")
        continued = write("r1", "n2", {"id": 3})
        self.assertTrue(continued["written"])
        self.assertEqual(continued["position"], 4)
