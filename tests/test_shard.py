import unittest

from crawlshard import configure, elect, heartbeat, status, write
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


class LeasedShardTest(unittest.TestCase):
    def setUp(self):
        shard._owners.clear()
        shard._clock_offset = 0.0
        configure("s1", ["n1", "n2", "n3"], lease_ttl=10.0)

    def test_campaign_uses_monotonic_term_and_standby_is_readonly(self):
        first = elect("s1", "n1")
        self.assertTrue(first["elected"])
        self.assertEqual(first["term"], 1)
        self.assertEqual(first["role"], "leader")

        # 租约有效，备机抢占被拒，任期不变。
        grab = elect("s1", "n2")
        self.assertFalse(grab["elected"])
        self.assertEqual(grab["term"], 1)

        # 备机只读。
        standby_write = write("s1", "n2", {"id": 1}, term=1)
        self.assertFalse(standby_write["written"])
        self.assertEqual(standby_write["reason"], "not_leader")

        # 主机失联、租约到期后备机竞选，任期单调递增。
        shard.advance(11)
        second = elect("s1", "n2")
        self.assertTrue(second["elected"])
        self.assertEqual(second["term"], 2)
        self.assertEqual(second["node"], "n2")
        self.assertEqual(status("s1", "n2")["role"], "leader")
        self.assertEqual(status("s1", "n1")["role"], "standby")

    def test_old_term_write_is_rejected(self):
        elect("s1", "n1")
        ok = write("s1", "n1", {"id": 1}, term=1)
        self.assertTrue(ok["written"])

        shard.advance(11)
        elect("s1", "n2")

        # 旧主拿着旧任期写，直接拒绝并计数。
        stale = write("s1", "n1", {"id": 2}, term=1)
        self.assertFalse(stale["written"])
        self.assertEqual(stale["reason"], "stale_term")
        self.assertEqual(stale["term"], 2)
        self.assertEqual(status("s1")["rejected_writes"], 1)

    def test_split_brain_minority_refuses_and_records(self):
        elect("s1", "n1")
        write("s1", "n1", {"id": 1}, term=1)

        # n1 被隔离，只能看到自己（1/3，少数派）。
        heartbeat("s1", "n1", seen=["n1"])
        minority_write = write("s1", "n1", {"id": 2}, term=1)
        self.assertFalse(minority_write["written"])
        self.assertEqual(minority_write["reason"], "minority_partition")

        # 少数派不能选主。
        shard.advance(11)
        minority_elect = elect("s1", "n1")
        self.assertFalse(minority_elect["elected"])
        self.assertEqual(minority_elect["reason"], "minority_partition")

        # 多数派重新选出新主并可继续写。
        majority_elect = elect("s1", "n2")
        self.assertTrue(majority_elect["elected"])
        self.assertEqual(majority_elect["term"], 2)
        majority_write = write("s1", "n2", {"id": 3}, term=2)
        self.assertTrue(majority_write["written"])
        self.assertEqual(status("s1")["rejected_writes"], 1)

    def test_failover_continues_from_last_confirmed_position(self):
        elect("s1", "n1")
        write("s1", "n1", {"id": 1, "position": 101}, term=1)
        write("s1", "n1", {"id": 2, "position": 102}, term=1)
        self.assertEqual(status("s1")["position"], 102)

        shard.advance(11)
        elect("s1", "n2")

        # 新主从上一任最后确认位点继续，回退位点被拒。
        self.assertEqual(status("s1")["position"], 102)
        duplicate = write("s1", "n2", {"id": 2, "position": 102}, term=2)
        self.assertFalse(duplicate["written"])
        self.assertEqual(duplicate["reason"], "stale_position")

        resumed = write("s1", "n2", {"id": 3, "position": 103}, term=2)
        self.assertTrue(resumed["written"])
        self.assertEqual(resumed["position"], 103)
        self.assertEqual(status("s1")["position"], 103)
