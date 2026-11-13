import threading
import time
import unittest

from formfill import KeyCoordinator, ReceiptStore, submit_batch


class ConcurrencyTest(unittest.TestCase):
    def make_env(self):
        return ReceiptStore(), KeyCoordinator()

    def test_same_business_key_serialized_across_batches(self):
        store, coordinator = self.make_env()
        guard = threading.Lock()
        inflight = set()
        overlap = []

        def submit_one(row):
            with guard:
                overlap.append(row["key"] in inflight)
                inflight.add(row["key"])
            time.sleep(0.15)
            with guard:
                inflight.discard(row["key"])
            return {"status": "ok"}

        row = {"id": 7, "date": "2026-03-01", "amount": "10"}
        outcomes = []

        def run_batch():
            outcomes.append(submit_batch(
                [row], workers=2, submit_one=submit_one,
                store=store, coordinator=coordinator,
            ))

        first = threading.Thread(target=run_batch)
        second = threading.Thread(target=run_batch)
        first.start()
        time.sleep(0.03)
        second.start()
        first.join()
        second.join()

        self.assertFalse(any(overlap), "同一业务键出现并发在跑")
        self.assertGreaterEqual(coordinator.stats()["total_waits"], 1)
        total_success = sum(len(item.success) for item in outcomes)
        total_skipped = sum(len(item.skipped) for item in outcomes)
        self.assertEqual(total_success, 1)
        self.assertEqual(total_skipped, 1)

    def test_distinct_keys_run_in_parallel(self):
        store, coordinator = self.make_env()
        entered = threading.Event()
        release = threading.Event()

        def submit_one(row):
            entered.set()
            release.wait(timeout=2)
            return {"status": "ok"}

        rows = [
            {"id": 1, "date": "2026-03-01", "amount": "1"},
            {"id": 2, "date": "2026-03-01", "amount": "2"},
        ]

        def run():
            return submit_batch(
                rows, workers=2, submit_one=submit_one,
                store=store, coordinator=coordinator,
            )

        worker = threading.Thread(target=run)
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        time.sleep(0.05)
        self.assertEqual(coordinator.stats()["inflight"], ["1", "2"])
        release.set()
        worker.join()

    def test_duplicate_key_in_one_batch_queues_then_skips(self):
        store, coordinator = self.make_env()
        calls = []

        def submit_one(row):
            calls.append(row["key"])
            return {"status": "ok", "receipt_id": "R-1"}

        rows = [
            {"id": 9, "date": "2026-03-01", "amount": "1"},
            {"id": 9, "date": "2026-03-01", "amount": "1"},
        ]
        result = submit_batch(
            rows, workers=2, submit_one=submit_one,
            store=store, coordinator=coordinator,
        )
        self.assertEqual(calls, ["9"])
        self.assertEqual(len(result.success), 1)
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(store.get("9").receipt_id, "R-1")


if __name__ == "__main__":
    unittest.main()
