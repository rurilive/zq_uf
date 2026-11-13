import os
import tempfile
import unittest

from auditlog import find_batch, record, replay


class AuditlogTest(unittest.TestCase):
    def test_record_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            record({"ok": 2, "failed": 1}, path)
            self.assertEqual(replay(path), {"fixed": 2, "failed": 1})

    def test_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "audit.jsonl")
            open(path, "w").close()
            self.assertEqual(replay(path), {"fixed": 0, "failed": 0})


class BatchReplayTest(unittest.TestCase):
    def test_record_tracks_failed_primary_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = [
                {"id": "k1", "v": 1},
                {"id": "k2", "v": 2},
                {"id": "k3", "v": 3},
            ]

            def handler(row):
                if row["id"] == "k2":
                    raise RuntimeError("boom")
                return True

            batch_id = record({"records": records}, handler=handler, directory=tmp)

            self.assertTrue(batch_id.startswith("b-"))
            entry, filename = find_batch(batch_id, directory=tmp)
            self.assertEqual(entry["failed_keys"], ["k2"])
            self.assertEqual(entry["output"], 2)
            self.assertEqual(entry["total"], 3)
            statuses = {e["key"]: e["status"] for e in entry["entries"]}
            self.assertEqual(statuses, {"k1": "ok", "k2": "failed", "k3": "ok"})
            self.assertEqual(
                next(e for e in entry["entries"] if e["key"] == "k2")["error"],
                "boom",
            )
            self.assertEqual(len(entry["fingerprint"]), 64)
            self.assertIsInstance(entry["elapsed"], float)
            self.assertTrue(filename.startswith("audit-2"))

    def test_incremental_replay_only_retries_failed_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = [{"id": "k1"}, {"id": "k2"}, {"id": "k3"}]
            seen = []

            def first_run(row):
                return row["id"] != "k2"

            batch_id = record({"records": records}, handler=first_run, directory=tmp)

            def repair(row):
                seen.append(row["id"])
                return row["id"] != "k3"  # k2 修复，k3 本就成功不会被重放

            result = replay(batch_id, repair, directory=tmp)

            self.assertEqual(seen, ["k2"])  # 只补失败主键
            self.assertEqual(result, {
                "batch_id": batch_id,
                "fixed": ["k2"],
                "failed": [],
                "retried": 1,
            })
            entry, _ = find_batch(batch_id, directory=tmp)
            self.assertEqual(entry["failed_keys"], [])
            self.assertEqual(entry["output"], 3)
            self.assertEqual(len(entry["replays"]), 1)

    def test_replay_compares_fixed_and_still_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = [{"id": "k1"}, {"id": "k2"}, {"id": "k3"}]
            batch_id = record(
                {"records": records, "results": {"k1": True, "k2": False, "k3": "down"}},
                directory=tmp,
            )

            def flaky(row):
                return row["id"] == "k2"  # k2 修复，k3 仍失败

            result = replay(batch_id, flaky, directory=tmp)

            self.assertEqual(result["fixed"], ["k2"])
            self.assertEqual(result["failed"], ["k3"])
            self.assertEqual(result["retried"], 2)
            entry, _ = find_batch(batch_id, directory=tmp)
            self.assertEqual(entry["failed_keys"], ["k3"])
            self.assertEqual(entry["output"], 2)
            self.assertEqual(
                {e["key"]: e["status"] for e in entry["entries"]},
                {"k1": "ok", "k2": "ok", "k3": "failed"},
            )

            # 再回放一次：只补仍失败的 k3
            second = replay(batch_id, lambda row: True, directory=tmp)
            self.assertEqual(second["fixed"], ["k3"])
            self.assertEqual(second["failed"], [])
            self.assertEqual(second["retried"], 1)

    def test_batch_searchable_across_rotated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = [{"id": "k1"}, {"id": "k2"}]
            batch_id = record(
                {"records": records, "results": {"k1": True, "k2": False}},
                directory=tmp,
            )
            original_file = os.path.join(tmp, find_batch(batch_id, directory=tmp)[1])

            # 模拟日志按天切割：旧批次文件改名，新一天写入另一个文件
            rolled = os.path.join(tmp, "audit-2020-01-01.jsonl")
            os.replace(original_file, rolled)
            new_id = record(
                {"records": [{"id": "j1"}], "results": {"j1": True}},
                directory=tmp,
            )

            self.assertNotEqual(find_batch(batch_id, directory=tmp)[1],
                                find_batch(new_id, directory=tmp)[1])
            # 索引自愈后仍可检索到旧批次
            entry, filename = find_batch(batch_id, directory=tmp)
            self.assertEqual(entry["batch_id"], batch_id)
            self.assertEqual(filename, "audit-2020-01-01.jsonl")

    def test_batch_ids_are_unique_and_fingerprints_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            records = [{"id": "k1"}]
            first = record({"records": records, "results": {"k1": True}},
                           directory=tmp)
            second = record({"records": records, "results": {"k1": True}},
                            directory=tmp)
            self.assertNotEqual(first, second)
            entry_a, _ = find_batch(first, directory=tmp)
            entry_b, _ = find_batch(second, directory=tmp)
            self.assertEqual(entry_a["fingerprint"], entry_b["fingerprint"])

    def test_replay_unknown_batch_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                find_batch("b-missing", directory=tmp)
