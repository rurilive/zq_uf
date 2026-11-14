"""血缘继承与下游收紧限制的测试。"""

import unittest

from tabledesens import (DesensEngine, Lineage, MASK, HASH, REDACT, NONE,
                         PolicyLooseningError, PolicyError)


def build_engine(node, lineage, default=MASK, **kwargs):
    return DesensEngine(lineage, node=node, default_strategy=default, **kwargs)


class LineageInheritanceTest(unittest.TestCase):
    def setUp(self):
        # ods.user(phone=MASK) -> dwd.user -> ads.user_report
        self.lineage = Lineage(
            edges=[("ods.user", "dwd.user"),
                   ("dwd.user", "ads.user_report")],
            rules={"ods.user": {"phone": MASK}})

    def test_rule_inherits_downstream(self):
        engine = build_engine("ads.user_report", self.lineage)
        # 三代下游仍继承 MASK，不会因为下游没配规则就漏成明文
        result = engine.mask({"phone": "13800000000"})
        self.assertEqual(result.row["phone"], "138******00")
        self.assertIn("phone", result.tokens)

    def test_downstream_may_tighten(self):
        # dwd 把 phone 从 MASK 收紧到 HASH，允许
        self.lineage.add_rules("dwd.user", {"phone": HASH})
        engine = build_engine("ads.user_report", self.lineage)
        result = engine.mask({"phone": "13800000000"})
        self.assertTrue(result.row["phone"].startswith("h_"))
        # HASH 不可逆，不发放令牌
        self.assertNotIn("phone", result.tokens)

    def test_downstream_cannot_loosen(self):
        # dwd 收紧后，ads 想放松回 MASK —— 拒绝
        self.lineage.add_rules("dwd.user", {"phone": HASH})
        with self.assertRaises(PolicyLooseningError):
            self.lineage.add_rules("ads.user_report", {"phone": MASK})

    def test_downstream_cannot_reset_to_none(self):
        with self.assertRaises(PolicyLooseningError):
            self.lineage.add_rules("dwd.user", {"phone": NONE})

    def test_multi_parent_takes_strictest(self):
        # 两条血缘汇入同一节点，严格度取 max
        lineage = Lineage(
            edges=[("a.t", "m.t"), ("b.t", "m.t")],
            rules={"a.t": {"id": MASK}, "b.t": {"id": HASH}})
        engine = build_engine("m.t", lineage)
        result = engine.mask({"id": "abc123"})
        self.assertTrue(result.row["id"].startswith("h_"))

    def test_node_default_inherits_and_tightens(self):
        # 上游节点级默认 MASK，下游未列字段也要继承
        lineage = Lineage(
            edges=[("ods.t", "dwd.t")],
            rules={"ods.t": {}, "dwd.t": {}},
        )
        lineage.add_rules("ods.t", default=MASK)
        engine = build_engine("dwd.t", lineage)
        result = engine.mask({"new_field": "x"})
        self.assertNotEqual(result.row["new_field"], "x")
        # 节点默认属于显式配置，不算遗漏
        self.assertEqual(result.missed, [])

    def test_node_default_via_constructor(self):
        # 规则表里的 None 键即节点级默认
        lineage = Lineage(rules={"ods.t": {None: MASK}})
        engine = build_engine("ods.t", lineage)
        result = engine.mask({"any_field": "abcdef"})
        self.assertNotEqual(result.row["any_field"], "abcdef")
        self.assertEqual(result.missed, [])

    def test_lineage_cycle_rejected(self):
        lineage = Lineage(edges=[("a", "b"), ("b", "c")])
        lineage.add_edge("c", "a")
        with self.assertRaises(PolicyError):
            lineage.effective_rules("a")

    def test_redact_is_strictest(self):
        self.lineage.add_rules("dwd.user", {"phone": REDACT})
        engine = build_engine("ads.user_report", self.lineage)
        result = engine.mask({"phone": "13800000000"})
        self.assertIsNone(result.row["phone"])
        self.assertNotIn("phone", result.tokens)


if __name__ == "__main__":
    unittest.main()
