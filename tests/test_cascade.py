import json
import os
import tempfile
import unittest

from partretention import apply, load_plan, plan, rollback, save_plan

CATALOG = {
    "lineage": {"orders": ["orders_daily", "orders_agg"], "orders_daily": ["orders_hourly"]},
    "partitions": {
        "orders_daily": ["2025-01-01", "2025-01-02", "2025-01-09"],
        "orders_agg": ["2025-01-01"],
        "orders_hourly": ["2025-01-01"],
    },
    "leases": {},
    "schema_changed": [],
}


def make_dataset(**kw):
    dataset = {"name": "orders", "days": ["2025-01-01", "2025-01-02"], "catalog": CATALOG}
    dataset.update(kw)
    return dataset


class CascadeTest(unittest.TestCase):
    def test_cascade_marks_downstream(self):
        p = plan(make_dataset())
        names = [d["name"] for d in p["downstream"]]
        self.assertEqual(names, ["orders_daily", "orders_agg", "orders_hourly"])
        # 只级联与过期天重合的分区
        daily = p["downstream"][0]
        self.assertEqual(daily["days"], ["2025-01-01", "2025-01-02"])

    def test_cascade_depth_limit(self):
        p = plan(make_dataset(), max_depth=1)
        names = [d["name"] for d in p["downstream"]]
        self.assertEqual(names, ["orders_daily", "orders_agg"])
        self.assertTrue(any("深度" in w for w in p["warnings"]))

    def test_apply_cascades(self):
        result = apply(plan(make_dataset()))
        self.assertEqual(result["deleted"], ["2025-01-01", "2025-01-02"])
        self.assertIn("orders_daily:2025-01-01", result["cascaded"])
        self.assertIn("orders_hourly:2025-01-01", result["cascaded"])


class LeaseTest(unittest.TestCase):
    def test_active_lease_downstream_skipped(self):
        catalog = dict(CATALOG, leases={"orders_hourly": ["lease-1"]})
        p = plan(make_dataset(), catalog=catalog)
        self.assertEqual([d["name"] for d in p["downstream"]], ["orders_daily", "orders_agg"])
        self.assertEqual(p["skipped"][0]["dataset"], "orders_hourly")
        self.assertEqual(p["skipped"][0]["reason"], "active_lease")

    def test_active_lease_on_main_skips_days(self):
        catalog = dict(CATALOG, leases={"orders": ["lease-9"]})
        p = plan(make_dataset(), catalog=catalog)
        self.assertEqual(p["expired"], [])
        self.assertEqual(len(p["skipped"]), 2)

    def test_schema_changed_skipped_with_warning(self):
        catalog = dict(CATALOG, schema_changed=["orders_agg"])
        p = plan(make_dataset(), catalog=catalog)
        self.assertNotIn("orders_agg", [d["name"] for d in p["downstream"]])
        self.assertTrue(any("orders_agg" in w for w in p["warnings"]))
        self.assertIn({"dataset": "orders_agg", "reason": "schema_changed"}, p["skipped"])


class BatchTest(unittest.TestCase):
    def test_batches_split_by_size(self):
        p = plan({"name": "d", "days": ["d1", "d2", "d3", "d4", "d5"]}, batch_size=2)
        self.assertEqual(p["batches"], 3)
        result = apply(p)
        self.assertEqual(result["batches"], 3)
        self.assertEqual([len(r["items"]) for r in result["batch_records"]], [2, 2, 1])
        self.assertEqual(result["deleted"], ["d1", "d2", "d3", "d4", "d5"])

    def test_dry_run_deletes_nothing(self):
        result = apply(plan(make_dataset()), dry_run=True)
        self.assertEqual(result["deleted"], [])
        self.assertEqual(result["cascaded"], [])
        self.assertTrue(all(r["status"] == "dry_run" for r in result["batch_records"]))


class RollbackTest(unittest.TestCase):
    def test_failure_stops_batches_and_rolls_back(self):
        catalog = dict(CATALOG, fail_on=["orders:2025-01-02"])
        p = plan(make_dataset(), catalog=catalog, batch_size=1)
        result = apply(p)
        self.assertEqual(result["failed_batch"], 1)
        self.assertEqual(result["batches"], 2)  # 第 3 批起不再执行
        self.assertEqual(result["deleted"], ["2025-01-01"])
        result = rollback(result, result["failed_batch"])
        self.assertEqual(result["rolled_back"], ["orders:2025-01-02"])
        self.assertEqual(result["deleted"], ["2025-01-01"])
        self.assertEqual(result["batch_records"][1]["status"], "rolled_back")

    def test_rollback_unknown_batch_raises(self):
        result = apply(plan({"name": "d", "days": ["d1"]}))
        self.assertRaises(KeyError, rollback, result, 9)


class PlanPersistenceTest(unittest.TestCase):
    def test_save_and_reexecute(self):
        p = plan(make_dataset())
        with tempfile.TemporaryDirectory() as tmp:
            path = save_plan(p, os.path.join(tmp, "plan.json"))
            again = apply(load_plan(path))
        self.assertEqual(again["deleted"], ["2025-01-01", "2025-01-02"])

    def test_archive_by_day_and_audit(self):
        p = plan(make_dataset())
        with tempfile.TemporaryDirectory() as tmp:
            result = apply(p, archive_dir=os.path.join(tmp, "arch"),
                           audit_path=os.path.join(tmp, "audit.jsonl"))
            self.assertTrue(os.path.exists(
                os.path.join(tmp, "arch", "2025-01-01", "orders.json")))
            self.assertTrue(os.path.exists(
                os.path.join(tmp, "arch", "2025-01-01", "orders_daily.json")))
            with open(os.path.join(tmp, "audit.jsonl"), encoding="utf-8") as fh:
                audit = json.loads(fh.readline())
        self.assertEqual(audit["dataset"], "orders")
        self.assertEqual(audit["summary"]["orders"]["deleted"], 2)
        self.assertEqual(result["audit_summary"]["orders_daily"]["cascaded"], 2)


if __name__ == "__main__":
    unittest.main()
