import unittest

from crawllease import claim, release, renew
from crawllease import lease
from crawllease import reclaims, status
import os
import tempfile
import threading
import time


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


class FencedLeaseTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="crawllease-test-")
        os.close(fd)
        os.unlink(self.path)
        self._old_state_file = lease._state_file
        lease._state_file = self.path
        lease._leases.clear()

    def tearDown(self):
        lease._state_file = self._old_state_file
        for suffix in ("", ".lock", ".tmp"):
            try:
                os.unlink(self.path + suffix)
            except OSError:
                pass

    def test_concurrent_claim_single_holder(self):
        n = 8
        barrier = threading.Barrier(n)
        results = []

        def worker(i):
            barrier.wait()
            results.append(claim("tc", f"w{i}", 30))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        fences = sorted(r["fence"] for r in results)
        self.assertEqual(fences, list(range(1, n + 1)))
        holder = status("tc")
        self.assertEqual(holder["fence"], n)
        for r in results:
            self.assertEqual(renew("tc", r["owner"], 30), r["owner"] == holder["owner"])

    def test_expired_lease_reclaimed(self):
        first = claim("te", "a", 0.2)
        time.sleep(0.3)
        second = claim("te", "b", 30)
        self.assertEqual(second["fence"], first["fence"] + 1)
        self.assertFalse(renew("te", "a", 30))
        records = [r for r in reclaims() if r["task_id"] == "te"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["reason"], "expired")
        self.assertEqual(records[0]["previous_owner"], "a")
        self.assertEqual(records[0]["new_owner"], "b")

    def test_stale_owner_rejected(self):
        claim("ts", "a", 30)
        claim("ts", "b", 30)
        self.assertFalse(renew("ts", "a", 30))
        self.assertFalse(release("ts", "a"))
        current = status("ts")
        self.assertEqual(current["owner"], "b")
        self.assertEqual(current["fence"], 2)
        self.assertTrue(renew("ts", "b", 30))

    def test_renew_atomic_under_contention(self):
        claim("tr", "owner", 30)
        claims = 4
        barrier = threading.Barrier(claims + 1)
        renew_results = []
        claim_results = []

        def renewer():
            barrier.wait()
            for _ in range(20):
                renew_results.append(renew("tr", "owner", 30))

        def claimer(i):
            barrier.wait()
            claim_results.append(claim("tr", f"c{i}", 30))

        threads = [threading.Thread(target=renewer)]
        threads += [threading.Thread(target=claimer, args=(i,)) for i in range(claims)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(isinstance(r, bool) for r in renew_results))
        holder = status("tr")
        self.assertEqual(holder["fence"], 1 + claims)
        self.assertIn(holder["owner"], {f"c{i}" for i in range(claims)})
        self.assertFalse(renew("tr", "owner", 30))

    def test_invalid_args_raise(self):
        with self.assertRaises(ValueError):
            claim("", "a", 10)
        with self.assertRaises(ValueError):
            claim("ti", "", 10)
        with self.assertRaises(ValueError):
            claim("ti", "a", -1)
        with self.assertRaises(ValueError):
            renew("ti", "a", -1)
