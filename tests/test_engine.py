import os
import unittest

from wafregress import Engine, compare, impact, load_cases, regress, report, reset
from wafregress.cases import load_case_files
from wafregress.errors import (
    DuplicateRuleId,
    NoBaselineRun,
    RunNotFound,
    WafValidationError,
)
from wafregress.models import OUTCOME_FALSE_POSITIVE, OUTCOME_MISSED
from wafregress.store import MemoryStore

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "cases")

RULES_V1 = [
    {"id": "sqli", "pattern": "union select"},
    {"id": "xss", "pattern": "<script>"},
]
RULES_V2 = [
    {"id": "sqli", "pattern": "union select"},
    {"id": "xss", "pattern": "<"},
    {"id": "login_sqli", "pattern": "'--", "scope": "login"},
]


def new_engine():
    return Engine(store=MemoryStore(), cases=load_case_files([DATA_DIR]))


class RegressBasicTest(unittest.TestCase):
    def setUp(self):
        self.engine = new_engine()

    def test_four_outcomes_both_sides_counted(self):
        out = self.engine.regress(RULES_V1)
        # 7 条用例：s1/p1/l1 攻击 3 条，业务 4 条
        self.assertEqual(out["cases"], 7)
        self.assertEqual(out["blocked"], 2)          # s1、p1
        self.assertEqual(out["missed"], 1)           # l1 无规则拦
        self.assertEqual(out["false_positive"], 0)   # v1 尖括号规则还很精确
        self.assertEqual(out["passed"], 4)

    def test_missed_keeps_trigger_request(self):
        self.engine.regress(RULES_V1)
        missed = self.engine.report()["missed"]
        self.assertEqual([item["case"] for item in missed], ["l1"])
        self.assertEqual(missed[0]["request"], "/login?user=admin'--")
        self.assertEqual(missed[0]["expect_rule"], "sqli")

    def test_business_block_is_false_positive_not_plain_hit(self):
        out = self.engine.regress(RULES_V2)
        self.assertEqual(out["false_positive"], 2)  # p2、p3 含 <
        rep = self.engine.report()
        fp_cases = {
            item["case"]
            for group in rep["false_positives"]["by_scenario"].values()
            for items in group.values()
            for item in items
        }
        self.assertEqual(fp_cases, {"p2", "p3"})
        # 误拦材料可直接复现：请求 + 拦截规则都在
        detail = rep["false_positives"]["by_scenario"]["pay"]["note"][0]
        self.assertIn("request", detail)
        self.assertEqual(detail["blocking_rule"], "xss")

    def test_input_validation(self):
        with self.assertRaises(WafValidationError):
            self.engine.regress("not-a-list")
        with self.assertRaises(DuplicateRuleId):
            self.engine.regress(
                [{"id": "dup", "pattern": "a"}, {"id": "dup", "pattern": "b"}]
            )


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.engine = new_engine()

    def test_report_before_run_is_empty(self):
        empty = self.engine.report()
        self.assertIsNone(empty["run_id"])
        self.assertEqual(empty["summary"]["total"], 0)
        self.assertEqual(empty["false_positives"], {"total": 0, "by_scenario": {}})
        self.assertEqual(empty["by_rule"], {})

    def test_false_positives_grouped_by_biz_and_scenario(self):
        self.engine.regress(RULES_V2)
        grouped = self.engine.report()["false_positives"]["by_scenario"]
        self.assertEqual(set(grouped), {"pay"})
        self.assertEqual(set(grouped["pay"]), {"note", "nickname"})
        self.assertEqual(
            [item["case"] for item in grouped["pay"]["nickname"]], ["p3"]
        )

    def test_counts_per_rule(self):
        self.engine.regress(RULES_V2)
        by_rule = self.engine.report()["by_rule"]
        # xss 拦了攻击 p1（blocked=1）+ 误拦 p2/p3（fp=2）
        self.assertEqual(by_rule["xss"]["blocked"], 1)
        self.assertEqual(by_rule["xss"]["false_positive"], 2)
        # login_sqli 正确拦了 l1
        self.assertEqual(by_rule["login_sqli"]["blocked"], 1)
        # 漏拦按 expect_rule 归因
        self.engine.reset()
        self.engine.regress(RULES_V1)
        by_rule = self.engine.report()["by_rule"]
        self.assertEqual(by_rule["sqli"]["missed"], 1)

    def test_unattributed_missed_separated(self):
        # 不声明 expect_rule 的攻击用例漏拦，不凭空算到某条规则头上
        engine = Engine(store=MemoryStore())
        engine.cases[0]  # builtin cases 存在即可
        from wafregress.models import Case

        engine.cases = [
            Case(id="z1", biz="pay", request="/pay?x=evil", expect="block")
        ]
        engine.regress([{"id": "r1", "pattern": "nomatch"}])
        rep = engine.report()
        self.assertEqual(rep["unattributed_missed"], ["z1"])
        self.assertEqual(rep["summary"]["missed"], 1)

    def test_by_biz_view(self):
        self.engine.regress(RULES_V2)
        by_biz = self.engine.report()["by_biz"]
        self.assertEqual(by_biz["pay"]["false_positive"], 2)
        self.assertEqual(by_biz["pay"]["false_positive_cases"], ["p2", "p3"])
        self.assertEqual(by_biz["login"]["blocked"], 1)
        self.assertEqual(by_biz["search"]["passed"], 1)

    def test_report_specific_run_and_unknown_run(self):
        self.engine.regress(RULES_V1)
        self.engine.regress(RULES_V2)
        first = self.engine.report(run_id=1)
        self.assertEqual(first["summary"]["false_positive"], 0)
        with self.assertRaises(RunNotFound):
            self.engine.report(run_id=99)


class ImpactAndAffectedRunTest(unittest.TestCase):
    def setUp(self):
        self.engine = new_engine()

    def test_impact_before_any_run_requires_baseline(self):
        with self.assertRaises(NoBaselineRun):
            self.engine.impact(RULES_V1)

    def test_impact_preview_before_execution(self):
        self.engine.regress(RULES_V1)
        preview = self.engine.impact(RULES_V2)
        changes = preview["rule_changes"]
        self.assertEqual(changes["added"], ["login_sqli"])
        self.assertEqual([item["rule"] for item in changes["modified"]], ["xss"])
        # 执行前就能看到受影响范围和业务线分布
        self.assertEqual(set(preview["affected_cases"]),
                         {"p1", "p2", "p3", "l1"})
        self.assertEqual(preview["by_biz"], {"login": 1, "pay": 3})

    def test_only_affected_runs_subset(self):
        self.engine.regress(RULES_V1)
        out = self.engine.regress(RULES_V2, only_affected=True)
        # 只跑受影响的 4 条，不受影响的 s2/l2 不在本轮
        self.assertEqual(out["cases"], 4)
        self.assertEqual(out["impact"]["affected_count"], 4)
        ids = {r["case_id"] for r in self.engine.report()["by_biz"] and
               [cr for cr in self._all_results()]}
        self.assertEqual(ids, {"p1", "p2", "p3", "l1"})

    def _all_results(self):
        # 通过各业务线汇总间接校验范围，结果详情用 report 的分组查看
        return self.engine.store.latest()["results"]

    def test_only_affected_without_baseline_raises(self):
        with self.assertRaises(NoBaselineRun):
            self.engine.regress(RULES_V1, only_affected=True)

    def test_biz_filter_isolates_business_line(self):
        self.engine.regress(RULES_V1)
        out = self.engine.regress(RULES_V2, biz="pay")
        self.assertEqual(out["biz"], "pay")
        self.assertEqual(out["cases"], 3)
        by_biz = self.engine.report()["by_biz"]
        self.assertEqual(set(by_biz), {"pay"})


class CompareTest(unittest.TestCase):
    def setUp(self):
        self.engine = new_engine()

    def test_compare_before_after_rule_change(self):
        self.engine.regress(RULES_V1)
        self.engine.regress(RULES_V2)
        diff = self.engine.compare()
        # 规则改动前后对比：新增 2 个误拦，消除 1 个漏拦
        self.assertEqual(
            sorted(item["case"] for item in diff["new_false_positives"]),
            ["p2", "p3"],
        )
        self.assertEqual(diff["resolved_missed"], ["l1"])
        self.assertEqual(diff["summary_delta"]["false_positive"], 2)
        self.assertEqual(diff["summary_delta"]["missed"], -1)
        self.assertEqual(diff["rule_changes"]["added"], ["login_sqli"])

    def test_resolved_false_positive_when_rule_tightened(self):
        broad = [{"id": "xss", "pattern": "<"}]
        tight = [{"id": "xss", "pattern": "<script>"}]
        self.engine.regress(broad)
        self.engine.regress(tight)
        diff = self.engine.compare()
        self.assertIn("p2", diff["resolved_false_positives"])
        self.assertIn("p3", diff["resolved_false_positives"])
        self.assertEqual(diff["new_missed"], [])

    def test_compare_needs_two_runs(self):
        with self.assertRaises(NoBaselineRun):
            self.engine.compare()

    def test_transition_carries_request_for_replay(self):
        self.engine.regress(RULES_V1)
        self.engine.regress(RULES_V2)
        diff = self.engine.compare()
        l1 = next(item for item in diff["transitions"] if item["case"] == "l1")
        self.assertEqual(l1["before"], OUTCOME_MISSED)
        self.assertEqual(l1["after"], "blocked")
        self.assertEqual(l1["request"], "/login?user=admin'--")


class ModuleLevelApiTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_default_engine_flow(self):
        out = regress(RULES_V1)
        self.assertEqual(out["rules"], 2)
        self.assertIn("false_positives", report())
        self.assertIn("false_positive", report()["summary"])

    def test_module_level_impact_and_compare(self):
        regress(RULES_V1)
        preview = impact(RULES_V2)
        self.assertIn("xss", preview["by_rule"])
        regress(RULES_V2)
        diff = compare()
        self.assertEqual(diff["base_run_id"], 1)
        self.assertEqual(diff["target_run_id"], 2)


if __name__ == "__main__":
    unittest.main()
