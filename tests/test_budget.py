import unittest

from jobbudget2 import (
    BudgetError,
    BudgetManager,
    checkpoint,
    start,
)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class BudgetTest(unittest.TestCase):
    def test_fixed_budget(self):
        self.assertEqual(start("a")["budget"], start("b")["budget"])

    def test_checkpoint_not_saved(self):
        self.assertFalse(checkpoint("a", {"done": 1})["saved"])

    def test_start_keeps_job(self):
        self.assertEqual(start("a")["job"], "a")


class PriorityBudgetTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr = BudgetManager(clock=self.clock)

    def test_high_priority_gets_more_than_low(self):
        hi = self.mgr.start({"name": "h", "priority": "high"})["budget"]
        lo = self.mgr.start({"name": "l", "priority": "low"})["budget"]
        self.assertGreater(hi, lo)

    def test_history_raises_budget(self):
        mgr = BudgetManager(clock=self.clock, history={"etl": [300.0, 360.0]})
        etl = mgr.start({"name": "e", "type": "etl"})["budget"]
        plain = mgr.start("plain")["budget"]
        self.assertGreater(etl, plain)

    def test_budget_assignment_audited(self):
        self.mgr.start({"name": "j", "type": "etl", "priority": "high"})
        records = [r for r in self.mgr.audit if r["event"] == "budget_assigned"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["job"], "j")
        self.assertEqual(records[0]["priority"], "high")
        self.assertGreater(records[0]["budget"], 0)


class DegradationTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr = BudgetManager(clock=self.clock)

    def test_projected_overrun_degrades_with_reason(self):
        self.mgr.start("job")  # 默认预算 60s
        self.clock.advance(50.0)
        result = self.mgr.checkpoint("job", {"done": 1, "total": 10})
        self.assertTrue(result["saved"])
        self.assertEqual(result["status"], "degraded")
        self.assertTrue(result["degraded"])
        self.assertIn("overrun", result["reason"])
        degraded = [r for r in self.mgr.audit if r["event"] == "degraded"]
        self.assertEqual(len(degraded), 1)
        self.assertEqual(degraded[0]["job"], "job")
        self.assertIn("overrun", degraded[0]["reason"])

    def test_exhausted_budget_fails_fast_with_reason(self):
        self.mgr.start("job")
        self.clock.advance(61.0)
        result = self.mgr.checkpoint("job", {"done": 5, "total": 10})
        self.assertEqual(result["status"], "failed")
        self.assertIn("exhausted", result["reason"])
        failed = [r for r in self.mgr.audit if r["event"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["reason"], result["reason"])

    def test_no_more_checkpoints_after_failure(self):
        self.mgr.start("job")
        self.clock.advance(61.0)
        self.mgr.checkpoint("job", {"done": 5, "total": 10})
        result = self.mgr.checkpoint("job", {"done": 6, "total": 10})
        self.assertFalse(result["saved"])
        self.assertIn("error", result)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr = BudgetManager(clock=self.clock)

    def test_resume_from_checkpoint_after_timeout(self):
        self.mgr.start("job")
        self.clock.advance(61.0)
        self.mgr.checkpoint("job", {"done": 4, "total": 10})  # 超时失败，进度已存
        again = self.mgr.start("job")
        self.assertTrue(again["resumed"])
        self.assertEqual(again["resume_from"], {"done": 4, "total": 10})
        resumed = [r for r in self.mgr.audit if r["event"] == "resumed"]
        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0]["job"], "job")

    def test_fresh_start_does_not_resume(self):
        self.mgr.start("job")
        self.mgr.checkpoint("job", {"done": 1, "total": 10})
        again = self.mgr.start("other")
        self.assertFalse(again["resumed"])
        self.assertIsNone(again["resume_from"])

    def test_completion_records_history(self):
        self.mgr.start({"name": "e", "type": "etl"})
        self.clock.advance(30.0)
        result = self.mgr.checkpoint("e", {"done": 10, "total": 10})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.mgr.history["etl"], [30.0])


class ErrorPathTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr = BudgetManager(clock=self.clock)

    def test_unknown_job_checkpoint_not_saved(self):
        result = self.mgr.checkpoint("ghost", {"done": 1})
        self.assertFalse(result["saved"])
        self.assertIn("ghost", result["error"])

    def test_invalid_priority_raises(self):
        with self.assertRaises(BudgetError):
            self.mgr.start({"name": "x", "priority": "urgent"})

    def test_invalid_job_raises(self):
        with self.assertRaises(BudgetError):
            self.mgr.start({"type": "etl"})

    def test_invalid_progress_raises(self):
        self.mgr.start("j")
        with self.assertRaises(BudgetError):
            self.mgr.checkpoint("j", "not-a-dict")
        with self.assertRaises(BudgetError):
            self.mgr.checkpoint("j", {"done": -1})
