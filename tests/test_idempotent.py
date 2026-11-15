import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import crawlledger
from crawlledger import append, ledger_size, verify


class LedgerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="crawlledger-test-")
        crawlledger.configure(self.dir)

    def tearDown(self):
        crawlledger.configure(None)

    def batch_file(self, batch):
        return os.path.join(self.dir, "batch-%s.jsonl" % batch)


class IdempotentTest(LedgerCase):
    def test_replay_same_seq_is_duplicate(self):
        first = append({"batch": "idem", "seq": 1, "page": "p1"})
        second = append({"batch": "idem", "seq": 1, "page": "p1"})
        self.assertTrue(first["appended"])
        self.assertFalse(first["duplicate"])
        self.assertFalse(second["appended"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["total"], 1)
        self.assertEqual(verify("idem")["rows"], 1)

    def test_auto_seq_entries_are_not_duplicates(self):
        append({"batch": "auto", "page": "p1"})
        result = append({"batch": "auto", "page": "p1"})
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["total"], 2)

    def test_seq_conflict_rejected(self):
        append({"batch": "conflict", "seq": 1, "page": "p1"})
        with self.assertRaises(ValueError):
            append({"batch": "conflict", "seq": 1, "page": "other"})

    def test_seq_gap_rejected(self):
        append({"batch": "gap", "seq": 1, "page": "p1"})
        with self.assertRaises(ValueError):
            append({"batch": "gap", "seq": 3, "page": "p3"})

    def test_replay_after_restart(self):
        append({"batch": "replay", "seq": 1, "page": "p1"})
        append({"batch": "replay", "seq": 2, "page": "p2"})
        crawlledger.reset()  # 模拟重启，从磁盘恢复
        self.assertTrue(append({"batch": "replay", "seq": 1, "page": "p1"})["duplicate"])
        self.assertEqual(append({"batch": "replay", "seq": 3, "page": "p3"})["total"], 3)


class AtomicWriteTest(LedgerCase):
    def test_failed_write_rolls_back(self):
        append({"batch": "atomic", "seq": 1, "page": "p1"})
        with open(self.batch_file("atomic"), encoding="utf-8") as fh:
            before = fh.read()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                append({"batch": "atomic", "seq": 2, "page": "p2"})
        with open(self.batch_file("atomic"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)  # 旧文件不动
        crawlledger.reset()
        self.assertEqual(verify("atomic")["rows"], 1)  # 回滚到上一个完整块
        leftovers = [n for n in os.listdir(self.dir) if n.startswith(".tmp-")]
        self.assertEqual(leftovers, [])  # 临时文件已清理

    def test_crash_leaves_no_partial_file(self):
        append({"batch": "crash", "seq": 1, "page": "p1"})
        with mock.patch("os.fsync", side_effect=OSError("power loss")):
            with self.assertRaises(OSError):
                append({"batch": "crash", "seq": 2, "page": "p2"})
        crawlledger.reset()
        result = verify("crash")
        self.assertEqual(result["rows"], 1)
        self.assertTrue(result["ok"])


class ConcurrencyTest(LedgerCase):
    def test_concurrent_same_batch_no_overwrite(self):
        errors = []

        def worker(worker_id):
            try:
                for i in range(20):
                    append({"batch": "conc", "page": "w%d-p%d" % (worker_id, i)})
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        result = verify("conc")
        self.assertEqual(result["rows"], 100)
        self.assertEqual(len(set(result["pages"])), 100)  # 无覆盖丢失
        with open(self.batch_file("conc"), encoding="utf-8") as fh:
            seqs = [
                json.loads(line)["seq"]
                for line in fh.read().splitlines()
                if json.loads(line)["type"] == "entry"
            ]
        self.assertEqual(seqs, list(range(1, 101)))  # 序号连续无空洞

    def test_concurrent_batches_isolated(self):
        def worker(batch):
            for i in range(10):
                append({"batch": batch, "seq": i + 1, "page": "%s-p%d" % (batch, i)})

        threads = [
            threading.Thread(target=worker, args=("iso-%d" % n,)) for n in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for n in range(4):
            self.assertEqual(verify("iso-%d" % n)["rows"], 10)


class VerifyRepairTest(LedgerCase):
    def test_half_line_detected_and_repaired(self):
        for i in range(1, 4):
            append({"batch": "fix", "seq": i, "page": "p%d" % i})
        with open(self.batch_file("fix"), "a", encoding="utf-8") as fh:
            fh.write('{"type": "entry", "batch": "fix", "seq": 4, "page": "p')  # 半行
        crawlledger.reset()
        result = verify("fix")
        self.assertEqual(result["half"], 1)
        self.assertEqual(result["rows"], 3)
        self.assertTrue(result["ok"])
        again = verify("fix")  # 修复后复检干净
        self.assertEqual(again["half"], 0)
        self.assertEqual(again["repaired"], 0)

    def test_bad_block_rolled_back(self):
        for i in range(1, 4):
            append({"batch": "bad", "seq": i, "page": "p%d" % i})
        with open(self.batch_file("bad"), "a", encoding="utf-8") as fh:
            fh.write('{"type": "entry", "batch": "bad", "seq": 4, "page": "p4", '
                     '"checksum": "deadbeef"}\n')
            fh.write('{"type": "commit", "seq": 4, "checksum": "deadbeef"}\n')
        crawlledger.reset()
        result = verify("bad")
        self.assertGreaterEqual(result["repaired"], 1)
        self.assertEqual(result["rows"], 3)  # 回滚到最后一个完整块
        self.assertTrue(result["ok"])

    def test_missing_commit_rolled_back(self):
        append({"batch": "nocommit", "seq": 1, "page": "p1"})
        with open(self.batch_file("nocommit"), "a", encoding="utf-8") as fh:
            entry = {"type": "entry", "batch": "nocommit", "seq": 2, "page": "p2"}
            entry["checksum"] = crawlledger.ledger._entry_checksum(
                {k: v for k, v in entry.items() if k != "type"}
            )
            fh.write(json.dumps(entry, sort_keys=True) + "\n")  # 无提交行的半块
        crawlledger.reset()
        result = verify("nocommit")
        self.assertEqual(result["rows"], 1)
        self.assertGreaterEqual(result["repaired"], 1)

    def test_reconcile_with_snapshots(self):
        append({"batch": "rec", "seq": 1, "page": "p1", "snapshot": "s1"})
        append({"batch": "rec", "seq": 2, "page": "p2", "snapshot": "s2"})
        ok = verify("rec", snapshots={"p1": "s1", "p2": "s2"})
        self.assertTrue(ok["ok"])
        self.assertEqual(ok["mismatched"], [])
        bad = verify("rec", snapshots={"p1": "s1", "p2": "tampered", "p3": "s3"})
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["mismatched"], ["p2"])
        self.assertEqual(bad["missing"], ["p3"])

    def test_completed_pages_listed(self):
        append({"batch": "pages", "seq": 1, "page": "p1"})
        append({"batch": "pages", "seq": 2, "page": "p2"})
        self.assertEqual(verify("pages")["pages"], ["p1", "p2"])


class LedgerSizeTest(LedgerCase):
    def test_size_visible_anytime(self):
        empty = ledger_size()
        self.assertEqual(empty["entries"], 0)
        append({"batch": "sized", "seq": 1, "page": "p1"})
        append({"batch": "sized", "seq": 2, "page": "p2"})
        size = ledger_size()
        self.assertEqual(size["batches"], 1)
        self.assertEqual(size["entries"], 2)
        self.assertGreater(size["bytes"], 0)


if __name__ == "__main__":
    unittest.main()
