import unittest
from datetime import datetime, timedelta, timezone

from vulnretest import history, reset, retest

T0 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def vuln(**overrides):
    data = {
        "id": "V-7",
        "project": "shop",
        "ticket": "SEC-7",
        "steps": [
            {"name": "提交恶意参数", "ok": True, "evidence": "after/submit.png"},
            {"name": "查看响应回显", "ok": True, "evidence": "after/echo.png"},
        ],
    }
    data.update(overrides)
    return data


def failing(**overrides):
    return vuln(
        steps=[
            {"name": "提交恶意参数", "ok": True, "evidence": "after/submit.png"},
            {
                "name": "查看响应回显",
                "ok": False,
                "reason": "响应仍回显脚本内容",
                "evidence": "after/echo.png",
            },
        ],
        **overrides,
    )


BEFORE = {
    "at": at(-60),
    "steps": [
        {"name": "提交恶意参数", "evidence": "before/submit.png"},
        {"name": "查看响应回显", "evidence": "before/echo.png"},
    ],
}


class RetestTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_pass_closes_ticket(self):
        result = retest(vuln(), now=at(0))
        self.assertEqual(result["result"], "pass")
        self.assertTrue(result["closed"])
        self.assertEqual(result["ticket_status"], "closed")
        self.assertEqual(result["failures"], [])

    def test_fail_keeps_open_with_reason_and_evidence(self):
        retest(failing(baseline=BEFORE), now=at(0))
        result = retest(failing(), now=at(1))
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["closed"])
        self.assertEqual(result["ticket_status"], "open")
        self.assertEqual(len(result["failures"]), 1)
        failure = result["failures"][0]
        self.assertEqual(failure["step"], "查看响应回显")
        self.assertEqual(failure["reason"], "响应仍回显脚本内容")
        self.assertEqual(failure["evidence"], ["after/echo.png"])
        self.assertEqual(failure["evidence_before"], ["before/echo.png"])

    def test_baseline_holds_pre_fix_evidence(self):
        first = retest(failing(baseline=BEFORE), now=at(0))
        self.assertEqual(first["baseline"]["step_names"], ["提交恶意参数", "查看响应回显"])
        self.assertEqual(first["evidence_before"], ["before/submit.png", "before/echo.png"])
        self.assertEqual(first["evidence"], ["after/submit.png", "after/echo.png"])

        second = retest(failing(), now=at(1))
        self.assertFalse(second["baseline_reset"])
        self.assertEqual(second["baseline"]["id"], first["baseline"]["id"])
        self.assertEqual(
            [step["evidence"] for step in second["baseline"]["steps"]],
            [["before/submit.png"], ["before/echo.png"]],
        )
        self.assertEqual(second["evidence_before"], ["before/submit.png", "before/echo.png"])

    def test_supplied_baseline_is_used_verbatim(self):
        result = retest(vuln(baseline=BEFORE), now=at(0))
        self.assertEqual(result["baseline"]["reason"], "provided")
        self.assertEqual(result["baseline"]["at"], at(-60).isoformat(timespec="microseconds"))
        self.assertEqual(result["evidence_before"], ["before/submit.png", "before/echo.png"])
        self.assertTrue(result["closed"])

    def test_supplied_baseline_with_different_steps_is_rebaselined(self):
        provided = {"steps": [{"name": "提交恶意参数", "evidence": "before/submit.png"}]}
        result = retest(vuln(baseline=provided), now=at(0))
        self.assertTrue(result["baseline_reset"])
        self.assertEqual(result["baseline"]["reason"], "steps_changed")
        self.assertEqual(result["baseline"]["step_names"], ["提交恶意参数", "查看响应回显"])
        # 同名步骤沿用旧基线的修复前证据；新步骤没有更早记录，用首次出现时的证据
        self.assertEqual(result["baseline"]["steps"][0]["evidence"], ["before/submit.png"])
        self.assertEqual(result["baseline"]["steps"][1]["evidence"], ["after/echo.png"])

    def test_new_failing_step_seeds_its_evidence(self):
        provided = {"steps": [{"name": "提交恶意参数", "evidence": "before/submit.png"}]}
        broken = vuln(
            baseline=provided,
            steps=[
                {"name": "提交恶意参数", "ok": True, "evidence": "after/submit.png"},
                {"name": "查看响应回显", "ok": False, "evidence": "after/echo.png"},
            ],
        )
        result = retest(broken, now=at(0))
        self.assertTrue(result["baseline_reset"])
        # 新步骤在本轮仍然失败，这份证据就是它修复前的样子
        self.assertEqual(result["baseline"]["steps"][1]["evidence"], ["after/echo.png"])
        self.assertFalse(result["closed"])
        self.assertEqual(result["failures"][0]["evidence_before"], ["after/echo.png"])

    def test_baseline_stable_across_rounds(self):
        first = retest(failing(baseline=BEFORE), now=at(0))
        second = retest(failing(), now=at(1))
        self.assertFalse(second["baseline_reset"])
        self.assertEqual(second["baseline"]["id"], first["baseline"]["id"])
        self.assertEqual(
            [step["evidence"] for step in second["baseline"]["steps"]],
            [["before/submit.png"], ["before/echo.png"]],
        )
        # 历史记录各自保留修复前证据，不随后续轮次变化
        self.assertEqual(
            [step["evidence"] for step in first["baseline"]["steps"]],
            [["before/submit.png"], ["before/echo.png"]],
        )
        self.assertEqual(first["evidence_before"], second["evidence_before"])

    def test_diff_against_previous_round(self):
        retest(vuln(baseline=BEFORE), now=at(0))
        first_fail = retest(failing(), now=at(1))
        changes = {item["step"]: item["change"] for item in first_fail["diff"]}
        self.assertEqual(changes, {"查看响应回显": "regressed"})
        self.assertEqual(first_fail["diff_summary"]["unchanged"], 1)
        self.assertTrue(first_fail["changed"])

        second_fail = retest(failing(), now=at(2))
        self.assertEqual(second_fail["diff"], [])
        self.assertFalse(second_fail["changed"])
        self.assertEqual(second_fail["diff_summary"]["unchanged"], 2)
        self.assertEqual(second_fail["previous_result"], "fail")

    def test_fixed_step_is_reported(self):
        retest(vuln(baseline=BEFORE), now=at(0))
        retest(failing(), now=at(1))
        fixed = retest(vuln(), now=at(2))
        changes = {item["step"]: item["change"] for item in fixed["diff"]}
        self.assertEqual(changes["查看响应回显"], "fixed")
        self.assertEqual(fixed["result"], "pass")
        self.assertTrue(fixed["closed"])

    def test_first_round_has_no_diff(self):
        result = retest(vuln(), now=at(0))
        self.assertEqual(result["diff"], [])
        self.assertEqual(result["diff_summary"], {
            "fixed": 0, "regressed": 0, "unchanged": 0,
            "new": 0, "removed": 0, "total": 0,
        })

    def test_step_change_regenerates_baseline(self):
        first = retest(vuln(baseline=BEFORE), now=at(0))
        changed = vuln(
            steps=[
                {"name": "提交恶意参数", "ok": True, "evidence": "after/submit.png"},
                {"name": "查看响应回显", "ok": True, "evidence": "after/echo.png"},
                {"name": "尝试批量读取", "ok": True, "evidence": "after/bulk.png"},
            ]
        )
        second = retest(changed, now=at(1))
        self.assertTrue(second["baseline_reset"])
        self.assertNotEqual(second["baseline"]["id"], first["baseline"]["id"])
        self.assertEqual(second["baseline"]["reason"], "steps_changed")
        self.assertIn("尝试批量读取", second["baseline"]["step_names"])
        self.assertIn("尝试批量读取", second["step_changes"]["added"])
        # 沿用下来的步骤仍保留更早的修复前证据
        self.assertEqual(second["baseline"]["steps"][0]["evidence"], ["before/submit.png"])

    def test_removed_step_is_reported_in_diff(self):
        retest(vuln(), now=at(0))
        shrunk = vuln(steps=[{"name": "提交恶意参数", "ok": True}])
        result = retest(shrunk, now=at(1))
        changes = {item["step"]: item["change"] for item in result["diff"]}
        self.assertEqual(changes["查看响应回显"], "removed")
        self.assertIn("查看响应回显", result["step_changes"]["removed"])
        self.assertTrue(result["baseline_reset"])

    def test_history_orders_rounds_by_time(self):
        retest(vuln(baseline=BEFORE), now=at(0))
        retest(failing(), now=at(1))
        retest(vuln(), now=at(2))
        records = history("V-7")["records"]
        self.assertEqual([row["round"] for row in records], [1, 2, 3])
        self.assertEqual([row["at"] for row in records], sorted(row["at"] for row in records))
        self.assertEqual(history("V-7")["count"], 3)

    def test_history_timestamps_increase_without_clock(self):
        retest(vuln())
        retest(failing())
        stamps = [row["at"] for row in history("V-7")["records"]]
        self.assertEqual(stamps, sorted(stamps))
        self.assertNotEqual(stamps[0], stamps[1])

    def test_history_keeps_evidence_per_round(self):
        retest(failing(baseline=BEFORE), now=at(0))
        retest(failing(), now=at(1))
        records = history("V-7")["records"]
        self.assertEqual(records[0]["evidence"], ["after/submit.png", "after/echo.png"])
        self.assertEqual(records[1]["evidence"], ["after/submit.png", "after/echo.png"])
        self.assertEqual(records[0]["evidence_before"], ["before/submit.png", "before/echo.png"])
        self.assertEqual(records[1]["evidence_before"], ["before/submit.png", "before/echo.png"])
        self.assertEqual(records[0]["at"], at(0).isoformat(timespec="microseconds"))

    def test_reappear_after_close_links_original_ticket(self):
        retest(vuln(baseline=BEFORE), now=at(0))
        again = retest(failing(), now=at(1))
        self.assertTrue(again["new_issue"])
        self.assertTrue(again["reappeared_after_close"])
        self.assertEqual(again["linked_ticket"], "SEC-7")
        self.assertEqual(again["previous_ticket"], "SEC-7")
        self.assertEqual(again["ticket"], "SEC-7-R2")
        self.assertEqual(again["ticket_status"], "open")
        self.assertFalse(again["closed"])
        self.assertEqual(again["baseline"]["reason"], "reappeared_after_close")
        self.assertEqual(again["issue"], 2)
        self.assertEqual(again["issue_round"], 1)
        changes = {item["step"]: item["change"] for item in again["diff"]}
        self.assertEqual(changes["查看响应回显"], "regressed")

        again2 = retest(vuln(), now=at(2))
        self.assertTrue(again2["closed"])
        self.assertEqual(again2["ticket"], "SEC-7-R2")
        self.assertFalse(again2["new_issue"])

    def test_new_issue_baseline_starts_empty(self):
        retest(vuln(baseline=BEFORE), now=at(0))
        again = retest(failing(), now=at(1))
        # 新问题重新建立基线，本轮失败证据成为新问题的起点
        self.assertTrue(again["baseline_reset"])
        self.assertEqual(again["baseline"]["issue"], 2)

    def test_fail_keeps_ticket_status_unchanged(self):
        retest(vuln(baseline=BEFORE), now=at(0))
        first_fail = retest(failing(), now=at(1))
        second_fail = retest(failing(), now=at(2))
        self.assertFalse(second_fail["new_issue"])
        self.assertEqual(second_fail["ticket"], first_fail["ticket"])
        self.assertEqual(second_fail["ticket_status"], "open")
        self.assertEqual(second_fail["issue"], first_fail["issue"])

    def test_history_filters_by_project(self):
        retest(vuln(), now=at(0))
        retest(vuln(id="V-8", ticket="SEC-8", project="blog"), now=at(1))
        shop = history(project="shop")
        self.assertEqual(shop["count"], 1)
        self.assertEqual([row["vuln"] for row in shop["records"]], ["V-7"])
        self.assertEqual([item["project"] for item in shop["projects"]], ["shop"])
        self.assertEqual(shop["projects"][0]["vulns"], ["V-7"])

    def test_history_summarises_projects(self):
        retest(vuln(), now=at(0))
        retest(vuln(id="V-8", ticket="SEC-8", steps=[{"name": "a", "ok": True}]), now=at(1))
        retest(failing(id="V-9", ticket="SEC-9"), now=at(2))
        projects = {item["project"]: item for item in history()["projects"]}
        self.assertEqual(projects["shop"]["closed"], 2)
        self.assertEqual(projects["shop"]["open"], 1)
        self.assertEqual(projects["shop"]["count"], 3)
        self.assertEqual(projects["shop"]["vulns"], ["V-7", "V-8", "V-9"])
        self.assertEqual(projects["shop"]["last_at"], at(2).isoformat(timespec="microseconds"))

    def test_history_exposes_latest_per_vuln(self):
        retest(vuln(baseline=BEFORE), now=at(0))
        retest(failing(), now=at(1))
        result = history("V-7")
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["closed"])
        self.assertEqual(result["ticket_status"], "open")
        self.assertEqual(result["latest"]["round"], 2)
        self.assertEqual(len(result["vulns"]), 1)
        self.assertEqual(result["vulns"][0]["ticket"], "SEC-7-R2")
        self.assertEqual(result["vulns"][0]["linked_ticket"], "SEC-7")
        self.assertEqual(result["vulns"][0]["rounds"], 2)

    def test_unknown_vuln_history_is_empty(self):
        result = history("V-404")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["records"], [])
        self.assertIsNone(result["closed"])
        self.assertIsNone(result["latest"])

    def test_returned_objects_do_not_mutate_history(self):
        result = retest(vuln(), now=at(0))
        result["result"] = "tampered"
        result["baseline"]["steps"][0]["evidence"].append("tampered")
        result["failures"].append({"step": "x"})
        stored = history("V-7")["records"][0]
        self.assertEqual(stored["result"], "pass")
        self.assertEqual(stored["baseline"]["steps"][0]["evidence"], ["after/submit.png"])
        self.assertEqual(stored["failures"], [])

    def test_second_reappearance_links_original_ticket(self):
        retest(vuln(baseline=BEFORE), now=at(0))
        first = retest(failing(), now=at(1))
        retest(vuln(), now=at(2))
        second = retest(failing(), now=at(3))
        self.assertEqual(first["ticket"], "SEC-7-R2")
        self.assertEqual(second["ticket"], "SEC-7-R3")
        self.assertEqual(second["linked_ticket"], "SEC-7")
        self.assertTrue(second["new_issue"])
        self.assertEqual(history("V-7")["vulns"][0]["rounds"], 4)

    def test_empty_steps_cannot_close(self):
        result = retest({"id": "V-10", "project": "shop", "steps": []}, now=at(0))
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["closed"])
        self.assertTrue(result["failures"])

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            retest("V-7")
        with self.assertRaises(ValueError):
            retest({"project": "shop"})
        with self.assertRaises(ValueError):
            retest({"id": "V-7", "steps": [{"name": 1, "ok": True}]})
        with self.assertRaises(ValueError):
            retest({"id": "", "steps": []})
        with self.assertRaises(ValueError):
            retest({"id": "V-7", "project": 1, "steps": []})
        with self.assertRaises(ValueError):
            retest({"id": "V-7", "steps": [{"name": "a", "ok": True}, {"name": "a", "ok": True}]})
        with self.assertRaises(ValueError):
            retest({"id": "V-7", "status": "resolved"})
        with self.assertRaises(ValueError):
            retest({"id": "V-7", "steps": "x"})
        with self.assertRaises(ValueError):
            history(123)

    def test_steps_without_names_are_accepted(self):
        first = retest({"id": "V-11", "project": "shop", "steps": [{"ok": False}, {"ok": False}]})
        self.assertEqual(first["step_names"], ["步骤1", "步骤2"])
        self.assertFalse(first["closed"])
        self.assertEqual(len(first["failures"]), 2)

        second = retest({"id": "V-11", "project": "shop", "steps": [{"ok": True}, {"ok": False}]})
        self.assertEqual(second["step_names"], ["步骤1", "步骤2"])
        self.assertFalse(second["baseline_reset"])
        self.assertEqual(second["failures"][0]["step"], "步骤2")

    def test_missing_steps_do_not_close_ticket(self):
        retest(vuln(), now=at(0))
        result = retest({"id": "V-7", "project": "shop"}, now=at(1))
        self.assertEqual(result["result"], "fail")
        self.assertFalse(result["closed"])
        self.assertTrue(result["failures"])


if __name__ == "__main__":
    unittest.main()
