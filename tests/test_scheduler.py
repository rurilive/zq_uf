import unittest

from jobbudget import DEGRADE_FAIL, DEGRADE_REDUCE, History, Scheduler, TaskSpec


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_scheduler(total=100.0, policy=None):
    history = History()
    history.record("etl", 80)
    history.record("report", 50)
    clock = FakeClock()
    return Scheduler(total, history=history, policy=policy, clock=clock), clock


class PriorityAllocationTest(unittest.TestCase):
    def test_budget_comes_from_history(self):
        sched, _ = make_scheduler()
        result = sched.start(TaskSpec("etl-1", "etl", priority=5))
        self.assertEqual(result["budget"], 80)
        self.assertFalse(result["degraded"])

    def test_high_priority_gets_budget_first(self):
        sched, _ = make_scheduler(total=100)
        sched.start(TaskSpec("etl-low", "etl", priority=1))
        sched.start(TaskSpec("report-high", "report", priority=9))
        allocation = sched.allocation()
        self.assertEqual(allocation["report-high"]["budget"], 50)
        self.assertFalse(allocation["report-high"]["degraded"])
        self.assertEqual(allocation["etl-low"]["budget"], 50)
        self.assertTrue(allocation["etl-low"]["degraded"])

    def test_late_high_priority_reallocates(self):
        sched, _ = make_scheduler(total=100)
        sched.start(TaskSpec("etl-low", "etl", priority=1))
        self.assertEqual(sched.allocation()["etl-low"]["budget"], 80)
        sched.start(TaskSpec("report-high", "report", priority=9))
        self.assertEqual(sched.allocation()["etl-low"]["budget"], 50)


class DegradationTest(unittest.TestCase):
    def test_reduce_records_reason(self):
        sched, _ = make_scheduler(total=100)
        sched.start(TaskSpec("report-high", "report", priority=9))
        result = sched.start(TaskSpec("etl-low", "etl", priority=1))
        self.assertTrue(result["degraded"])
        self.assertEqual(result["reason"], "budget_shortage")
        self.assertEqual(result["budget"], 50)
        degraded = sched.degraded_tasks()
        self.assertEqual(len(degraded), 1)
        self.assertEqual(degraded[0]["task"], "etl-low")
        self.assertEqual(degraded[0]["reason"], "budget_shortage")

    def test_fail_policy_fails_early(self):
        sched, _ = make_scheduler(total=100, policy=lambda p: DEGRADE_FAIL)
        sched.start(TaskSpec("etl-high", "etl", priority=9))
        result = sched.start(TaskSpec("report-low", "report", priority=1))
        self.assertTrue(result["failed"])
        self.assertEqual(result["reason"], "budget_exhausted")
        self.assertEqual(result["budget"], 0.0)

    def test_policy_adjustable_per_priority(self):
        sched, _ = make_scheduler(
            total=100, policy={9: DEGRADE_REDUCE, "*": DEGRADE_FAIL}
        )
        sched.start(TaskSpec("etl-high", "etl", priority=9))
        failed = sched.start(TaskSpec("report-low", "report", priority=0))
        degraded = sched.start(TaskSpec("etl-high-2", "etl", priority=9))
        self.assertTrue(degraded["degraded"])
        self.assertEqual(degraded["budget"], 20)
        self.assertTrue(failed["failed"])


class ResumeTest(unittest.TestCase):
    def test_checkpoint_saved_and_resumed_after_timeout(self):
        sched, clock = make_scheduler()
        sched.start(TaskSpec("etl-1", "etl", priority=5))
        clock.advance(90)
        saved = sched.checkpoint("etl-1", {"done": 3})
        self.assertTrue(saved["saved"])
        self.assertTrue(saved["timed_out"])
        restarted = sched.start(TaskSpec("etl-1", "etl", priority=5))
        self.assertEqual(restarted["resumed_from"], {"done": 3})
        self.assertEqual(sched.resume_info("etl-1")["progress"], {"done": 3})


class TimeoutAttributionTest(unittest.TestCase):
    def test_degraded_timeout_attribution(self):
        sched, clock = make_scheduler(total=100)
        sched.start(TaskSpec("report-high", "report", priority=9))
        sched.start(TaskSpec("etl-low", "etl", priority=1))
        clock.advance(60)
        sched.checkpoint("etl-low", {"done": 1})
        report = sched.timeout_report()
        self.assertEqual(report[0]["cause"], "degraded_budget_shortage")

    def test_estimate_underestimated_attribution(self):
        sched, clock = make_scheduler()
        sched.start(TaskSpec("report-1", "report", priority=5))
        clock.advance(60)
        sched.checkpoint("report-1", {"done": 1})
        report = sched.timeout_report()
        self.assertEqual(report[0]["cause"], "estimate_underestimated")
        self.assertEqual(report[0]["estimate"], 50)


class AuditTest(unittest.TestCase):
    def test_budget_change_and_degradation_audited(self):
        sched, clock = make_scheduler(total=100)
        sched.start(TaskSpec("etl-low", "etl", priority=1))
        sched.start(TaskSpec("report-high", "report", priority=9))
        clock.advance(60)
        sched.checkpoint("report-high", {"done": 2})
        sched.start(TaskSpec("report-high", "report", priority=9))
        events = [entry["event"] for entry in sched.audit()]
        self.assertIn("budget_change", events)
        self.assertIn("degraded", events)
        self.assertIn("timeout", events)
        self.assertIn("resume", events)
        degraded = [e for e in sched.audit() if e["event"] == "degraded"]
        self.assertEqual(degraded[0]["task"], "etl-low")
        self.assertEqual(degraded[0]["detail"]["reason"], "budget_shortage")


if __name__ == "__main__":
    unittest.main()
