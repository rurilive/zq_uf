import unittest

from redteam import halt, plan, start, timeline

PLAN = {
    "name": "演练甲",
    "stages": [
        {"name": "侦察", "goal": "收集资产", "after": []},
        {"name": "打点", "goal": "拿到入口", "after": ["侦察"]},
    ],
}


class PlaybookTest(unittest.TestCase):
    def test_plan_defaults(self):
        result = plan(PLAN)
        self.assertEqual([s["status"] for s in result["stages"]], ["pending", "pending"])
        self.assertIsNone(result["stages"][0]["owner"])
        self.assertIsNone(result["stages"][0]["started"])

    def test_plan_keeps_after(self):
        result = plan(PLAN)
        self.assertEqual(result["stages"][1]["after"], ["侦察"])

    def test_start_ignores_dependency(self):
        plan(PLAN)
        result = start("演练甲", "打点")
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["blocked_by"], [])

    def test_halt_has_no_assets(self):
        plan(PLAN)
        start("演练甲", "侦察")
        result = halt("演练甲", "业务方叫停")
        self.assertTrue(result["halted"])
        self.assertEqual(result["assets"], [])
        self.assertEqual(result["recovered"], [])

    def test_timeline_has_no_time(self):
        plan(PLAN)
        start("演练甲", "侦察")
        entries = timeline("演练甲")["entries"]
        self.assertIsNone(entries[0]["at"])
        self.assertEqual(entries[1]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
