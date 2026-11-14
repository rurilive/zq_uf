import importlib
import time
import unittest

from siteleasectl import lease, release, usage

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


class ExpiryTest(unittest.TestCase):
    def setUp(self):
        module.reset()

    def tearDown(self):
        module.reset()

    def test_expired_lease_reclaimed(self):
        rec = lease("shop", ttl=0.05)
        time.sleep(0.08)
        self.assertEqual(module.reclaim_expired(), [rec["id"]])
        self.assertEqual(usage()["used"], 0)
        self.assertFalse(module.is_active(rec["id"]))

    def test_reclaim_idempotent(self):
        lease("shop", ttl=0.05)
        time.sleep(0.08)
        module.reclaim_expired()
        self.assertEqual(module.reclaim_expired(), [])
        self.assertEqual(usage()["used"], 0)

    def test_lazy_sweep_on_lease(self):
        rec = lease("shop", ttl=0.05)
        time.sleep(0.08)
        lease("news")
        self.assertFalse(module.is_active(rec["id"]))


class PreemptionTest(unittest.TestCase):
    def setUp(self):
        module.reset()

    def tearDown(self):
        module.reset()

    def test_high_priority_preempts_low(self):
        low = lease("shop", priority=1)
        lease("news", priority=2)
        lease("blog", priority=2)
        urgent = lease("shop", priority=9)
        self.assertTrue(module.is_active(urgent["id"]))
        self.assertFalse(module.is_active(low["id"]))
        records = module.preemptions()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["lease_id"], low["id"])
        self.assertEqual(records[0]["by_id"], urgent["id"])

    def test_equal_priority_cannot_preempt(self):
        lease("shop", priority=5)
        lease("news", priority=5)
        lease("blog", priority=5)
        with self.assertRaises(module.LeaseLimitError):
            lease("shop", priority=5)

    def test_site_limit_preempts_within_site(self):
        module.set_site_limit("shop", 1)
        low = lease("shop", priority=1)
        high = lease("shop", priority=8)
        self.assertFalse(module.is_active(low["id"]))
        self.assertTrue(module.is_active(high["id"]))

    def test_preemption_saves_checkpoint(self):
        low = lease("shop", priority=1)
        lease("news", priority=2)
        lease("blog", priority=2)
        module.checkpoint(low["id"], {"page": 7})
        lease("news", priority=9)
        record = module.preemptions()[0]
        self.assertEqual(record["lease_id"], low["id"])
        self.assertEqual(record["checkpoint"], {"page": 7})
        self.assertEqual(module.checkpoints()[low["id"]], {"page": 7})

    def test_checkpoint_requires_active_lease(self):
        with self.assertRaises(module.LeaseStateError):
            module.checkpoint("lease-404", {"page": 1})


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        module.reset()

    def tearDown(self):
        module.reset()

    def test_release_idempotent(self):
        rec = lease("shop")
        self.assertTrue(release(rec["id"]))
        self.assertFalse(release(rec["id"]))
        self.assertEqual(usage()["used"], 0)

    def test_release_unknown(self):
        self.assertFalse(release("lease-404"))

    def test_lease_ids_unique_and_not_reused(self):
        first = lease("shop")
        second = lease("shop")
        self.assertNotEqual(first["id"], second["id"])
        release(first["id"])
        third = lease("shop")
        self.assertNotEqual(third["id"], first["id"])


class SiteIsolationTest(unittest.TestCase):
    def setUp(self):
        module.reset()

    def tearDown(self):
        module.reset()

    def test_site_limit_blocks_only_that_site(self):
        module.set_site_limit("shop", 1)
        first = lease("shop")
        self.assertTrue(module.is_active(first["id"]))
        with self.assertRaises(module.LeaseLimitError):
            lease("shop")
        other = lease("news")
        self.assertEqual(other["site"], "news")
        waiting = module.wait_queue()
        self.assertEqual(len(waiting), 1)
        self.assertEqual(waiting[0]["site"], "shop")

    def test_status_reports_sites_waiting_preemptions(self):
        module.set_site_limit("shop", 1)
        lease("shop")
        with self.assertRaises(module.LeaseLimitError):
            lease("shop")
        snapshot = module.status()
        self.assertEqual(snapshot["sites"]["shop"]["used"], 1)
        self.assertEqual(snapshot["sites"]["shop"]["limit"], 1)
        self.assertEqual(len(snapshot["waiting"]), 1)
        self.assertEqual(snapshot["preemptions"], [])

    def test_invalid_arguments_raise_clear_errors(self):
        with self.assertRaises(ValueError):
            lease("")
        with self.assertRaises(ValueError):
            lease("shop", ttl=0)
        with self.assertRaises(ValueError):
            module.set_site_limit("shop", 0)
