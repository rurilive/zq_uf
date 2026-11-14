import threading
import time
import unittest

from partitionlease import (
    LeaseExpiredError,
    NotLeaseHolderError,
    StaleFenceError,
    acquire,
    events,
    holder,
    release,
    renew,
    write,
)
from partitionlease import lease


class FencingTest(unittest.TestCase):
    def setUp(self):
        lease._leases.clear()

    def test_stale_fence_rejected(self):
        first = acquire("d", "p-stale", "a", 10)
        second = acquire("d", "p-stale", "b", 10)
        self.assertLess(first["fence_token"], second["fence_token"])
        with self.assertRaises(StaleFenceError):
            write("d", "p-stale", "a", first["fence_token"], "old")
        ok = write("d", "p-stale", "b", second["fence_token"], "new")
        self.assertEqual(ok["owner"], "b")

    def test_preempt_recorded_and_old_holder_failed(self):
        acquire("d", "p-preempt", "a", 10)
        acquire("d", "p-preempt", "b", 10)
        self.assertEqual(holder("d", "p-preempt")["owner"], "b")
        self.assertFalse(renew("d", "p-preempt", "a", 10))
        kinds = [e["event"] for e in events("d", "p-preempt")]
        self.assertIn("preempt", kinds)

    def test_expired_lease_reclaimed(self):
        acquire("d", "p-reclaim", "a", 0.05)
        time.sleep(0.08)
        acquire("d", "p-reclaim", "b", 10)
        kinds = [e["event"] for e in events("d", "p-reclaim")]
        self.assertIn("reclaim", kinds)
        self.assertNotIn("preempt", kinds)
        self.assertEqual(holder("d", "p-reclaim")["owner"], "b")

    def test_expired_write_and_renew_rejected(self):
        old = acquire("d", "p-expire", "a", 0.05)
        time.sleep(0.08)
        acquire("d", "p-expire", "b", 10)
        with self.assertRaises(StaleFenceError):
            write("d", "p-expire", "a", old["fence_token"], "x")
        self.assertFalse(renew("d", "p-expire", "a", 10))

    def test_write_requires_live_lease(self):
        old = acquire("d", "p-dead", "a", 0.05)
        time.sleep(0.08)
        with self.assertRaises(LeaseExpiredError):
            write("d", "p-dead", "a", old["fence_token"], "x")
        with self.assertRaises(NotLeaseHolderError):
            write("d", "p-none", "a", 1, "x")

    def test_concurrent_acquire_single_holder(self):
        results = []
        lock = threading.Lock()

        def worker(name):
            lease_info = acquire("d", "p-race", name, 10)
            with lock:
                results.append(lease_info)

        threads = [threading.Thread(target=worker, args=(f"job-{i}",)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        fences = [r["fence_token"] for r in results]
        self.assertEqual(len(set(fences)), 8)
        current = holder("d", "p-race")
        self.assertIsNotNone(current)
        self.assertEqual(current["fence_token"], max(fences))
        for r in results:
            if r["fence_token"] != current["fence_token"]:
                with self.assertRaises(StaleFenceError):
                    write("d", "p-race", r["owner"], r["fence_token"], "x")

    def test_renew_atomic_no_double_primary(self):
        acquire("d", "p-renew", "a", 5)
        outcomes = {"a": [], "b": []}

        def renewer(name):
            for _ in range(30):
                outcomes[name].append(renew("d", "p-renew", name, 5))

        threads = [threading.Thread(target=renewer, args=(n,)) for n in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(outcomes["a"]))
        self.assertFalse(any(outcomes["b"]))
        self.assertEqual(holder("d", "p-renew")["owner"], "a")

    def test_renew_extends_expiry(self):
        acquire("d", "p-extend", "a", 0.1)
        for _ in range(3):
            time.sleep(0.06)
            self.assertTrue(renew("d", "p-extend", "a", 0.1))
        self.assertEqual(holder("d", "p-extend")["owner"], "a")

    def test_release_only_by_holder(self):
        acquire("d", "p-release", "a", 10)
        self.assertFalse(release("d", "p-release", "b"))
        self.assertEqual(holder("d", "p-release")["owner"], "a")
        self.assertTrue(release("d", "p-release", "a"))
        self.assertIsNone(holder("d", "p-release"))

    def test_fence_monotonic_across_release(self):
        first = acquire("d", "p-mono", "a", 10)["fence_token"]
        release("d", "p-mono", "a")
        second = acquire("d", "p-mono", "a", 10)["fence_token"]
        self.assertGreater(second, first)


if __name__ == "__main__":
    unittest.main()
