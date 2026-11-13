import subprocess
import sys
import unittest
from pathlib import Path

from txrollback import (
    JOURNAL,
    PENDING,
    STORE,
    BatchError,
    commit,
    configure,
    reset,
    rollback,
    stats,
)

ROOT = Path(__file__).resolve().parent.parent


class RollbackTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_failed_commit_then_rollback_removes_written_rows(self):
        with self.assertRaises(RuntimeError):
            commit({"id": "b1", "rows": [{"key": "k1"}, {"key": "k2", "fail": True}]})
        self.assertIn("k1", STORE)
        result = rollback("b1")
        self.assertEqual(result["compensated"], ["k1"])
        self.assertEqual(result["pending"], [])
        self.assertNotIn("k1", STORE)

    def test_rollback_reverses_write_order(self):
        commit({"id": "b1", "rows": [{"key": "k1"}, {"key": "k2"}, {"key": "k3"}]})
        result = rollback("b1")
        self.assertEqual(result["compensated"], ["k3", "k2", "k1"])
        self.assertEqual(STORE, {})

    def test_rollback_is_idempotent(self):
        commit({"id": "b1", "rows": [{"key": "k1"}]})
        rollback("b1")
        again = rollback("b1")
        self.assertEqual(again["compensated"], [])
        self.assertEqual(again["pending"], [])
        self.assertEqual(stats(), {"committed": 1, "rolled_back": 1, "pending": 0})

    def test_commit_is_idempotent_for_committed_batch(self):
        batch = {"id": "b1", "rows": [{"key": "k1"}]}
        first = commit(batch)
        second = commit(batch)
        self.assertEqual(first["written"], second["written"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(stats()["committed"], 1)

    def test_compensation_failure_goes_to_pending_queue(self):
        commit({"id": "b1", "rows": [{"key": "k1"}, {"key": "k2"}]})
        STORE["k2"] = {"key": "k2", "note": "外部修改"}
        result = rollback("b1")
        self.assertEqual(result["compensated"], ["k1"])
        self.assertEqual(len(result["pending"]), 1)
        item = result["pending"][0]
        self.assertEqual(item["key"], "k2")
        self.assertEqual(item["batch_id"], "b1")
        self.assertTrue(item["reason"])
        self.assertEqual(PENDING, [item])
        self.assertEqual(STORE["k2"], {"key": "k2", "note": "外部修改"})

    def test_compensation_failure_when_key_deleted_externally(self):
        commit({"id": "b1", "rows": [{"key": "k1"}]})
        del STORE["k1"]
        result = rollback("b1")
        self.assertEqual(result["compensated"], [])
        self.assertEqual(len(result["pending"]), 1)
        self.assertEqual(stats()["pending"], 1)

    def test_update_and_delete_restore_before_image(self):
        STORE["k1"] = {"key": "k1", "v": 0}
        STORE["k2"] = {"key": "k2", "v": 0}
        commit({"id": "b1", "rows": [
            {"key": "k1", "op": "update", "v": 1},
            {"key": "k2", "op": "delete"},
        ]})
        self.assertNotIn("k2", STORE)
        rollback("b1")
        self.assertEqual(STORE["k1"], {"key": "k1", "v": 0})
        self.assertEqual(STORE["k2"], {"key": "k2", "v": 0})

    def test_stats_counts(self):
        commit({"id": "b1", "rows": [{"key": "k1"}, {"key": "k2"}]})
        commit({"id": "b2", "rows": [{"key": "k3"}]})
        STORE["k3"] = {"key": "k3", "note": "外部修改"}
        rollback("b1")
        rollback("b2")
        self.assertEqual(stats(), {"committed": 3, "rolled_back": 2, "pending": 1})

    def test_journal_records_key_and_op(self):
        commit({"id": "b1", "rows": [{"key": "k1", "op": "insert"}]})
        entry = JOURNAL["b1"]["ops"][0]
        self.assertEqual(entry["key"], "k1")
        self.assertEqual(entry["op"], "insert")

    def test_empty_rows_batch(self):
        result = commit({"id": "b1", "rows": []})
        self.assertEqual(result["written"], [])
        self.assertEqual(rollback("b1")["compensated"], [])

    def test_invalid_batch_raises_clear_error(self):
        with self.assertRaises(BatchError):
            commit("not-a-dict")
        with self.assertRaises(BatchError):
            commit({"rows": []})
        with self.assertRaises(BatchError):
            commit({"id": "b1", "rows": "not-a-list"})
        with self.assertRaises(BatchError):
            commit({"id": "b1", "rows": [{"nokey": 1}]})
        with self.assertRaises(BatchError):
            commit({"id": "b1", "rows": [{"key": "k1", "op": "upsert"}]})

    def test_update_or_delete_missing_key_raises(self):
        with self.assertRaises(BatchError):
            commit({"id": "b1", "rows": [{"key": "k1", "op": "update"}]})
        with self.assertRaises(BatchError):
            commit({"id": "b2", "rows": [{"key": "k1", "op": "delete"}]})

    def test_max_rows_threshold(self):
        configure(max_rows_per_batch=2)
        with self.assertRaises(BatchError):
            commit({"id": "b1", "rows": [{"key": "k1"}, {"key": "k2"}, {"key": "k3"}]})
        commit({"id": "b2", "rows": [{"key": "k1"}, {"key": "k2"}]})
        self.assertEqual(sorted(STORE), ["k1", "k2"])

    def test_failed_batch_can_be_retried(self):
        rows = [{"key": "k1"}, {"key": "k2", "fail": True}]
        with self.assertRaises(RuntimeError):
            commit({"id": "b1", "rows": rows})
        result = commit({"id": "b1", "rows": [{"key": "k1"}, {"key": "k2"}]})
        self.assertEqual(result["written"], ["k1", "k2"])
        self.assertEqual(sorted(STORE), ["k1", "k2"])


class DemoTest(unittest.TestCase):
    def test_demo_output_matches_expectations(self):
        proc = subprocess.run(
            [sys.executable, "demo.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(lines[0], "失败 row k2 failed")
        self.assertEqual(lines[1], "库中仍有 ['k1']")
        self.assertEqual(lines[2], "回滚补偿 ['k1'] 待人工 0")
        self.assertEqual(lines[3], "库中剩余 []")
        self.assertEqual(lines[4], "补偿失败待人工 ['k9']")
        self.assertEqual(lines[5], "统计 提交 2 回滚 1 待处理 1")


if __name__ == "__main__":
    unittest.main()
