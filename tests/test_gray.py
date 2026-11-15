import unittest

from schemagray import apply, plan

CHANGE = {
    "tables": [
        {"name": "dim_region", "access": "read", "task": "bi"},
        {"name": "dim_channel", "access": "read", "task": "bi"},
        {"name": "orders", "access": "write", "task": "sync"},
        {"name": "users", "access": "write", "task": "sync"},
    ],
    "batch_size": 1,
    "approver": "dba-chen",
    "summary": "add column vip_level",
}


class PlanTest(unittest.TestCase):
    def test_read_tables_gray_before_write_tables(self):
        batches = plan(CHANGE)["batches"]
        self.assertEqual(batches, [["dim_region"], ["dim_channel"], ["orders"], ["users"]])

    def test_batch_size_groups_within_direction(self):
        p = plan({**CHANGE, "batch_size": 2})
        self.assertEqual(p["batches"], [["dim_region", "dim_channel"], ["orders", "users"]])

    def test_plan_keeps_approval_and_thresholds(self):
        p = plan(CHANGE)
        self.assertEqual(p["approver"], "dba-chen")
        self.assertEqual(p["summary"], "add column vip_level")
        self.assertIn("error_rate", p["thresholds"])
        self.assertIn("latency_ms", p["thresholds"])


class ApplyTest(unittest.TestCase):
    def test_healthy_run_applies_all_batches(self):
        result = apply(plan(CHANGE))
        self.assertFalse(result["paused"])
        self.assertEqual(result["applied"], ["dim_region", "dim_channel", "orders", "users"])
        self.assertEqual(result["rolled_back"], [])
        self.assertEqual(len(result["observations"]), 4)

    def test_anomaly_pauses_and_rolls_back_only_released(self):
        def observe(batch, _idx):
            if batch == ["orders"]:
                return {"error_rate": 0.5, "latency_ms": 10.0}
            return {"error_rate": 0.0, "latency_ms": 10.0}

        result = apply(plan(CHANGE), observe=observe)
        self.assertTrue(result["paused"])
        # 只回滚已放量的表（含当前批），未放量的 users 不受影响
        self.assertEqual(result["rolled_back"], ["dim_region", "dim_channel", "orders"])
        self.assertEqual(result["applied"], [])
        self.assertIn("error_rate", result["stop_reason"])
        # 变更记录保留审批人与摘要
        self.assertEqual(result["record"]["approver"], "dba-chen")
        self.assertEqual(result["record"]["summary"], "add column vip_level")
        self.assertEqual(result["record"]["rolled_back"], result["rolled_back"])

    def test_resume_continues_from_stopped_batch(self):
        p = plan(CHANGE)
        calls = []

        def flaky(batch, _idx):
            calls.append(batch)
            return {"error_rate": 0.5 if batch == ["orders"] and len(calls) <= 3 else 0.0}

        stopped = apply(p, observe=flaky)
        self.assertTrue(stopped["paused"])
        self.assertEqual(stopped["resume_from"], 2)

        resumed = apply({**p, "resume_from": stopped["resume_from"]}, observe=flaky)
        self.assertFalse(resumed["paused"])
        self.assertEqual(resumed["applied"], ["orders", "users"])
        self.assertEqual(resumed["observations"][0]["batch"], 2)

    def test_latency_threshold_triggers_pause(self):
        result = apply(plan(CHANGE), observe=lambda b, i: {"latency_ms": 9999.0})
        self.assertTrue(result["paused"])
        self.assertEqual(result["rolled_back"], ["dim_region"])

    def test_empty_change(self):
        result = apply(plan({"tables": []}))
        self.assertFalse(result["paused"])
        self.assertEqual(result["applied"], [])


if __name__ == "__main__":
    unittest.main()
