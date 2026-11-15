"""jobbudget3 行为测试。"""

import json
import os
import tempfile
import unittest

from jobbudget3 import (
    ACTION_CONSERVE,
    ACTION_EARLY_FAIL,
    ACTION_MINIMAL,
    ACTION_REDUCE,
    ACTION_RESUME,
    ACTION_TIMEOUT,
    ACTION_TOPUP,
    BudgetError,
    BudgetExhausted,
    BudgetManager,
    policy,
)


class FakeClock:
    """可手动推进的时钟，避免测试依赖真实时间。"""

    def __init__(self, start=1_700_000_000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_path = os.path.join(self._tmp.name, "state.json")
        self.clock = FakeClock()

    def manager(self, total_budget=None, **kwargs):
        return BudgetManager(
            total_budget=total_budget,
            state_path=self.state_path,
            clock=self.clock,
            **kwargs,
        )


class AllocationTest(ManagerTestCase):
    def test_small_job_gets_less_than_big_job(self):
        small = self.manager().start({"name": "s", "type": "quick"})
        big = self.manager(total_budget=None).start({"name": "b", "type": "backfill"})
        self.assertLess(small["budget"], big["budget"])
        self.assertNotEqual(small["budget"], policy.LEGACY_BUDGET)
        self.assertNotEqual(big["budget"], policy.LEGACY_BUDGET)

    def test_priority_scales_budget(self):
        low = policy.allocate({"type": "batch", "priority": 2})["requested_budget"]
        high = policy.allocate({"type": "batch", "priority": 9})["requested_budget"]
        self.assertGreater(high, low)
        self.assertAlmostEqual(high / low, policy.priority_multiplier(9) / policy.priority_multiplier(2), places=2)

    def test_history_raises_budget_above_type_base(self):
        without = policy.allocate({"type": "small"})["requested_budget"]
        with_history = policy.allocate({"type": "small", "history": [5000, 5200, 5100]})["requested_budget"]
        self.assertGreater(with_history, without)
        self.assertAlmostEqual(without, policy.TYPE_BUDGETS["small"], delta=1)

    def test_history_shapes_are_accepted(self):
        expected = policy.history_estimate([100, 200, 300])
        self.assertEqual(expected, 300)
        self.assertEqual(policy.history_estimate({"p90": 300}), 300)
        self.assertEqual(policy.history_estimate({"samples": [100, 200, 300]}), 300)
        self.assertIsNone(policy.history_estimate([]))
        self.assertIsNone(policy.history_estimate("n/a"))

    def test_budget_is_clamped(self):
        allocated = policy.allocate({"type": "backfill", "priority": 10, "history": [999999]})
        self.assertLessEqual(allocated["requested_budget"], policy.MAX_BUDGET)

    def test_unknown_type_falls_back_to_default(self):
        self.assertEqual(policy.normalize_type("mystery"), "mystery")
        self.assertEqual(policy.type_base_budget("mystery"), policy.DEFAULT_BUDGET)


class DegradeTest(ManagerTestCase):
    def test_pool_shortage_reduces_budget_with_reason(self):
        manager = self.manager(total_budget=1800)
        result = manager.start({"name": "job", "type": "normal"})
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degradation"], ACTION_REDUCE)
        self.assertEqual(result["budget"], 1800)
        self.assertLess(result["budget"], result["requested_budget"])
        self.assertIn("资源池", result["reason"])

        entries = manager.actions("job")
        self.assertEqual([e["action"] for e in entries], [ACTION_REDUCE])
        self.assertIn("资源池", entries[0]["reason"])

    def test_pool_empty_fails_fast(self):
        manager = self.manager(total_budget=1800)
        manager.start({"name": "first", "type": "normal"}, budget=1700)
        manager.finish("first", used=1700)
        result = manager.start({"name": "second", "type": "normal"}, budget=3600)
        self.assertTrue(result["halted"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["degradation"], ACTION_EARLY_FAIL)
        self.assertIn("提前失败", result["reason"])
        self.assertEqual(manager.actions("second")[-1]["action"], ACTION_EARLY_FAIL)

    def test_running_job_reserves_its_budget(self):
        manager = self.manager(total_budget=4000)
        manager.start({"name": "a", "type": "normal"}, budget=3600)
        # 运行中的任务整份预算已被预留，剩余 400s 不足以再启动一个大任务
        self.assertEqual(manager.pool_available(), 400)
        result = manager.start({"name": "b", "type": "normal"}, budget=3600)
        self.assertTrue(result["halted"])
        self.assertEqual(result["degradation"], ACTION_EARLY_FAIL)
        self.assertEqual(result["remaining_pool"], 400)

    def test_conserve_then_minimal(self):
        manager = self.manager()
        started = manager.start({"name": "job", "type": "normal"}, budget=1000)
        self.assertFalse(started["degraded"])

        # 剩余 80%：不降级
        self.clock.advance(200)
        self.assertFalse(manager.checkpoint("job", 0.2)["degraded"])

        # 剩余 20%：保守模式
        self.clock.advance(600)
        result = manager.checkpoint("job", 0.8)
        self.assertTrue(result["degraded"])
        self.assertEqual(result["degradation"], ACTION_CONSERVE)

        # 剩余 5%：只保留必要步骤
        self.clock.advance(150)
        result = manager.checkpoint("job", 0.95)
        self.assertEqual(result["degradation"], ACTION_MINIMAL)

        kinds = [e["action"] for e in manager.actions("job")]
        self.assertEqual(kinds, [ACTION_CONSERVE, ACTION_MINIMAL])

    def test_timeout_stops_execution(self):
        manager = self.manager()
        manager.start({"name": "job", "type": "normal"}, budget=100)
        self.clock.advance(120)
        result = manager.checkpoint("job", {"cursor": 7})
        self.assertTrue(result["halted"])
        self.assertTrue(result["saved"])
        self.assertEqual(result["status"], "timed_out")
        self.assertEqual(result["degradation"], ACTION_TIMEOUT)
        self.assertEqual(result["resume_from"], {"cursor": 7})
        self.assertIn("停止执行", result["reason"])

        # 超时后再次检查不应继续推进，也不再产生动作
        before = len(manager.actions("job"))
        again = manager.checkpoint("job", {"cursor": 8})
        self.assertTrue(again["halted"])
        self.assertEqual(len(manager.actions("job")), before)
        self.assertEqual(again["status"], "timed_out")

    def test_check_raises_when_exhausted(self):
        manager = self.manager()
        manager.start({"name": "job", "type": "normal"}, budget=60)
        self.clock.advance(61)
        with self.assertRaises(BudgetExhausted) as ctx:
            manager.check("job")
        self.assertEqual(ctx.exception.result["status"], "timed_out")

    def test_actions_are_queryable_and_persisted(self):
        manager = self.manager(total_budget=1800)
        manager.start({"name": "job", "type": "normal"})
        reopened = self.manager(total_budget=1800)
        entries = reopened.actions("job")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["action"], ACTION_REDUCE)
        self.assertTrue(entries[0]["reason"])
        self.assertTrue(entries[0]["at"])


class TopupTest(ManagerTestCase):
    def test_high_priority_job_can_top_up(self):
        manager = self.manager()
        manager.start({"name": "urgent", "type": "normal", "priority": 9}, budget=600)
        result = manager.topup("urgent", 300, reason="客户报障，需补跑迁移")
        self.assertTrue(result["granted"])
        self.assertEqual(result["budget"], 900)
        self.assertEqual(result["reason"], "客户报障，需补跑迁移")
        entry = manager.actions("urgent")[-1]
        self.assertEqual(entry["action"], ACTION_TOPUP)
        self.assertTrue(entry["granted"])
        self.assertEqual(entry["seconds"], 300)

        # 追加后不再超时
        self.clock.advance(600)
        self.assertFalse(manager.checkpoint("urgent", 0.5)["halted"])

    def test_low_priority_top_up_is_denied_with_reason(self):
        manager = self.manager()
        manager.start({"name": "minor", "type": "normal", "priority": 3}, budget=600)
        result = manager.topup("minor", 300)
        self.assertFalse(result["granted"])
        self.assertEqual(result["budget"], 600)
        self.assertIn("高优任务", result["reason"])
        self.assertFalse(manager.actions("minor")[-1]["granted"])

    def test_top_up_limited_by_pool(self):
        manager = self.manager(total_budget=1000)
        manager.start({"name": "urgent", "type": "normal", "priority": 10}, budget=900)
        result = manager.topup("urgent", 500)
        self.assertFalse(result["granted"])
        self.assertIn("资源池", result["reason"])


class ResumeTest(ManagerTestCase):
    def test_resume_continues_from_checkpoint(self):
        manager = self.manager()
        manager.start({"name": "job", "type": "backfill", "priority": 7}, budget=100)
        self.clock.advance(400)
        manager.checkpoint("job", {"cursor": 500})
        self.clock.advance(200)
        timed_out = manager.checkpoint("job", {"cursor": 650})
        self.assertTrue(timed_out["halted"])
        self.assertEqual(timed_out["resume_from"], {"cursor": 650})

        resumed = manager.resume("job")
        self.assertEqual(resumed["resume_from"], {"cursor": 650})
        self.assertEqual(resumed["status"], "running")
        self.assertFalse(resumed["halted"])
        self.assertGreater(resumed["budget"], 0)
        self.assertEqual(resumed["resumes"], 1)
        self.assertEqual(manager.actions("job")[-1]["action"], ACTION_RESUME)

        # 恢复后重新计时：已用从 0 开始
        self.assertEqual(resumed["used"], 0)
        self.assertFalse(manager.checkpoint("job", {"cursor": 700})["halted"])

    def test_resume_survives_process_restart(self):
        manager = self.manager()
        manager.start({"name": "job", "type": "normal"}, budget=50)
        self.clock.advance(60)
        manager.checkpoint("job", {"cursor": 42})
        manager.finish("job", status="timed_out", used=60)

        restarted = self.manager()
        resumed = restarted.resume("job")
        self.assertEqual(resumed["resume_from"], {"cursor": 42})
        self.assertEqual(resumed["status"], "running")

    def test_resume_rejects_running_job(self):
        manager = self.manager()
        manager.start({"name": "job", "type": "normal"}, budget=600)
        with self.assertRaises(BudgetError):
            manager.resume("job")

    def test_resume_unknown_job_raises(self):
        with self.assertRaises(KeyError):
            self.manager().resume("ghost")


class ShadowTest(ManagerTestCase):
    def test_shadow_observes_without_enforcing(self):
        manager = self.manager(shadow=True, shadow_days=7)
        self.assertTrue(manager.shadow_active())
        started = manager.start({"name": "job", "type": "normal"}, budget=100)
        self.assertEqual(started["budget"], 100)

        # 剩余 1%：影子模式下只记录"本应降为 minimal"
        self.clock.advance(99)
        result = manager.checkpoint("job", {"cursor": 9})
        self.assertFalse(result["halted"])
        self.assertTrue(result["shadow"])
        self.assertIn(f"shadow:{ACTION_MINIMAL}", result["would"])
        self.assertEqual(result["status"], "running")

        # 已用超出预算：只记录"本应超时"，仍然继续执行
        self.clock.advance(1)
        overtime = manager.checkpoint("job", {"cursor": 10})
        self.assertFalse(overtime["halted"])
        self.assertIn(f"shadow:{ACTION_TIMEOUT}", overtime["would"])
        self.assertEqual(overtime["status"], "running")

        finished = manager.finish("job", used=120)
        self.assertTrue(finished["shadow"])

        report = manager.shadow_report()
        self.assertEqual(report["jobs"], 1)
        self.assertEqual(report["overruns"], 1)
        self.assertEqual(report["would_timeout"], ["job"])
        self.assertEqual(report["false_kill_risk"], ["job"])
        self.assertGreater(report["max_overrun_ratio"], 1.0)

        # 影子期只记录，不产生真正的降级动作
        self.assertFalse([a for a in manager.actions("job") if not a["action"].startswith("shadow:")])
        self.assertFalse(finished["degraded"])

    def test_shadow_expires_after_seven_days(self):
        manager = self.manager(shadow=True, shadow_days=7)
        self.assertTrue(manager.shadow_active())
        self.clock.advance(7 * 86400 + 1)
        self.assertFalse(manager.shadow_active())

        # 到期后恢复正常拦截
        manager.start({"name": "job", "type": "normal"}, budget=60)
        self.clock.advance(61)
        self.assertTrue(manager.checkpoint("job")["halted"])

    def test_shadow_records_pool_shortage(self):
        manager = self.manager(total_budget=1000, shadow=True)
        result = manager.start({"name": "job", "type": "normal"})
        self.assertFalse(result["halted"])
        self.assertEqual(result["budget"], result["requested_budget"])
        self.assertTrue(any(a["action"].startswith("shadow:") for a in manager.actions("job")))


class PersistenceTest(ManagerTestCase):
    def test_state_is_written_to_disk(self):
        manager = self.manager()
        manager.start({"name": "job", "type": "normal"}, budget=600)
        manager.checkpoint("job", {"cursor": 3})
        self.assertTrue(os.path.exists(self.state_path))
        with open(self.state_path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["jobs"]["job"]["progress"], {"cursor": 3})

    def test_finish_feeds_history_into_next_allocation(self):
        manager = self.manager()
        manager.start({"name": "job", "type": "small", "priority": 5}, budget=1200)
        manager.finish("job", used=1100)

        second = manager.start({"name": "job", "type": "small", "priority": 5})
        self.assertAlmostEqual(second["history_estimate"], 1100)
        self.assertGreater(second["budget"], policy.TYPE_BUDGETS["small"])

    def test_corrupt_state_is_quarantined(self):
        with open(self.state_path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        manager = self.manager()
        result = manager.start({"name": "job", "type": "normal"})
        self.assertEqual(result["status"], "running")
        self.assertTrue(any(name.startswith("state.json.corrupt-") for name in os.listdir(self._tmp.name)))

    def test_finish_is_idempotent(self):
        manager = self.manager(shadow=True)
        manager.start({"name": "job", "type": "normal"}, budget=600)
        manager.finish("job", used=100)
        manager.finish("job", used=100)
        report = manager.shadow_report()
        self.assertEqual(report["jobs"], 1)
        self.assertEqual(manager.status("job")["status"], "completed")
        # 已完成任务再 checkpoint 不会被当成超时
        self.assertFalse(manager.checkpoint("job")["halted"])

    def test_status_reports_budget_used_and_resume_point(self):
        manager = self.manager()
        manager.start({"name": "job", "type": "normal"}, budget=600)
        self.clock.advance(60)
        manager.checkpoint("job", {"cursor": 5})
        info = manager.status("job")
        self.assertEqual(info["budget"], 600)
        self.assertAlmostEqual(info["used"], 60)
        self.assertAlmostEqual(info["remaining"], 540)
        self.assertEqual(info["resume_from"], {"cursor": 5})
        self.assertAlmostEqual(info["remaining_fraction"], 0.9, places=3)
        with self.assertRaises(KeyError):
            manager.status("nobody")

    def test_started_job_is_not_restarted(self):
        manager = self.manager()
        manager.start({"name": "job", "type": "normal"})
        again = manager.start({"name": "job", "type": "normal"})
        self.assertTrue(again["already_started"])


class CompatTest(unittest.TestCase):
    """旧的"固定一小时"行为已被替换，返回结构向后兼容。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._old = os.environ.get("JOBBUDGET_STATE")
        os.environ["JOBBUDGET_STATE"] = os.path.join(self._tmp.name, "state.json")
        os.environ.pop("JOBBUDGET_TOTAL", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("JOBBUDGET_STATE", None)
        else:
            os.environ["JOBBUDGET_STATE"] = self._old

    def test_module_level_helpers(self):
        import jobbudget3

        started = jobbudget3.start({"name": "a"})
        self.assertIn("budget", started)
        self.assertIn("used", started)
        self.assertIn("degraded_actions", started)
        self.assertIn("resume_from", started)

        saved = jobbudget3.checkpoint("a", 0.5)
        self.assertTrue(saved["saved"])
        self.assertEqual(saved["resume_from"], 0.5)

        queried = jobbudget3.actions("a")
        self.assertIsInstance(queried, list)
