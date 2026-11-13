import unittest

from dqrules import run


def make_config(rules, **extra):
    config = {"rules": rules}
    config.update(extra)
    return config


class RuleTypesTest(unittest.TestCase):
    def test_not_null(self):
        rows = [{"name": "a"}, {"name": ""}, {"name": None}, {"other": 1}]
        report = run(rows, make_config([
            {"name": "name_required", "type": "not_null", "field": "name"},
        ]))
        rule = report["rules"][0]
        self.assertEqual(rule["passed"], 1)
        self.assertEqual(rule["violated"], 3)
        self.assertEqual(rule["status"], "failed")

    def test_unique(self):
        rows = [{"id": "1"}, {"id": "2"}, {"id": "1"}, {"id": "2"}]
        report = run(rows, make_config([
            {"name": "id_unique", "type": "unique", "field": "id"},
        ]))
        rule = report["rules"][0]
        self.assertEqual(rule["passed"], 2)
        self.assertEqual(rule["violated"], 2)

    def test_range(self):
        rows = [{"age": 18}, {"age": 200}, {"age": -1}, {"age": "abc"}, {}]
        report = run(rows, make_config([
            {"name": "age_range", "type": "range", "field": "age",
             "min": 0, "max": 150},
        ]))
        rule = report["rules"][0]
        self.assertEqual(rule["passed"], 1)
        self.assertEqual(rule["violated"], 4)

    def test_enum(self):
        rows = [{"s": "on"}, {"s": "off"}, {"s": "bad"}, {}]
        report = run(rows, make_config([
            {"name": "s_enum", "type": "enum", "field": "s",
             "allowed": ["on", "off"]},
        ]))
        rule = report["rules"][0]
        self.assertEqual(rule["passed"], 2)
        self.assertEqual(rule["violated"], 2)

    def test_expression(self):
        rows = [{"a": 1, "b": 2}, {"a": 5, "b": 4}, {"a": "x", "b": 1}]
        report = run(rows, make_config([
            {"name": "sum_limit", "type": "expression",
             "expression": "row['a'] + row['b'] <= 10"},
        ]))
        rule = report["rules"][0]
        self.assertEqual(rule["passed"], 2)
        self.assertEqual(rule["violated"], 1)


class DependencyTest(unittest.TestCase):
    def test_skip_when_dependency_failed(self):
        rows = [{"id": ""}, {"id": "1"}]
        report = run(rows, make_config([
            {"name": "id_required", "type": "not_null", "field": "id"},
            {"name": "id_unique", "type": "unique", "field": "id",
             "depends_on": ["id_required"]},
        ]))
        skipped = report["rules"][1]
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(skipped["passed"], 0)
        self.assertEqual(skipped["violated"], 0)
        self.assertIn("id_required", skipped["skip_reason"])
        self.assertEqual(report["checks"], 2)

    def test_run_when_dependency_passed(self):
        rows = [{"id": "1"}, {"id": "2"}]
        report = run(rows, make_config([
            {"name": "id_required", "type": "not_null", "field": "id"},
            {"name": "id_unique", "type": "unique", "field": "id",
             "depends_on": ["id_required"]},
        ]))
        self.assertEqual(report["rules"][1]["status"], "passed")


class SampleRowsTest(unittest.TestCase):
    def test_sample_rows_locate_violations(self):
        rows = [{"v": i} for i in range(10)]
        rows[3]["v"] = ""
        rows[7]["v"] = None
        report = run(rows, make_config([
            {"name": "v_required", "type": "not_null", "field": "v"},
        ]))
        self.assertEqual(report["rules"][0]["sample_rows"], [3, 7])

    def test_sample_size_limit(self):
        rows = [{"v": ""} for _ in range(10)]
        report = run(rows, make_config(
            [{"name": "v_required", "type": "not_null", "field": "v"}],
            sample_size=3,
        ))
        rule = report["rules"][0]
        self.assertEqual(rule["sample_rows"], [0, 1, 2])
        self.assertEqual(rule["violated"], 10)


class ThresholdTest(unittest.TestCase):
    def config(self, warning, error):
        return make_config(
            [{"name": "v_required", "type": "not_null", "field": "v"}],
            thresholds={"warning": warning, "error": error},
        )

    def test_ok_below_warning(self):
        rows = [{"v": "x"}] * 10
        report = run(rows, self.config(0.1, 0.5))
        self.assertEqual(report["level"], "ok")
        self.assertEqual(report["violation_rate"], 0.0)

    def test_warning_level(self):
        rows = [{"v": "x"}] * 8 + [{"v": ""}] * 2
        report = run(rows, self.config(0.1, 0.5))
        self.assertEqual(report["violation_rate"], 0.2)
        self.assertEqual(report["level"], "warning")

    def test_error_level(self):
        rows = [{"v": "x"}] * 2 + [{"v": ""}] * 8
        report = run(rows, self.config(0.1, 0.5))
        self.assertEqual(report["level"], "error")


class ReportStructureTest(unittest.TestCase):
    def test_report_fields(self):
        rows = [{"id": "1"}, {"id": ""}]
        report = run(rows, make_config([
            {"name": "id_required", "type": "not_null", "field": "id"},
        ]))
        self.assertEqual(report["total_rows"], 2)
        self.assertEqual(report["checks"], 2)
        self.assertEqual(report["violations"], 1)
        self.assertEqual(report["violation_rate"], 0.5)
        self.assertIn("thresholds", report)
        rule = report["rules"][0]
        for key in ("name", "type", "status", "passed", "violated", "sample_rows"):
            self.assertIn(key, rule)


if __name__ == "__main__":
    unittest.main()
