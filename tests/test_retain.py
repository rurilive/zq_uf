import json
import os
import tempfile
import unittest

from retention import AuditLog, RetentionError, apply, plan


class RetainTest(unittest.TestCase):
    def test_age_filter(self):
        result = plan("orders", [{"name": "p1", "age_days": 40}, {"name": "p2", "age_days": 5}])
        self.assertEqual([p["name"] for p in result["delete"]], ["p1"])

    def test_no_cascade(self):
        result = plan("orders", [{"name": "p1", "age_days": 40}])
        self.assertNotIn("cascade", result)

    def test_no_batch(self):
        self.assertNotIn("batches", apply(plan("orders", [])))

    def test_boundary_age_is_kept(self):
        result = plan("orders", [{"name": "p1", "age_days": 30}])
        self.assertEqual(result["delete"], [])


class CascadeTest(unittest.TestCase):
    def test_cascade_follows_lineage(self):
        partitions = [{"name": "p1", "age_days": 40}]
        lineage = {
            ("orders", "p1"): [("orders_replica", "p1"), ("orders_cache", "p1")],
            ("orders_replica", "p1"): [("orders_replica_cache", "p1")],
        }
        result = plan("orders", partitions, lineage=lineage)
        scope = {(item["table"], item["partition"]): item["via"]
                 for item in result["cascade"]}
        self.assertEqual(set(scope), {
            ("orders_replica", "p1"),
            ("orders_cache", "p1"),
            ("orders_replica_cache", "p1"),
        })
        self.assertEqual(scope[("orders_replica_cache", "p1")],
                         {"table": "orders_replica", "partition": "p1"})

    def test_cascade_supports_inline_downstream(self):
        partitions = [{
            "name": "p1", "age_days": 40,
            "downstream": [{"table": "orders_replica", "partition": "p1"}],
        }]
        result = plan("orders", partitions)
        self.assertEqual(
            [(item["table"], item["partition"]) for item in result["cascade"]],
            [("orders_replica", "p1")],
        )

    def test_cascade_dedupes_cycles(self):
        partitions = [{"name": "p1", "age_days": 40}]
        lineage = {
            ("orders", "p1"): [("b", "p1")],
            ("b", "p1"): [("c", "p1")],
            ("c", "p1"): [("orders", "p1"), ("b", "p1")],
        }
        result = plan("orders", partitions, lineage=lineage)
        self.assertEqual(
            sorted((item["table"], item["partition"]) for item in result["cascade"]),
            [("b", "p1"), ("c", "p1")],
        )

    def test_lease_skips_primary_and_downstream(self):
        partitions = [{
            "name": "p1", "age_days": 40,
            "lease": {"id": "lease-1"},
            "downstream": [("orders_replica", "p1")],
        }]
        result = plan("orders", partitions)
        self.assertEqual(result["delete"], [])
        self.assertNotIn("cascade", result)
        self.assertEqual(result["skipped"], [{
            "table": "orders",
            "partition": "p1",
            "reason": "active_lease",
            "lease": {"id": "lease-1"},
        }])

    def test_lease_skips_downstream_keeps_sibling(self):
        partitions = [{"name": "p1", "age_days": 40}]
        lineage = {("orders", "p1"): [("replica", "p1"), ("cache", "p1")]}
        leases = {("replica", "p1"): {"id": "lease-9"}}
        result = plan("orders", partitions, lineage=lineage, leases=leases)
        self.assertEqual(
            [(item["table"], item["partition"]) for item in result["cascade"]],
            [("cache", "p1")],
        )
        self.assertEqual(result["skipped"], [{
            "table": "replica",
            "partition": "p1",
            "reason": "active_lease",
            "lease": {"id": "lease-9"},
            "via": {"table": "orders", "partition": "p1"},
        }])

    def test_inactive_lease_is_not_skipped(self):
        partitions = [{
            "name": "p1", "age_days": 40,
            "lease": {"id": "old", "active": False},
        }]
        result = plan("orders", partitions)
        self.assertEqual([p["name"] for p in result["delete"]], ["p1"])
        self.assertEqual(result["skipped"], [])


class BatchTest(unittest.TestCase):
    def _plan(self, count=25):
        partitions = [{"name": "p%d" % i, "age_days": 40} for i in range(count)]
        return plan("orders", partitions)

    def test_apply_batches_with_size_limit(self):
        calls = []
        result = apply(self._plan(), batch_size=10, delete_func=calls.append)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["deleted"], 25)
        self.assertEqual([batch["size"] for batch in result["batches"]], [10, 10, 5])
        self.assertEqual([batch["status"] for batch in result["batches"]],
                         ["committed"] * 3)
        self.assertEqual([len(call) for call in calls], [10, 10, 5])

    def test_apply_sleeps_between_batches(self):
        sleeps = []
        result = apply(self._plan(21), batch_size=10,
                       interval_seconds=0.5, sleeper=sleeps.append)
        self.assertEqual(sleeps, [0.5, 0.5])
        self.assertEqual(len(result["batches"]), 3)

    def test_no_sleep_when_interval_zero(self):
        sleeps = []
        apply(self._plan(11), batch_size=10, interval_seconds=0, sleeper=sleeps.append)
        self.assertEqual(sleeps, [])

    def test_apply_stops_at_failed_batch(self):
        calls = []

        def delete(batch):
            calls.append(len(batch))
            if len(calls) == 2:
                raise RuntimeError("lock timeout")

        with self.assertRaises(RetentionError) as ctx:
            apply(self._plan(25), batch_size=10, delete_func=delete)
        self.assertEqual(calls, [10, 10])
        partial = ctx.exception.partial
        self.assertEqual(partial["status"], "failed")
        self.assertEqual(partial["failed_batch"], 2)
        self.assertEqual(partial["deleted"], 10)
        self.assertEqual([batch["batch_no"] for batch in partial["batches"]], [1])


class AuditTest(unittest.TestCase):
    def test_audit_records_scope_batches_and_skip_reasons(self):
        partitions = [{"name": "p%d" % i, "age_days": 40} for i in range(3)]
        lineage = {("orders", "p0"): [("replica", "p0")]}
        leases = {("replica", "p0"): {"id": "lease-2"}}
        audit = AuditLog()
        result = apply(plan("orders", partitions, lineage=lineage, leases=leases),
                       batch_size=2, delete_func=lambda batch: None,
                       audit_log=audit)

        start = audit.entries[0]
        self.assertEqual(start["event"], "retention_start")
        self.assertEqual(start["target_count"], 3)
        self.assertEqual(
            sorted((ref["table"], ref["partition"]) for ref in start["scope"]),
            [("orders", "p0"), ("orders", "p1"), ("orders", "p2")],
        )
        self.assertEqual(start["skipped"], result["skipped"])
        self.assertEqual(start["skipped"][0]["reason"], "active_lease")

        events = [entry["event"] for entry in audit.entries]
        self.assertEqual(events, [
            "retention_start",
            "batch_start", "batch_committed",
            "batch_start", "batch_committed",
        ])
        self.assertEqual(
            [entry["batch_no"] for entry in audit.entries
             if entry["event"] == "batch_start"],
            [1, 2],
        )
        self.assertEqual(result["audit"], audit.entries)

    def test_audit_log_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "retention.audit.jsonl")
            audit = AuditLog(path=path)
            partitions = [{"name": "p1", "age_days": 40}]
            apply(plan("orders", partitions), batch_size=1,
                  delete_func=lambda batch: None, audit_log=audit)
            with open(path, encoding="utf-8") as handle:
                lines = [json.loads(line) for line in handle if line.strip()]
            self.assertEqual([entry["event"] for entry in lines],
                             ["retention_start", "batch_start", "batch_committed"])
            self.assertTrue(all("ts" in entry for entry in lines))

    def test_failed_batch_is_audited(self):
        audit = AuditLog()

        def delete(batch):
            raise RuntimeError("boom")

        with self.assertRaises(RetentionError):
            apply(plan("orders", [{"name": "p1", "age_days": 40}]),
                  delete_func=delete, audit_log=audit)
        self.assertEqual(audit.entries[-1]["event"], "batch_failed")
        self.assertEqual(audit.entries[-1]["error"], "boom")
