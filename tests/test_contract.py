import unittest

from contractdiff import check, RuleBook, Rule, History, evaluate_gate, enable_if_passed
from contractdiff.rules import Severity
from contractdiff.schema import MISSING


def paths(findings, rule_id=None, severity=None):
    return sorted(
        f["path"] for f in findings
        if (rule_id is None or f["rule"] == rule_id)
        and (severity is None or f["severity"] == severity))


def by_path(result, path):
    return [f for f in result["findings"] if f["path"] == path]


class FieldRemovalTest(unittest.TestCase):
    def test_missing_field_breaks_both_directions(self):
        result = check({"fields": {"a": "int"}}, {"fields": {}})
        self.assertFalse(result["compatible"])
        self.assertFalse(result["read_compatible"])
        self.assertFalse(result["write_compatible"])
        breaking = result["breaking"]
        self.assertEqual([f["path"] for f in breaking], ["a"])
        self.assertEqual(breaking[0]["direction"], "both")
        self.assertEqual(breaking[0]["rule"], "field.removed")

    def test_deleted_nested_field_carries_full_path(self):
        old = {"fields": {"user": {"type": "record", "fields": {"name": "text"}}}}
        new = {"fields": {"user": {"type": "record", "fields": {}}}}
        result = check(old, new)
        self.assertEqual(paths(result["breaking"]), ["user.name"])


class TypeChangeTest(unittest.TestCase):
    def test_narrowing_breaks_read_only(self):
        # int -> short：读取方按 int 解析，短整数装不下旧数据
        result = check({"fields": {"a": "int"}},
                       {"fields": {"a": "short"}})
        self.assertFalse(result["read_compatible"])
        self.assertTrue(result["write_compatible"])
        self.assertFalse(result["compatible"])
        f = by_path(result, "a")[0]
        self.assertEqual(f["rule"], "type.changed")
        self.assertEqual(f["direction"], "read")
        self.assertEqual(f["severity"], "breaking")
        self.assertEqual((f["old"], f["new"]), ("int", "short"))

    def test_widening_breaks_write_only(self):
        # int -> long：按旧类型 int 写入的列存不下放宽后的值
        result = check({"fields": {"a": "int"}},
                       {"fields": {"a": "long"}})
        self.assertTrue(result["read_compatible"])
        self.assertFalse(result["write_compatible"])
        f = by_path(result, "a")[0]
        self.assertEqual(f["direction"], "write")
        self.assertEqual(f["severity"], "breaking")

    def test_type_family_change_breaks_both(self):
        result = check({"fields": {"a": "int"}},
                       {"fields": {"a": "text"}})
        self.assertFalse(result["read_compatible"])
        self.assertFalse(result["write_compatible"])
        self.assertEqual(by_path(result, "a")[0]["direction"], "both")

    def test_unchanged_type_is_compatible(self):
        result = check({"fields": {"a": "int"}},
                       {"fields": {"a": "int"}})
        self.assertTrue(result["compatible"])
        self.assertEqual(result["findings"], [])


class DefaultValueTest(unittest.TestCase):
    def test_default_none_to_zero_marked_separately(self):
        # 默认值从空（无默认）变成 0：不判破坏，单独 info 标记
        old = {"fields": {"a": {"type": "int"}}}
        new = {"fields": {"a": {"type": "int", "default": 0}}}
        result = check(old, new)
        self.assertTrue(result["compatible"])
        marked = [f for f in result["info"] if f["rule"] == "default.changed"]
        self.assertEqual(len(marked), 1)
        self.assertEqual(marked[0]["path"], "a")
        self.assertEqual((marked[0]["old"], marked[0]["new"]), (None, 0))
        self.assertEqual(marked[0]["direction"], "both")

    def test_default_value_change_values(self):
        old = {"fields": {"a": {"type": "int", "default": 1}}}
        new = {"fields": {"a": {"type": "int", "default": 2}}}
        result = check(old, new)
        f = [x for x in result["info"] if x["rule"] == "default.changed"][0]
        self.assertEqual((f["old"], f["new"]), (1, 2))


class EnumTest(unittest.TestCase):
    def test_enum_removal_breaks_read(self):
        old = {"fields": {"color": {"type": "text", "enum": ["r", "g", "b"]}}}
        new = {"fields": {"color": {"type": "text", "enum": ["r", "g"]}}}
        result = check(old, new)
        breaking = [f for f in result["breaking"] if f["rule"] == "enum.changed"]
        self.assertEqual(len(breaking), 1)
        self.assertEqual(breaking[0]["direction"], "read")
        self.assertEqual(breaking[0]["enum_removed"], ["b"])

    def test_enum_addition_warns_write(self):
        old = {"fields": {"color": {"type": "text", "enum": ["r", "g"]}}}
        new = {"fields": {"color": {"type": "text", "enum": ["r", "g", "b"]}}}
        result = check(old, new)
        self.assertTrue(result["compatible"])  # 警告不算破坏
        warns = [f for f in result["warnings"] if f["rule"] == "enum.changed"]
        self.assertEqual(warns[0]["direction"], "write")
        self.assertEqual(warns[0]["enum_added"], ["b"])

    def test_enum_toggle_is_info(self):
        old = {"fields": {"c": "text"}}
        new = {"fields": {"c": {"type": "text", "enum": ["x"]}}}
        result = check(old, new)
        self.assertTrue(result["compatible"])
        self.assertEqual(paths(result["info"], "enum.changed"), ["c"])


class RequiredTest(unittest.TestCase):
    def test_optional_to_required_without_default_breaks_write(self):
        old = {"fields": {"a": {"type": "int"}}}
        new = {"fields": {"a": {"type": "int", "required": True}}}
        result = check(old, new)
        self.assertFalse(result["write_compatible"])
        self.assertTrue(result["read_compatible"])
        self.assertEqual(paths(result["breaking"], "required.changed"), ["a"])

    def test_optional_to_required_with_default_is_info(self):
        old = {"fields": {"a": {"type": "int"}}}
        new = {"fields": {"a": {"type": "int", "required": True, "default": 0}}}
        result = check(old, new)
        self.assertTrue(result["compatible"])
        self.assertEqual(paths(result["info"], "required.changed"), ["a"])


class AddedFieldTest(unittest.TestCase):
    def test_optional_added_field_warns_write_compatible_read(self):
        result = check({"fields": {}},
                       {"fields": {"a": {"type": "int"}}})
        self.assertTrue(result["compatible"])
        self.assertEqual(paths(result["warnings"], "field.added"), ["a"])
        compat = [f for f in result["compatible_items"] if f["path"] == "a"]
        self.assertEqual(compat[0]["direction"], "read")

    def test_required_added_field_without_default_breaks_write(self):
        result = check({"fields": {}},
                       {"fields": {"a": {"type": "int", "required": True}}})
        self.assertFalse(result["write_compatible"])
        self.assertTrue(result["read_compatible"])
        self.assertEqual(paths(result["breaking"], "field.added"), ["a"])

    def test_extra_column_is_surfaced_not_silent(self):
        # 写入方多写一列要有人提醒：对应新契约多出的字段必须出现在结论里
        result = check({"fields": {"id": "int"}},
                       {"fields": {"id": "int", "tag": "text"}})
        self.assertIn("tag", paths(result["findings"]))


class ShorthandAndResultShapeTest(unittest.TestCase):
    def test_string_shorthand_still_works(self):
        result = check({"fields": {"a": "int"}}, {"fields": {"a": "long"}})
        self.assertFalse(result["compatible"])

    def test_empty_contracts(self):
        result = check({}, {})
        self.assertTrue(result["compatible"])
        self.assertEqual(result["findings"], [])

    def test_suggestions_carry_direction_and_path(self):
        result = check({"fields": {"a": "int"}}, {"fields": {"a": "short"}})
        suggestions = result["suggestions"]
        self.assertTrue(suggestions)
        s = suggestions[0]
        self.assertEqual(s["path"], "a")
        self.assertEqual(s["direction"], "read")
        self.assertTrue(s["suggestion"])


class HistoryTest(unittest.TestCase):
    def _history(self):
        h = History()
        h.add("v1", {"fields": {"id": "int", "name": "text"}})
        h.add("v2", {"fields": {"id": "int"}})                      # 删列
        h.add("v3", {"fields": {"id": "short"}})                    # 收紧
        return h

    def test_versions_are_reviewable_and_immutable(self):
        h = self._history()
        self.assertEqual(h.versions(), ["v1", "v2", "v3"])
        snapshot = h.get("v1")
        snapshot["fields"]["id"] = "hacked"
        self.assertEqual(h.get("v1")["fields"]["id"]["type"], "int")

    def test_diff_arbitrary_versions(self):
        h = self._history()
        result = h.diff("v1", "v3")
        self.assertIn("name", paths(result["breaking"], "field.removed"))
        self.assertIn("id", paths(result["breaking"], "type.changed"))

    def test_scan_consecutive_versions(self):
        h = self._history()
        scan = h.scan()
        self.assertEqual(len(scan["results"]), 2)
        self.assertFalse(scan["all_compatible"])
        self.assertEqual(
            [(r["from_version"], r["to_version"]) for r in scan["results"]],
            [("v1", "v2"), ("v2", "v3")])
        self.assertFalse(scan["results"][0]["result"]["compatible"])
        self.assertFalse(scan["results"][1]["result"]["compatible"])

    def test_duplicate_version_rejected(self):
        h = History().add("v1", {})
        with self.assertRaises(ValueError):
            h.add("v1", {})

    def test_double_normalization_keeps_fields_and_defaults(self):
        # 回归：History 规范化后 check 会再规范化一次，
        # MISSING 哨兵不得被误当成字段 dict 导致字段丢失
        h = History()
        h.add("v1", {"fields": {"b": {"type": "int", "default": 0}}})
        h.add("v2", {"fields": {"b": {"type": "int", "default": 9}}})
        self.assertEqual(list(h.get("v1")["fields"].keys()), ["b"])
        result = h.diff("v1", "v2")
        self.assertEqual(paths(result["info"], "default.changed"), ["b"])


def _shadow_rule(rule, path, old, new, ctx):
    """候选新规则：默认值变化升级为 warning（影子模式，不参与正式判定）。"""
    if old is None or new is None:
        return None
    from contractdiff.schema import render_value
    if old["default"] is MISSING and new["default"] is MISSING:
        return None
    if old["default"] == new["default"]:
        return None
    return {
        "rule": rule.id,
        "direction": "both",
        "severity": Severity.WARNING,
        "path": path,
        "message": "影子规则：默认值变化需人工复核",
        "suggestion": "人工确认默认值变更影响",
        "old": render_value(old["default"]),
        "new": render_value(new["default"]),
    }


class GateTest(unittest.TestCase):
    def _history(self):
        h = History()
        h.add("v1", {"fields": {"a": {"type": "int"}},
                     "b": {"type": "int", "default": 0}})
        h.add("v2", {"fields": {"a": {"type": "int", "default": 0}},  # a 默认值变化
                     "b": {"type": "int", "default": 0}})
        h.add("v3", {"fields": {"a": {"type": "int", "default": 0}},  # 无默认值变化
                     "b": {"type": "int", "default": 0}})
        return h

    def test_shadow_rule_excluded_from_normal_check(self):
        book = RuleBook()
        book.register(Rule("default.review", "默认值人工复核", False, _shadow_rule))
        self.assertFalse(book.is_enabled("default.review"))
        result = book.check(
            {"fields": {"a": {"type": "int"}}},
            {"fields": {"a": {"type": "int", "default": 0}}})
        self.assertNotIn("default.review", {f["rule"] for f in result["findings"]})

    def test_gate_passes_then_enables(self):
        book = RuleBook()
        book.register(Rule("default.review", "默认值人工复核", False, _shadow_rule))
        h = self._history()
        gate = evaluate_gate(book, "default.review", h, [
            {"from_version": "v1", "to_version": "v2", "expected": ["a"]},
            {"from_version": "v2", "to_version": "v3", "expected": "none"},
        ])
        self.assertTrue(gate["passed"])
        self.assertTrue(enable_if_passed(book, "default.review", gate))
        self.assertTrue(book.is_enabled("default.review"))
        # 启用后正式 check 能看到该规则结论
        result = book.check(
            {"fields": {"a": {"type": "int"}}},
            {"fields": {"a": {"type": "int", "default": 0}}})
        self.assertIn("default.review", {f["rule"] for f in result["findings"]})

    def test_gate_mismatch_blocks_enable(self):
        book = RuleBook()
        book.register(Rule("default.review", "默认值人工复核", False, _shadow_rule))
        h = self._history()
        # 人工判断错了：v1->v2 实际命中 a，却声称无命中
        gate = evaluate_gate(book, "default.review", h, [
            {"from_version": "v1", "to_version": "v2", "expected": "none"},
        ])
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["mismatches"][0]["actual"], ["a"])
        self.assertFalse(enable_if_passed(book, "default.review", gate))
        self.assertFalse(book.is_enabled("default.review"))

    def test_gate_rejects_already_enabled_rule(self):
        book = RuleBook()
        h = self._history()
        with self.assertRaises(ValueError):
            evaluate_gate(book, "default.changed", h, [])


if __name__ == "__main__":
    unittest.main()
