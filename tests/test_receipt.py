import os
import tempfile
import unittest

from formfill import (
    FAILED,
    MANUAL,
    SKIPPED,
    SUCCESS,
    KeyCoordinator,
    Receipt,
    ReceiptStore,
    submit_batch,
)


ROW = {"id": 1, "date": "2026-03-01", "amount": "10"}


class ReceiptReconcileTest(unittest.TestCase):
    def setUp(self):
        self.store = ReceiptStore()
        self.coordinator = KeyCoordinator()

    def test_success_receipt_skips_replay(self):
        self.store.save(Receipt("1", "ok", receipt_id="R-1"))
        calls = []
        result = submit_batch(
            [ROW], workers=2,
            submit_one=lambda row: calls.append(row["key"]) or {"status": "ok"},
            store=self.store, coordinator=self.coordinator,
        )
        self.assertEqual(calls, [])
        self.assertEqual([item.business_key for item in result.skipped], ["1"])
        self.assertEqual(result.skipped[0].category, SKIPPED)

    def test_failed_receipt_is_replayed(self):
        self.store.save(Receipt("1", "rejected", "旧失败"))
        result = submit_batch(
            [ROW], workers=2,
            submit_one=lambda row: {"status": "ok", "receipt_id": "R-2"},
            store=self.store, coordinator=self.coordinator,
        )
        self.assertEqual(len(result.success), 1)
        self.assertEqual(self.store.get("1").status, "ok")
        self.assertEqual(self.store.get("1").receipt_id, "R-2")

    def test_timeout_receipt_waits_for_manual_without_blind_retry(self):
        self.store.save(Receipt("1", "timeout", "上次超时，状态不明", category=MANUAL))
        calls = []
        result = submit_batch(
            [ROW], workers=2,
            submit_one=lambda row: calls.append(row["key"]) or {"status": "ok"},
            store=self.store, coordinator=self.coordinator,
        )
        self.assertEqual(calls, [])
        self.assertEqual(len(result.manual), 1)

    def test_verifier_confirms_remote_success(self):
        self.store.save(Receipt("1", "timeout", "状态不明", category=MANUAL))
        result = submit_batch(
            [ROW], workers=2,
            submit_one=lambda row: {"status": "ok"},
            verifier=lambda key: {"status": "accepted", "receipt_id": "R-9"},
            store=self.store, coordinator=self.coordinator,
        )
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(self.store.get("1").category, SUCCESS)
        self.assertEqual(self.store.get("1").receipt_id, "R-9")

    def test_transport_error_goes_manual(self):
        def submit_one(row):
            raise TimeoutError("确认弹窗后提交超时")

        result = submit_batch(
            [ROW], workers=2, submit_one=submit_one,
            store=self.store, coordinator=self.coordinator,
        )
        self.assertEqual(result.manual[0].status, "timeout")
        self.assertEqual(self.store.get("1").category, MANUAL)


class ReceiptStorePersistenceTest(unittest.TestCase):
    def test_json_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "receipts.json")
            store = ReceiptStore(path)
            store.save(Receipt("k1", "ok", "提交成功", receipt_id="R-1"))
            store.save(Receipt("k2", "rejected", "被拒", category=FAILED,
                               fields=[{"field": "amount", "value": "x", "reason": "不是数字"}]))

            reloaded = ReceiptStore(path)
            self.assertEqual(reloaded.get("k1").receipt_id, "R-1")
            self.assertEqual(reloaded.get("k2").fields[0]["field"], "amount")
            self.assertTrue(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
