import json
import unittest

from etlconf import PlanError, compile_plan, transform


class EtlconfTest(unittest.TestCase):
    def test_rename(self):
        rows = [{"old": 1}]
        self.assertEqual(transform(rows, {"rename": {"old": "new"}}), [{"new": 1}])

    def test_unmapped_kept(self):
        rows = [{"a": 1, "b": 2}]
        self.assertEqual(transform(rows, {"rename": {"a": "x"}}), [{"x": 1, "b": 2}])

    def test_empty(self):
        self.assertEqual(transform([], {"rename": {}}), [])


class StepsTest(unittest.TestCase):
    def test_compose_steps(self):
        plan = {
            "strip": ["name"],
            "cast": {"qty": "int", "price": "float"},
            "fill": {"qty": 0},
            "rename": {"name": "label"},
            "derive": {"amount": "qty * price"},
        }
        rows = [{"name": "  apple  ", "qty": "", "price": "2.5"}]
        self.assertEqual(
            transform(rows, plan),
            [{"label": "apple", "qty": 0, "price": 2.5, "amount": 0.0}],
        )

    def test_steps_subset_and_order(self):
        plan = {"steps": ["rename", "cast"], "rename": {"n": "qty"}, "cast": {"qty": "int"}}
        self.assertEqual(transform([{"n": "3"}], plan), [{"qty": 3}])

    def test_null_and_zero_are_distinct(self):
        plan = {"nulls": ["N/A"], "cast": {"qty": "int"}, "fill": {"qty": 9}}
        rows = [{"qty": "0"}, {"qty": ""}, {"qty": "N/A"}, {"qty": None}, {}]
        self.assertEqual(
            [row["qty"] for row in transform(rows, plan)],
            [0, 9, 9, 9, 9],
        )

    def test_zero_fill_does_not_swallow_real_zero(self):
        plan = {"cast": {"qty": "int"}, "fill": {"qty": 0}}
        rows = [{"qty": "0"}, {"qty": ""}]
        self.assertEqual(transform(rows, plan), [{"qty": 0}, {"qty": 0}])

    def test_cast_null_becomes_none_without_fill(self):
        plan = {"cast": {"qty": "int"}}
        self.assertEqual(transform([{"qty": ""}], plan), [{"qty": None}])


class ConflictTest(unittest.TestCase):
    def test_rename_target_conflict(self):
        with self.assertRaises(PlanError):
            compile_plan({"rename": {"a": "x", "b": "x"}})

    def test_derive_rename_conflict(self):
        with self.assertRaises(PlanError):
            compile_plan({"rename": {"a": "x"}, "derive": {"x": "a * 2"}})

    def test_fill_cast_type_conflict(self):
        with self.assertRaises(PlanError):
            compile_plan({"cast": {"qty": "int"}, "fill": {"qty": "abc"}})

    def test_fill_with_null_token_rejected(self):
        with self.assertRaises(PlanError):
            compile_plan({"nulls": ["N/A"], "fill": {"qty": "N/A"}})

    def test_unknown_cast_type(self):
        with self.assertRaises(PlanError):
            compile_plan({"cast": {"qty": "decimal"}})

    def test_bad_derive_expression(self):
        with self.assertRaises(PlanError):
            compile_plan({"derive": {"x": "qty *"}})

    def test_dangerous_derive_expression(self):
        with self.assertRaises(PlanError):
            compile_plan({"derive": {"x": "__import__('os')"}})


class OverrideTest(unittest.TestCase):
    PLAN = {
        "cast": {"qty": "int"},
        "fill": {"qty": 0},
        "strip": ["name"],
        "overrides": {
            "store": {"fill": {"qty": -1}, "strip": ["name", "note"]},
        },
    }

    def test_base_plan_without_source(self):
        rows = [{"qty": ""}]
        self.assertEqual(transform(rows, self.PLAN), [{"qty": 0}])

    def test_override_wins_for_matching_source(self):
        rows = [{"qty": ""}]
        self.assertEqual(transform(rows, self.PLAN, source="store"), [{"qty": -1}])

    def test_source_key_in_plan(self):
        plan = dict(self.PLAN, source="store")
        self.assertEqual(transform([{"qty": ""}], plan), [{"qty": -1}])

    def test_unknown_source_falls_back_to_base(self):
        rows = [{"qty": ""}]
        self.assertEqual(transform(rows, self.PLAN, source="web"), [{"qty": 0}])

    def test_list_section_replaced_not_merged(self):
        rows = [{"name": " a ", "note": " x ", "qty": "1"}]
        self.assertEqual(
            transform(rows, self.PLAN, source="store"),
            [{"name": "a", "note": "x", "qty": 1}],
        )
        self.assertEqual(
            transform(rows, self.PLAN),
            [{"name": "a", "note": " x ", "qty": 1}],
        )

    def test_plan_is_json_serializable(self):
        plan = json.loads(json.dumps(self.PLAN))
        self.assertEqual(transform([{"qty": ""}], plan, source="store"), [{"qty": -1}])


if __name__ == "__main__":
    unittest.main()
