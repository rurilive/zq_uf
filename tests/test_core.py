import json
import os
import tempfile
import time
import unittest

from retention import Dataset, Plan, apply, plan

DAY = 86400
NOW = 1_700_000_000.0


class RetentionTest(unittest.TestCase):
    def test_plan_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.txt")
            open(path, "w").write("x")
            stamp = time.time() - 90 * 86400
            os.utime(path, (stamp, stamp))
            self.assertEqual(plan(tmp, 30), [path])

    def test_recent_file_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            open(os.path.join(tmp, "new.txt"), "w").write("x")
            self.assertEqual(plan(tmp, 30), [])

    def test_apply_removes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.txt")
            open(path, "w").write("x")
            self.assertEqual(apply([path]), [path])
            self.assertFalse(os.path.exists(path))


def write_record(root, name, age_days, tags=(), stamp_field="timestamp"):
    record = {stamp_field: NOW - age_days * DAY, "tags": list(tags)}
    with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return os.path.join(root, name)


class PolicyPlanTest(unittest.TestCase):
    def test_per_dataset_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            compliance = os.path.join(tmp, "compliance")
            business = os.path.join(tmp, "business")
            os.makedirs(compliance)
            os.makedirs(business)
            held = write_record(compliance, "c.json", 60)
            expired = write_record(business, "b.json", 60)
            result = plan(datasets=[
                Dataset("compliance", compliance, days=90),
                Dataset("business", business, days=30),
            ], now=NOW)
            self.assertIsInstance(result, Plan)
            self.assertEqual(result.paths, [expired])
            kept = result.entries[0]["kept"]
            self.assertEqual(kept[0]["path"], held)
            self.assertEqual(kept[0]["reason"], "within_retention")

    def test_age_uses_time_field_not_mtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh_copy = write_record(tmp, "copy.json", 10)
            old_stamp = NOW - 90 * DAY
            os.utime(fresh_copy, (old_stamp, old_stamp))
            stale = write_record(tmp, "stale.json", 90)
            result = plan(datasets=[Dataset("d", tmp, days=30)], now=NOW)
            self.assertEqual(result.paths, [stale])

    def test_iso_time_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "iso.json")
            with open(path, "w") as fh:
                json.dump({"timestamp": "2023-01-01T00:00:00Z"}, fh)
            result = plan(datasets=[Dataset("d", tmp, days=30)], now=NOW)
            self.assertEqual(result.paths, [path])

    def test_unparseable_file_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plain.txt")
            with open(path, "w") as fh:
                fh.write("not json")
            result = plan(datasets=[Dataset("d", tmp, days=30)], now=NOW)
            self.assertEqual(result.paths, [])
            self.assertEqual(result.entries[0]["kept"][0]["reason"],
                             "no_time_field")

    def test_min_keep_spares_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            oldest = write_record(tmp, "a.json", 200)
            middle = write_record(tmp, "b.json", 150)
            newest = write_record(tmp, "c.json", 100)
            result = plan(
                datasets=[Dataset("d", tmp, days=30, min_keep=2)], now=NOW)
            self.assertEqual(result.paths, [oldest])
            spared = {k["path"] for k in result.entries[0]["kept"]
                      if k["reason"] == "min_keep"}
            self.assertEqual(spared, {middle, newest})

    def test_exempt_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            exempt = write_record(tmp, "hold.json", 100, tags=["legal_hold"])
            normal = write_record(tmp, "normal.json", 100)
            result = plan(datasets=[Dataset(
                "d", tmp, days=30, exempt_tags=["legal_hold"])], now=NOW)
            self.assertEqual(result.paths, [normal])
            kept = result.entries[0]["kept"]
            self.assertIn({"path": exempt, "reason": "exempt_tag"}, kept)


class ApplyPlanTest(unittest.TestCase):
    def make_plan(self, tmp):
        target = write_record(tmp, "old.json", 100)
        write_record(tmp, "new.json", 1)
        return target, plan(
            datasets=[Dataset("d", tmp, days=30, batch_size=1)], now=NOW)

    def test_confirm_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, deletion_plan = self.make_plan(tmp)
            with self.assertRaises(ValueError):
                apply(deletion_plan, audit_dir=os.path.join(tmp, "audit"))

    def test_dry_run_keeps_files_and_audits(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_dir = os.path.join(tmp, "audit")
            target, deletion_plan = self.make_plan(tmp)
            self.assertEqual(apply(deletion_plan, dry_run=True,
                                   audit_dir=audit_dir), [target])
            self.assertTrue(os.path.exists(target))
            audits = os.listdir(audit_dir)
            self.assertEqual(len(audits), 1)
            with open(os.path.join(audit_dir, audits[0])) as fh:
                record = json.load(fh)
            self.assertTrue(record["dry_run"])
            self.assertEqual(record["would_remove"], 1)
            self.assertEqual(record["removed"], 0)

    def test_confirmed_apply_batches_and_audits(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_dir = os.path.join(tmp, "audit")
            extra = write_record(tmp, "older.json", 150)
            target, deletion_plan = self.make_plan(tmp)
            removed = apply(deletion_plan, confirm=True, audit_dir=audit_dir)
            self.assertEqual(sorted(removed), sorted([target, extra]))
            self.assertFalse(os.path.exists(target))
            self.assertFalse(os.path.exists(extra))
            self.assertTrue(os.path.exists(os.path.join(tmp, "new.json")))
            with open(os.path.join(
                    audit_dir, os.listdir(audit_dir)[0])) as fh:
                record = json.load(fh)
            self.assertFalse(record["dry_run"])
            self.assertEqual(record["removed"], 2)
            self.assertEqual(len(record["batches"]), 2)
            self.assertEqual(record["manifest"]["delete_count"], 2)

    def test_manifest_saved_and_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_dir = os.path.join(tmp, "manifests")
            os.makedirs(manifest_dir)
            stale = os.path.join(manifest_dir, "plan-20000101-000000-1.json")
            with open(stale, "w") as fh:
                fh.write("{}")
            old_stamp = NOW - 90 * DAY
            os.utime(stale, (old_stamp, old_stamp))
            write_record(tmp, "old.json", 100)
            result = plan(datasets=[Dataset("d", tmp, days=30)], now=NOW,
                          manifest_dir=manifest_dir)
            self.assertIsInstance(result, Plan)
            remaining = os.listdir(manifest_dir)
            self.assertEqual(len(remaining), 1)
            self.assertNotIn(os.path.basename(stale), remaining)
