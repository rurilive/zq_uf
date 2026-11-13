import os
import json
import tempfile
import time
import unittest

from partitiongc import apply, plan


class PartitiongcTest(unittest.TestCase):
    def test_plan_old_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = os.path.join(tmp, "2020-01-01")
            os.makedirs(old)
            stamp = time.time() - 90 * 86400
            os.utime(old, (stamp, stamp))
            self.assertEqual(plan(tmp, keep_days=30), [old])

    def test_recent_dir_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "today"))
            self.assertEqual(plan(tmp, keep_days=30), [])

    def test_apply_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old")
            os.makedirs(path)
            self.assertEqual(apply([path]), [path])
            self.assertFalse(os.path.exists(path))

    def _make_partition(self, root, name, data_time, dataset=None,
                        lease_expires_at=None):
        path = os.path.join(root, name)
        os.makedirs(path)
        meta = {"event_time": data_time}
        if dataset is not None:
            meta["dataset"] = dataset
        with open(os.path.join(path, "partition.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(meta, handle)
        if lease_expires_at is not None:
            lease_dir = os.path.join(path, ".leases")
            os.makedirs(lease_dir)
            with open(os.path.join(lease_dir, "reader-a.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"reader": "reader-a",
                           "expires_at": lease_expires_at}, handle)
        return path

    def test_plan_identifies_irregular_names_by_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = self._make_partition(
                tmp, "weird-name-001",
                "2020-01-15T10:00:00+00:00")
            recent = self._make_partition(
                tmp, "another_weird_partition",
                time.time() - 86400)
            # 让目录修改时间都很旧，证明识别依据是元数据时间字段而非目录名/mtime
            old_stamp = time.time() - 200 * 86400
            os.utime(recent, (old_stamp, old_stamp))
            planned = plan(tmp, keep_days=30)
            self.assertEqual(planned, [old])
            self.assertEqual(planned.entries[0]["time_source"], "metadata")

    def test_apply_skips_active_lease_and_records_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked = self._make_partition(
                tmp, "locked-partition",
                "2020-02-01T00:00:00+00:00",
                lease_expires_at=time.time() + 3600)
            free = self._make_partition(
                tmp, "free-partition",
                "2020-02-02T00:00:00+00:00",
                lease_expires_at=time.time() - 3600)
            planned = plan(tmp, keep_days=30, batch_size=10)
            result = apply(planned)
            self.assertEqual(result, [free])
            self.assertTrue(os.path.exists(locked))
            self.assertEqual(len(result.skipped), 1)
            self.assertEqual(result.skipped[0]["path"], locked)
            self.assertEqual(result.skipped[0]["reason"], "lease_active")
            self.assertTrue(
                result.skipped[0]["detail"][0]["reader"], "reader-a")

    def test_apply_commits_in_batches_with_size_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [
                self._make_partition(
                    tmp, "p-%02d" % index,
                    "2020-03-%02dT00:00:00+00:00" % (index + 1))
                for index in range(5)
            ]
            planned = plan(tmp, keep_days=30, batch_size=2)
            self.assertEqual([len(batch) for batch in planned.batches],
                             [2, 2, 1])
            result = apply(planned)
            self.assertEqual(sorted(result), sorted(paths))
            self.assertEqual([len(batch["removed"]) for batch in
                              result.batches], [2, 2, 1])
            for path in paths:
                self.assertFalse(os.path.exists(path))

    def test_audit_log_records_plan_delete_skip_and_batches(self):
        with tempfile.TemporaryDirectory() as tmp:
            locked = self._make_partition(
                tmp, "audit-locked",
                "2020-04-10T00:00:00+00:00",
                lease_expires_at=time.time() + 3600)
            removed = self._make_partition(
                tmp, "audit-free",
                "2020-04-11T00:00:00+00:00")
            audit_path = os.path.join(tmp, "audit.jsonl")
            planned = plan(tmp, keep_days=30, batch_size=1,
                           audit_path=audit_path)
            result = apply(planned)
            self.assertEqual(result.audit_path, audit_path)
            with open(audit_path, encoding="utf-8") as handle:
                events = [json.loads(line) for line in handle if line.strip()]
            by_event = {}
            for event in events:
                by_event.setdefault(event["event"], []).append(event)
            self.assertEqual(
                [item["path"] for item in by_event["plan"][0]["candidates"]],
                [locked, removed])
            self.assertEqual(
                [event["path"] for event in by_event["delete"]], [removed])
            skip_events = by_event["skip"]
            self.assertEqual(len(skip_events), 1)
            self.assertEqual(skip_events[0]["path"], locked)
            self.assertEqual(skip_events[0]["reason"], "lease_active")
            self.assertEqual(len(by_event["batch"]), 2)

    def test_keep_days_override_per_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            hot = self._make_partition(
                tmp, "hot-1", "2020-05-01T00:00:00+00:00", dataset="hot")
            cold = self._make_partition(
                tmp, "cold-1", "2020-05-01T00:00:00+00:00", dataset="cold")
            planned = plan(tmp, keep_days={"hot": 36500, "default": 30})
            self.assertEqual(planned, [cold])
            self.assertNotIn(hot, planned)
