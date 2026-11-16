import unittest

from wafregress.errors import WafValidationError
from wafregress.models import (
    OUTCOME_BLOCKED,
    OUTCOME_FALSE_POSITIVE,
    OUTCOME_MISSED,
    OUTCOME_PASSED,
    Case,
    Rule,
    evaluate,
    match_rules,
)


def make_case(expect="block", biz="pay", request="/pay?note=x", case_id="t1",
              expect_rule=None, scenario="note"):
    return Case(
        id=case_id, biz=biz, request=request, expect=expect,
        scenario=scenario, expect_rule=expect_rule,
    )


class RuleTest(unittest.TestCase):
    def test_from_dict_and_scope(self):
        rule = Rule.from_dict({"id": "x", "pattern": "<"})
        self.assertEqual(rule.scope, None)
        self.assertTrue(rule.applies_to("pay"))
        self.assertTrue(rule.applies_to(None))

        scoped = Rule.from_dict({"id": "y", "pattern": "<", "scope": "pay"})
        self.assertTrue(scoped.applies_to("pay"))
        self.assertFalse(scoped.applies_to("search"))

    def test_invalid_rules(self):
        for bad in ({}, {"id": "x"}, {"id": "x", "pattern": 1}, "not-a-dict"):
            with self.assertRaises(WafValidationError):
                Rule.from_dict(bad)


class CaseTest(unittest.TestCase):
    def test_defaults_and_validation(self):
        case = Case.from_dict(
            {"id": "c", "biz": "pay", "request": "/pay", "expect": "pass"}
        )
        self.assertEqual(case.scenario, "default")
        self.assertIsNone(case.expect_rule)

        for bad in (
            {"id": "", "biz": "pay", "request": "/x", "expect": "block"},
            {"id": "c", "biz": "pay", "request": "/x", "expect": "maybe"},
            {"id": "c", "biz": "pay", "request": 3, "expect": "block"},
        ):
            with self.assertRaises(WafValidationError):
                Case.from_dict(bad)


class EvaluateTest(unittest.TestCase):
    RULES = [
        Rule(id="r1", pattern="union select"),
        Rule(id="r2", pattern="<script>"),
        Rule(id="r3", pattern="<", scope="pay"),
    ]

    def test_attack_blocked_is_correct(self):
        case = make_case(request="/search?q=union select 1", biz="search")
        result = evaluate(case, self.RULES)
        self.assertEqual(result.outcome, OUTCOME_BLOCKED)
        self.assertEqual(result.blocking_rule, "r1")
        self.assertEqual(result.matched_rules, ["r1"])
        self.assertTrue(result.is_correct)

    def test_business_blocked_is_false_positive(self):
        case = make_case(expect="pass", request="/pay?nickname=<coolkid>")
        result = evaluate(case, self.RULES)
        self.assertEqual(result.outcome, OUTCOME_FALSE_POSITIVE)
        self.assertEqual(result.blocking_rule, "r3")
        self.assertFalse(result.is_correct)

    def test_attack_not_blocked_is_missed_with_request(self):
        case = make_case(request="/login?user=admin'--", biz="login")
        result = evaluate(case, self.RULES)
        self.assertEqual(result.outcome, OUTCOME_MISSED)
        self.assertIsNone(result.blocking_rule)
        # 漏拦必须保留触发请求，便于直接复现
        self.assertEqual(result.request, "/login?user=admin'--")
        self.assertFalse(result.is_correct)

    def test_business_passed_is_correct(self):
        case = make_case(expect="pass", request="/pay?note=thanks")
        result = evaluate(case, self.RULES)
        self.assertEqual(result.outcome, OUTCOME_PASSED)
        self.assertTrue(result.is_correct)

    def test_scope_isolates_business_lines(self):
        # r3 只作用于 pay，search 下的尖括号不应被它拦
        case = make_case(biz="search", expect="pass",
                         request="/search?q=<coolkid>", case_id="s9")
        self.assertEqual(match_rules(case, self.RULES), [])
        self.assertEqual(evaluate(case, self.RULES).outcome, OUTCOME_PASSED)

    def test_first_matching_rule_blocks_but_all_hits_kept(self):
        # 规则按传入顺序匹配，最先命中的作为拦截规则，全部命中都保留留痕
        rules = [
            Rule(id="r1", pattern="union select"),
            Rule(id="r2", pattern="<script>"),
            Rule(id="r3", pattern="<", scope="pay"),
        ]
        case = make_case(request="/pay?note=<script>union select x")
        result = evaluate(case, rules)
        self.assertEqual(result.blocking_rule, "r1")
        self.assertEqual(result.matched_rules, ["r1", "r2", "r3"])


if __name__ == "__main__":
    unittest.main()
