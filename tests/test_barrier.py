import time
import unittest

from crawlbarrier import arrive, expect, wait
from crawlbarrier import barrier


class BarrierTest(unittest.TestCase):
    def setUp(self):
        barrier._arrived.clear()

    def test_arrive_counts(self):
        arrive("b1", "s1")
        self.assertEqual(arrive("b1", "s2"), 2)

    def test_wait_returns_arrived_subsets(self):
        arrive("b1", "s1")
        self.assertEqual(wait("b1"), ["s1"])

    def test_failure_not_tracked(self):
        arrive("b1", "s1", ok=False)
        self.assertEqual(wait("b1"), ["s1"])

    def test_all_shards_release(self):
        expect("b2", "s1", "s2")
        arrive("b2", "s1")
        arrive("b2", "s2")
        result = wait("b2", timeout=1)
        self.assertEqual(result, ["s1", "s2"])
        self.assertTrue(result.ok)
        self.assertEqual(result.status, {"s1": "ok", "s2": "ok"})
        self.assertEqual(result.timed_out, [])

    def test_partial_failure_rolls_back_in_reverse_order(self):
        expect("b3", "s1", "s2")
        log = []
        arrive("b3", "s1", compensate=lambda: log.append("undo-s1") or "s1-done")
        arrive("b3", "s2", ok=False, compensate=lambda: log.append("undo-s2") or "s2-done")
        result = wait("b3", timeout=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.failed, ["s2"])
        self.assertEqual(log, ["undo-s2", "undo-s1"])
        self.assertEqual(result.compensation, {"s2": "s2-done", "s1": "s1-done"})
        self.assertEqual(result.status, {"s1": "ok", "s2": "failed"})

    def test_compensation_is_idempotent(self):
        expect("b4", "s1")
        calls = []
        arrive("b4", "s1", ok=False, compensate=lambda: calls.append(1))
        arrive("b4", "s1", ok=False, compensate=lambda: calls.append(1))
        wait("b4", timeout=1)
        wait("b4", timeout=1)
        self.assertEqual(calls, [1])

    def test_duplicate_arrive_counts_once(self):
        expect("b5", "s1", "s2")
        self.assertEqual(arrive("b5", "s1"), 1)
        self.assertEqual(arrive("b5", "s1"), 1)
        self.assertEqual(arrive("b5", "s2"), 2)
        self.assertTrue(wait("b5", timeout=1).ok)

    def test_wait_timeout_returns_missing_shards(self):
        expect("b6", "s1", "s2", "s3")
        arrive("b6", "s1")
        start = time.monotonic()
        result = wait("b6", timeout=0.2)
        self.assertGreaterEqual(time.monotonic() - start, 0.2)
        self.assertEqual(result, ["s2", "s3"])
        self.assertEqual(result.timed_out, ["s2", "s3"])
        self.assertFalse(result.ok)
        self.assertEqual(result.status, {"s1": "ok", "s2": "missing", "s3": "missing"})
