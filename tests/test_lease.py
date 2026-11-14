import importlib
import unittest

from siteleasectl import (
    checkpoint_of,
    lease,
    register_checkpoint,
    release,
    usage,
)

module = importlib.import_module("siteleasectl.lease")


class LeaseTest(unittest.TestCase):
    def setUp(self):
        module._used = 0

    def test_lease(self):
        self.assertEqual(lease("shop")["site"], "shop")

    def test_global_limit(self):
        lease("shop")
        lease("shop")
        lease("news")
        with self.assertRaises(RuntimeError):
            lease("blog")

    def test_no_expiry(self):
        lease("shop")
        self.assertEqual(usage()["used"], 1)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class SiteLeaseTest(unittest.TestCase):
    def setUp(self):
        module._used = 0  # 触发完整状态重置
        self.clock = FakeClock()
        self._orig_now = module._now
        self._orig_ttl = module.LEASE_TTL
        module._now = self.clock

    def tearDown(self):
        module._now = self._orig_now
        module.LEASE_TTL = self._orig_ttl
        module._used = 0

    def test_site_isolation(self):
        lease("shop")
        lease("shop")
        with self.assertRaises(RuntimeError):
            lease("shop")  # 每站上限 SITE_LIMIT=2
        other = lease("news")  # 站点隔离：别的站不受影响
        self.assertEqual(other["site"], "news")
        self.assertEqual(usage()["sites"]["shop"]["used"], 2)

    def test_priority_preemption(self):
        low = lease("shop", priority=1)
        lease("shop", priority=2)
        high = lease("shop", priority=9)
        records = usage()["preemptions"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["lease_id"], low["id"])  # 优先级最低的被抢
        self.assertEqual(usage()["sites"]["shop"]["used"], 2)
        self.assertFalse(release(low["id"]))  # 被抢占的租约已失效
        self.assertTrue(release(high["id"]))

    def test_preemption_checkpoint(self):
        low = lease("shop", priority=1)
        lease("shop", priority=1)
        register_checkpoint(low["id"], lambda rec: {"progress": 7})
        lease("shop", priority=9)
        self.assertEqual(checkpoint_of(low["id"]), {"progress": 7})
        self.assertEqual(usage()["preemptions"][0]["checkpoint"],
                         {"progress": 7})

    def test_timeout_reclaim(self):
        lease("shop")
        lease("shop")
        self.clock.t += module.LEASE_TTL + 1
        self.assertEqual(usage()["used"], 0)  # 过期自动回收
        lease("shop")
        self.assertEqual(usage()["used"], 1)

    def test_idempotent_release(self):
        rec = lease("shop")
        self.assertTrue(release(rec["id"]))
        self.assertFalse(release(rec["id"]))  # 幂等：重复释放不重复扣减
        self.assertEqual(usage()["used"], 0)
        with self.assertRaises(ValueError):
            release("lease-999")

    def test_waiting_queue_and_promotion(self):
        first = lease("shop")
        lease("shop")
        with self.assertRaises(RuntimeError):
            lease("shop")
        self.assertEqual(len(usage()["waiting"]), 1)
        release(first["id"])
        self.assertEqual(usage()["waiting"], [])  # 等待请求被补位
        self.assertEqual(len(usage()["promoted"]), 1)
        self.assertEqual(usage()["sites"]["shop"]["used"], 2)

    def test_single_owner_per_lease(self):
        first = lease("shop")
        second = lease("shop")
        self.assertNotEqual(first["owner"], second["owner"])
        release(first["id"])
        third = lease("shop")
        self.assertNotEqual(third["id"], first["id"])  # 租约 id 不复用
        self.assertNotEqual(third["owner"], first["owner"])

    def test_invalid_args(self):
        with self.assertRaises(ValueError):
            lease("")
        with self.assertRaises(ValueError):
            lease("shop", priority="high")
        with self.assertRaises(ValueError):
            register_checkpoint("lease-999", lambda rec: None)
