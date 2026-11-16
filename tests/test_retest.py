import unittest

from vulnretest import history, retest
from vulnretest.retest import _reset

BASE = [
    {"name": "提交恶意参数", "ok": False, "evidence": "http://old/1"},
    {"name": "查看响应回显", "ok": False, "evidence": "http://old/2"},
]

FIXED = [
    {"name": "提交恶意参数", "ok": True, "evidence": "http://new/1"},
    {"name": "查看响应回显", "ok": True, "evidence": "http://new/2"},
]


def vuln(steps=None, **extra):
    payload = {"id": "V-7", "project": "shop", "steps": steps or BASE}
    payload.update(extra)
    return payload


class RetestTest(unittest.TestCase):
    def setUp(self):
        _reset()

    # —— 基线与对照 ——

    def test_first_round_establishes_baseline_without_closing(self):
        result = retest(vuln(FIXED))
        self.assertEqual(result["result"], "baseline")
        self.assertFalse(result["closed"])
        self.assertTrue(result["baseline"]["established"])
        self.assertEqual(result["diff"], [])
        # 基线沿用本轮步骤，供下次对照。
        self.assertEqual(
            [step["name"] for step in result["baseline"]["steps"]],
            ["提交恶意参数", "查看响应回显"],
        )
        self.assertEqual(result["baseline"]["source"], "current_run")

    def test_baseline_can_come_from_before_fix_evidence(self):
        result = retest(vuln(FIXED, baseline={"at": "2026-11-01T00:00:00Z", "steps": BASE}))
        self.assertEqual(result["result"], "pass")
        self.assertTrue(result["closed"])
        self.assertEqual(result["baseline"]["source"], "provided")
        self.assertEqual(result["baseline"]["at"], "2026-11-01T00:00:00Z")
        # 修复前的证据保留在差异里。
        self.assertEqual(
            [row["status"] for row in result["diff"]], ["fixed", "fixed"]
        )
        self.assertEqual(result["diff"][0]["evidence_before"], "http://old/1")

    def test_fail_with_baseline_records_failure_point_and_evidence(self):
        still_broken = [
            {"name": "提交恶意参数", "ok": True, "evidence": "http://new/1"},
            {
                "name": "查看响应回显",
                "ok": False,
                "evidence": "http://new/2",
                "reason": "响应仍回显脚本",
            },
        ]
        result = retest(vuln(still_broken, baseline={"steps": BASE}))
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["closed"])
        self.assertEqual(len(result["failures"]), 1)
        failure = result["failures"][0]
        self.assertEqual(failure["step"], "查看响应回显")
        self.assertEqual(failure["reason"], "响应仍回显脚本")
        self.assertEqual(failure["evidence"], "http://new/2")
        self.assertFalse(failure["baseline_ok"])
        self.assertEqual(
            [row["status"] for row in result["diff"]], ["fixed", "unchanged"]
        )

    def test_step_that_used_to_pass_is_marked_regressed(self):
        baseline = [
            {"name": "提交恶意参数", "ok": False},
            {"name": "查看响应回显", "ok": True},
        ]
        steps = [
            {"name": "提交恶意参数", "ok": True},
            {"name": "查看响应回显", "ok": False, "reason": "修复引入新回显"},
        ]
        result = retest(vuln(steps, baseline={"steps": baseline}))
        self.assertEqual([row["status"] for row in result["diff"]], ["fixed", "regressed"])
        self.assertEqual(result["failures"][0]["reason"], "修复引入新回显")

    def test_pass_requires_every_step_ok(self):
        empty_steps_or_missing = {"id": "V-1", "steps": []}
        with self.assertRaises(ValueError):
            retest(empty_steps_or_missing)
        with self.assertRaises(ValueError):
            retest({"id": "V-1"})
        with self.assertRaises(ValueError):
            retest({"id": "V-1", "steps": [{"name": "a", "ok": "yes"}]})

    # —— 多轮与差异 ——

    def test_second_round_compares_with_previous_round(self):
        retest(vuln(BASE))
        result = retest(vuln(FIXED))
        self.assertEqual(result["result"], "pass")
        self.assertEqual(result["since_last"]["round"], 1)
        self.assertTrue(result["since_last"]["changed"])
        self.assertEqual(
            result["since_last"]["steps_changed"],
            [
                {"step": "提交恶意参数", "was_ok": False, "ok": True},
                {"step": "查看响应回显", "was_ok": False, "ok": True},
            ],
        )

    def test_repeat_round_reports_no_change(self):
        retest(vuln(BASE))
        result = retest(vuln(BASE))
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["since_last"]["changed"])
        self.assertEqual(result["since_last"]["steps_changed"], [])

    def test_step_change_regenerates_baseline(self):
        retest(vuln(BASE))
        changed = [
            {"name": "提交恶意参数", "ok": True},
            {"name": "检查响应头", "ok": True},
        ]
        result = retest(vuln(changed))
        self.assertEqual(result["result"], "baseline")
        self.assertTrue(result["baseline"]["regenerated"])
        self.assertFalse(result["closed"])
        # 旧的基础线不再参与对照。
        self.assertEqual([row["step"] for row in result["diff"]], [])

    def test_mismatched_provided_baseline_is_ignored(self):
        result = retest(
            vuln(FIXED, baseline={"steps": [{"name": "别的步骤", "ok": False}]})
        )
        self.assertEqual(result["result"], "baseline")
        self.assertTrue(result["baseline"]["ignored_provided"])

    # —— 工单关闭与关闭后复现 ——

    def test_pass_closes_ticket_and_fail_keeps_it_open(self):
        opened = retest(vuln(BASE))
        self.assertEqual(opened["ticket"]["status"], "open")
        self.assertFalse(opened["closed"])

        passed = retest(vuln(FIXED))
        self.assertTrue(passed["closed"])
        self.assertEqual(passed["ticket"]["status"], "closed")
        self.assertEqual(passed["ticket"]["previous_status"], "open")
        self.assertTrue(passed["ticket"]["changed"])

    def test_fail_after_close_is_new_issue_linked_to_original(self):
        retest(vuln(BASE))
        retest(vuln(FIXED))
        result = retest(vuln(BASE, ticket="V-7"))
        self.assertEqual(result["result"], "fail")
        self.assertIsNotNone(result["recurrence"])
        self.assertEqual(result["recurrence"]["handled_as"], "new_issue")
        self.assertEqual(result["recurrence"]["original_ticket"], "V-7")
        self.assertEqual(result["recurrence"]["new_ticket"], "V-7-R1")
        self.assertEqual(result["ticket"]["id"], "V-7-R1")
        self.assertEqual(result["ticket"]["original"], "V-7")
        self.assertEqual(result["ticket"]["status"], "open")
        self.assertFalse(result["closed"])

    def test_recurrence_keeps_old_records_and_starts_new_baseline(self):
        retest(vuln(BASE, ticket="V-7"))
        retest(vuln(FIXED, ticket="V-7"))
        retest(vuln(BASE, ticket="V-7"))
        rows = history("V-7")["records"]
        self.assertEqual(len(rows), 3)
        # 历史结论保留修复前证据。
        self.assertEqual(rows[1]["diff"][0]["evidence_before"], "http://old/1")
        retried = retest(vuln(FIXED, ticket="V-7-R1"))
        self.assertEqual(retried["result"], "pass")
        self.assertEqual(retried["ticket"]["id"], "V-7-R1")

    def test_conflicting_ticket_is_rejected(self):
        retest(vuln(BASE, ticket="V-7"))
        with self.assertRaises(ValueError):
            retest(vuln(FIXED, ticket="OTHER"))

    # —— 历史与汇总 ——

    def test_history_is_ordered_by_time_and_filters_by_project(self):
        retest(vuln(BASE))
        retest(vuln(FIXED))
        retest({"id": "V-8", "project": "blog", "steps": [{"name": "a", "ok": True}]})

        shop = history("V-7")
        self.assertEqual(shop["count"], 2)
        self.assertEqual([row["round"] for row in shop["records"]], [1, 2])

        only_blog = history(project="blog")
        self.assertEqual(only_blog["count"], 1)
        self.assertEqual(only_blog["records"][0]["vuln"], "V-8")
        self.assertEqual(only_blog["projects"], ["blog", "shop"])

        summary = history()["by_project"]["shop"]
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["fail"], 1)
        self.assertEqual(summary["pass"], 1)
        self.assertEqual(summary["closed"], 1)

    def test_history_unknown_vuln_is_empty(self):
        empty = history("nope")
        self.assertEqual(empty["count"], 0)
        self.assertEqual(empty["records"], [])

    def test_history_rejects_blank_filters(self):
        with self.assertRaises(ValueError):
            history(vuln="")
        with self.assertRaises(ValueError):
            history(project="  ")


if __name__ == "__main__":
    unittest.main()
