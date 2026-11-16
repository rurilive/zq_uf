import unittest

from wafregress.cases import load_case_files
from wafregress.impact import (
    CHANGE_ADDED,
    CHANGE_MODIFIED,
    CHANGE_REMOVED,
    affected_cases,
    diff_rules,
    summarize_by_biz,
)
from wafregress.models import Rule
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "cases")


def rules_v1():
    return [
        Rule(id="sqli", pattern="union select"),
        Rule(id="xss", pattern="<script>"),
    ]


def rules_v2():
    # xss 放宽为单个尖括号（修改）；新增 login 作用域的注入规则；删除 sqli
    return [
        Rule(id="xss", pattern="<"),
        Rule(id="login_sqli", pattern="'--", scope="login"),
    ]


class DiffRulesTest(unittest.TestCase):
    def test_add_remove_modify(self):
        diff = diff_rules(rules_v1(), rules_v2())
        self.assertEqual(diff["added"], ["login_sqli"])
        self.assertEqual(diff["removed"], ["sqli"])
        self.assertEqual([item["rule"] for item in diff["modified"]], ["xss"])
        change = diff["modified"][0]
        self.assertEqual(change["before"]["pattern"], "<script>")
        self.assertEqual(change["after"]["pattern"], "<")

    def test_unchanged_rules_not_reported(self):
        diff = diff_rules(rules_v1(), rules_v1())
        self.assertEqual(diff, {"added": [], "removed": [], "modified": []})


class AffectedCasesTest(unittest.TestCase):
    def setUp(self):
        self.cases = load_case_files([DATA_DIR])

    def test_affected_set_and_changes(self):
        ids, by_rule, diff = affected_cases(self.cases, rules_v1(), rules_v2())
        # sqli 原本命中 s1（union select），删除后 s1 受影响
        # xss 改为 < 后，所有含 < 的 pay 用例 p1/p2/p3 受影响
        # login_sqli 新增，命中 l1
        self.assertEqual(set(ids), {"s1", "p1", "p2", "p3", "l1"})
        self.assertEqual(by_rule["sqli"]["change"], CHANGE_REMOVED)
        self.assertEqual(by_rule["xss"]["change"], CHANGE_MODIFIED)
        self.assertEqual(by_rule["login_sqli"]["change"], CHANGE_ADDED)
        self.assertEqual(by_rule["xss"]["affected_cases"], ["p1", "p2", "p3"])

    def test_affected_grouped_by_biz(self):
        ids, _, _ = affected_cases(self.cases, rules_v1(), rules_v2())
        counts = summarize_by_biz(self.cases, ids)
        self.assertEqual(counts, {"login": 1, "pay": 3, "search": 1})

    def test_biz_filter_limits_blast_radius(self):
        # 只看 pay 业务线：login 作用域的新规则与 login/search 的用例都不出现；
        # 全局规则（无 scope）仍在分析范围内，但没有命中 pay 用例时影响集为空。
        ids, by_rule, _ = affected_cases(
            self.cases, rules_v1(), rules_v2(), biz="pay"
        )
        self.assertEqual(set(ids), {"p1", "p2", "p3"})
        self.assertNotIn("login_sqli", by_rule)
        self.assertEqual(by_rule["xss"]["affected_cases"], ["p1", "p2", "p3"])
        self.assertEqual(by_rule["sqli"]["affected_cases"], [])

    def test_scope_change_is_a_modification(self):
        before = [Rule(id="xss", pattern="<")]
        after = [Rule(id="xss", pattern="<", scope="pay")]
        diff = diff_rules(before, after)
        self.assertEqual(len(diff["modified"]), 1)
        ids, by_rule, _ = affected_cases(self.cases, before, after)
        # 加了 pay 作用域后，search 下若有含 < 的用例也受影响（此处仅 pay 含尖括号）
        self.assertEqual(by_rule["xss"]["change"], CHANGE_MODIFIED)
        self.assertEqual(set(ids), {"p1", "p2", "p3"})


if __name__ == "__main__":
    unittest.main()
