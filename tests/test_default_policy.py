"""默认策略：未配置字段必须兜底处理，绝不允许明文漏出。"""

import unittest

from tabledesens import (DesensEngine, Lineage, MASK, HASH, REDACT, NONE)


class DefaultPolicyTest(unittest.TestCase):
    def test_unconfigured_field_uses_default_mask(self):
        lineage = Lineage(rules={"dwd.t": {"phone": MASK}})
        engine = DesensEngine(lineage, node="dwd.t",
                              default_strategy=MASK)
        result = engine.mask({"phone": "13800000000",
                              "new_secret": "abcdef"})
        # 已配置字段正常掩码
        self.assertEqual(result.row["phone"], "138******00")
        # 未配置字段走默认策略，同样被掩码，不会原样落盘
        self.assertNotEqual(result.row["new_secret"], "abcdef")
        # 遗漏字段被显式上报，供治理侧补规则
        self.assertEqual(result.missed, ["new_secret"])
        # 默认 MASK 仍然可逆，令牌照常发放
        self.assertIn("new_secret", result.tokens)

    def test_default_hash_leaves_no_plaintext(self):
        lineage = Lineage()
        engine = DesensEngine(lineage, node="unknown.node",
                              default_strategy=HASH)
        result = engine.mask({"anything": "v"})
        self.assertTrue(result.row["anything"].startswith("h_"))
        self.assertEqual(result.missed, ["anything"])
        # HASH 兜底不发令牌，杜绝绕过治理的还原通道
        self.assertEqual(result.tokens, {})

    def test_default_redact_destroys_value(self):
        engine = DesensEngine(Lineage(), node="n",
                              default_strategy=REDACT)
        result = engine.mask({"x": "secret"})
        self.assertIsNone(result.row["x"])
        self.assertEqual(result.missed, ["x"])

    def test_none_default_still_reports_missed(self):
        # 即使全局默认是 NONE（明文），遗漏字段也必须上报，不能静默
        engine = DesensEngine(Lineage(), node="n",
                              default_strategy=NONE)
        result = engine.mask({"x": "v"})
        self.assertEqual(result.row["x"], "v")
        self.assertEqual(result.missed, ["x"])

    def test_no_plaintext_leak_under_strict_default(self):
        # 宽行：所有字段都没配规则，逐字段断言没有一个原样输出
        engine = DesensEngine(Lineage(), node="wide.t",
                              default_strategy=MASK)
        row = {"a": "1", "bb": "22", "ccc": "333",
               "long_field": "sensitive-value"}
        result = engine.mask(row)
        for field, value in row.items():
            self.assertNotEqual(result.row[field], value,
                                "field %s leaked plaintext" % field)
        self.assertEqual(sorted(result.missed), sorted(row.keys()))

    def test_configured_field_not_marked_missed(self):
        lineage = Lineage(rules={"n": {"phone": MASK}})
        engine = DesensEngine(lineage, node="n", default_strategy=MASK)
        result = engine.mask({"phone": "13800000000"})
        self.assertEqual(result.missed, [])

    def test_mask_result_outputs_token_audit_missed(self):
        engine = DesensEngine(Lineage(), node="n", default_strategy=MASK)
        result = engine.mask({"phone": "13800000000"}, operator="etl")
        self.assertIsInstance(result.row, dict)
        self.assertIn("phone", result.tokens)
        self.assertEqual(result.audit["action"], "mask")
        self.assertEqual(result.audit["operator"], "etl")
        self.assertEqual(result.missed, ["phone"])

    def test_input_row_not_mutated(self):
        engine = DesensEngine(Lineage(rules={"n": {"phone": MASK}}),
                              node="n")
        row = {"phone": "13800000000"}
        engine.mask(row)
        self.assertEqual(row["phone"], "13800000000")


if __name__ == "__main__":
    unittest.main()
