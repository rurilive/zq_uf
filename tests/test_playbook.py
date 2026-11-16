import unittest

from redteam import (PlaybookError, acquire, complete, fail, halt, plan,
                     progress, recover, rollback, start, timeline)

SCRIPT = {
    "name": "演练甲",
    "owner": "张队",
    "stages": [
        {"name": "侦察", "goal": "收集资产", "owner": "李工", "after": []},
        {"name": "打点", "goal": "拿到入口", "owner": "王工", "after": ["侦察"]},
        {"name": "横向", "goal": "扩展权限", "owner": "王工", "after": ["打点"]},
    ],
}


def fresh():
    return plan({**SCRIPT, "stages": [dict(s) for s in SCRIPT["stages"]]})


class PlanTest(unittest.TestCase):
    def test_plan_defaults(self):
        result = fresh()
        self.assertEqual([s["status"] for s in result["stages"]], ["pending"] * 3)
        self.assertEqual(result["stages"][0]["owner"], "李工")
        self.assertIsNone(result["stages"][0]["started"])
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["recovered"], [])
        self.assertEqual(result["owner"], "张队")

    def test_plan_keeps_after(self):
        result = fresh()
        self.assertEqual(result["stages"][1]["after"], ["侦察"])

    def test_plan_rejects_unknown_dependency(self):
        bad = {"name": "x", "stages": [{"name": "a", "after": ["ghost"]}]}
        with self.assertRaises(PlaybookError):
            plan(bad)

    def test_plan_rejects_cycle(self):
        bad = {
            "name": "x",
            "stages": [
                {"name": "a", "after": ["b"]},
                {"name": "b", "after": ["a"]},
            ],
        }
        with self.assertRaises(PlaybookError):
            plan(bad)

    def test_plan_rejects_duplicate_stage(self):
        bad = {"name": "x", "stages": [{"name": "a"}, {"name": "a"}]}
        with self.assertRaises(PlaybookError):
            plan(bad)


class DependencyTest(unittest.TestCase):
    def test_start_blocked_by_unfinished_dependency(self):
        fresh()
        result = start("演练甲", "打点", at="2026-01-01T00:00:00+00:00")
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blocked_by"], ["侦察"])
        self.assertEqual(timeline("演练甲")["stages"][1]["status"], "pending")

    def test_start_after_dependency_done(self):
        fresh()
        start("演练甲", "侦察", at="2026-01-01T00:00:00+00:00")
        complete("演练甲", "侦察", result="12 台资产", at="2026-01-01T00:10:00+00:00")
        result = start("演练甲", "打点", at="2026-01-01T00:11:00+00:00")
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["blocked_by"], [])
        self.assertEqual(result["started"], "2026-01-01T00:11:00+00:00")

    def test_completion_does_not_autostart_next(self):
        fresh()
        start("演练甲", "侦察")
        complete("演练甲", "侦察")
        statuses = [s["status"] for s in timeline("演练甲")["stages"]]
        self.assertEqual(statuses, ["done", "pending", "pending"])


class StageEvidenceTest(unittest.TestCase):
    def test_stage_records_goal_owner_start_and_result(self):
        fresh()
        start("演练甲", "侦察", owner="李工", at="2026-01-01T00:00:00+00:00")
        complete("演练甲", "侦察", result="拿到 12 台资产",
                 at="2026-01-01T00:10:00+00:00")
        stage = timeline("演练甲")["stages"][0]
        self.assertEqual(stage["goal"], "收集资产")
        self.assertEqual(stage["owner"], "李工")
        self.assertEqual(stage["started"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(stage["finished"], "2026-01-01T00:10:00+00:00")
        self.assertEqual(stage["result"], "拿到 12 台资产")

    def test_failure_records_reason_and_current_assets(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        result = fail("演练甲", "侦察", "钓鱼邮件被拦截")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "钓鱼邮件被拦截")
        self.assertEqual([a["name"] for a in result["assets"]], ["域账号读权限"])
        stages = timeline("演练甲")["stages"]
        self.assertEqual(stages[1]["status"], "pending")
        self.assertEqual(stages[2]["status"], "pending")

    def test_failed_stage_blocks_dependents(self):
        fresh()
        start("演练甲", "侦察")
        fail("演练甲", "侦察", "入口被封")
        blocked = start("演练甲", "打点")
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["blocked_by"], ["侦察"])

    def test_rollback_records_reason(self):
        fresh()
        start("演练甲", "侦察")
        complete("演练甲", "侦察")
        result = rollback("演练甲", "侦察", "资产登记漏了")
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["reason"], "资产登记漏了")
        stage = timeline("演练甲")["stages"][0]
        self.assertEqual(stage["status"], "pending")
        self.assertIsNone(stage["started"])

    def test_rollback_rejected_when_downstream_progressed(self):
        fresh()
        start("演练甲", "侦察")
        complete("演练甲", "侦察")
        start("演练甲", "打点")
        with self.assertRaises(PlaybookError):
            rollback("演练甲", "侦察", "想重做")


class TimelineTest(unittest.TestCase):
    def test_timeline_is_ordered_and_stamped(self):
        fresh()
        start("演练甲", "侦察", at="2026-01-01T00:00:00+00:00")
        acquire("演练甲", "侦察", "域账号读权限", at="2026-01-01T00:03:00+00:00")
        complete("演练甲", "侦察", at="2026-01-01T00:10:00+00:00")
        entries = timeline("演练甲")["entries"]
        self.assertEqual([e["type"] for e in entries],
                         ["stage_started", "asset_acquired", "stage_completed"])
        self.assertEqual([e["seq"] for e in entries], [1, 2, 3])
        self.assertEqual(entries[1]["at"], "2026-01-01T00:03:00+00:00")
        self.assertEqual(entries[0]["stage"], "侦察")

    def test_timeline_reports_owner_progress(self):
        fresh()
        start("演练甲", "侦察")
        complete("演练甲", "侦察")
        progress_map = timeline("演练甲")["progress"]["by_owner"]
        self.assertEqual(progress_map["李工"]["done"], 1)
        self.assertEqual(progress_map["王工"]["pending"], 2)
        self.assertEqual(progress("演练甲")["by_owner"]["王工"]["total"], 2)

    def test_unknown_exercise(self):
        with self.assertRaises(PlaybookError):
            timeline("不存在")


class HaltTest(unittest.TestCase):
    def test_halt_lists_assets_for_recovery(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        acquire("演练甲", "侦察", "跳板机 shell")
        result = halt("演练甲", "业务方叫停")
        self.assertTrue(result["halted"])
        self.assertEqual(result["status"], "halted")
        self.assertEqual([a["name"] for a in result["assets"]],
                         ["域账号读权限", "跳板机 shell"])
        self.assertEqual(result["stages"][0]["status"], "stopped")
        self.assertEqual(result["recovered"], [])
        self.assertEqual(result["unrecovered"], [])

    def test_halt_requires_owner(self):
        fresh()
        with self.assertRaises(PlaybookError):
            halt("演练甲", "业务方叫停", by="李工")

    def test_halt_blocks_further_progress(self):
        fresh()
        start("演练甲", "侦察")
        halt("演练甲", "业务方叫停")
        with self.assertRaises(PlaybookError):
            start("演练甲", "侦察")

    def test_recovery_confirmed_item_by_item(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        acquire("演练甲", "侦察", "跳板机 shell")
        halt("演练甲", "业务方叫停")

        first = recover("演练甲", "域账号读权限", by="李工")
        self.assertEqual([a["name"] for a in first["recovered"]], ["域账号读权限"])
        self.assertEqual([a["name"] for a in first["pending_recovery"]], ["跳板机 shell"])
        self.assertEqual(first["status"], "halted")

        second = recover("演练甲", "跳板机 shell", by="李工")
        self.assertEqual(second["status"], "closed")
        self.assertTrue(second["pending_recovery"] == [])

    def test_unrecovered_asset_kept_with_blocker_note(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        halt("演练甲", "业务方叫停")
        result = recover("演练甲", "域账号读权限", ok=False,
                         note="账号被下游系统缓存，需等缓存过期")
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "halted")
        unrecovered = timeline("演练甲")["unrecovered"]
        self.assertEqual(len(unrecovered), 1)
        self.assertEqual(unrecovered[0]["note"], "账号被下游系统缓存，需等缓存过期")

    def test_failed_recovery_can_be_retried(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        halt("演练甲", "业务方叫停")
        recover("演练甲", "域账号读权限", ok=False, note="权限未生效")
        result = recover("演练甲", "域账号读权限", by="李工")
        self.assertEqual(result["status"], "closed")

    def test_failed_recovery_requires_note(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        halt("演练甲", "业务方叫停")
        with self.assertRaises(PlaybookError):
            recover("演练甲", "域账号读权限", ok=False)

    def test_exercise_not_closed_until_recovered(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        halt("演练甲", "业务方叫停")
        self.assertEqual(timeline("演练甲")["status"], "halted")
        recover("演练甲", "域账号读权限")
        self.assertEqual(timeline("演练甲")["status"], "closed")
        entries = timeline("演练甲")["entries"]
        self.assertEqual(entries[-1]["type"], "exercise_closed")

    def test_recovered_asset_cannot_repeat(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        halt("演练甲", "业务方叫停")
        recover("演练甲", "域账号读权限")
        with self.assertRaises(PlaybookError):
            recover("演练甲", "域账号读权限")


class FinishTest(unittest.TestCase):
    def test_all_stages_done_still_waits_for_recovery(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        complete("演练甲", "侦察")
        start("演练甲", "打点")
        complete("演练甲", "打点")
        start("演练甲", "横向")
        complete("演练甲", "横向")
        self.assertEqual(timeline("演练甲")["status"], "finished")
        recover("演练甲", "域账号读权限")
        self.assertEqual(timeline("演练甲")["status"], "closed")

    def test_recover_rejected_while_running(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        with self.assertRaises(PlaybookError):
            recover("演练甲", "域账号读权限")

    def test_duplicate_asset_rejected(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        with self.assertRaises(PlaybookError):
            acquire("演练甲", "侦察", "域账号读权限")

    def test_reacquire_after_rollback_is_idempotent(self):
        fresh()
        start("演练甲", "侦察")
        acquire("演练甲", "侦察", "域账号读权限")
        complete("演练甲", "侦察")
        rollback("演练甲", "侦察", "资产漏登记")
        start("演练甲", "侦察")
        result = acquire("演练甲", "侦察", "域账号读权限")
        self.assertTrue(result["repeated"])
        self.assertEqual(len(timeline("演练甲")["assets"]), 1)


if __name__ == "__main__":
    unittest.main()
