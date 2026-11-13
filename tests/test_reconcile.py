import unittest
import json

from multisource import reconcile

A = [{"id": 1, "amount": 10}, {"id": 2, "amount": 20}]
B = [{"id": 1, "amount": 10}]
C = [{"id": 3, "amount": 30}]


class ReconcileTest(unittest.TestCase):
    def test_missing_key_detected(self):
        result = reconcile([A, B], {"key": "id"})
        self.assertEqual([d["key"] for d in result["diffs"]], [2])

    def test_third_source_ignored(self):
        result = reconcile([A, B, C], {"key": "id"})
        self.assertEqual(result["sources"], 2)
        self.assertNotIn(3, [d["key"] for d in result["diffs"]])

    def test_no_attribution(self):
        result = reconcile([A, B], {"key": "id"})
        self.assertEqual(set(result["diffs"][0].keys()), {"key", "kind"})


ERP = [
    {"order_id": "A1", "amount": 100.0, "status": "PAID"},
    {"order_id": "A2", "amount": 200.0, "status": "PAID"},
    {"order_id": "A3", "amount": 300.0, "status": "OPEN"},
]
PAYMENT = [
    {"order_id": "A1", "amount": 10000, "status": "paid"},
    {"order_id": "A2", "amount": 20050, "status": "paid"},
    {"order_id": "A2", "amount": 20050, "status": "paid"},
    {"order_id": "A4", "amount": 40000, "status": "paid"},
]
CRM = [
    {"order_id": "A1", "amount": 100.0, "status": "Paid"},
    {"order_id": "A2", "amount": 200.0, "status": "PAID"},
    {"order_id": "A3", "amount": 300.0, "status": "CANCELLED"},
]
MULTI_CONFIG = {
    "mode": "multi",
    "key": "order_id",
    "source_names": ["erp", "payment", "crm"],
    "tolerance": 0.01,
    "conversions": [
        {"field": "amount", "sources": ["payment"], "op": "divide", "factor": 100},
        {"field": "status", "op": "lower"},
    ],
}


class MultiSourceAlignTest(unittest.TestCase):
    def test_missing_across_three_sources(self):
        result = reconcile([ERP, PAYMENT, CRM], MULTI_CONFIG)
        missing = [d for d in result["diffs"] if d["kind"] == "missing"]
        self.assertEqual([d["key"] for d in missing], ["A3", "A4"])
        self.assertEqual(missing[0]["missing_from"], ["payment"])
        self.assertEqual(missing[1]["missing_from"], ["erp", "crm"])

    def test_duplicate_detected_per_source(self):
        result = reconcile([ERP, PAYMENT, CRM], MULTI_CONFIG)
        dups = [d for d in result["diffs"] if d["kind"] == "duplicate"]
        self.assertEqual(len(dups), 1)
        self.assertEqual((dups[0]["key"], dups[0]["source"], dups[0]["count"]), ("A2", "payment", 2))

    def test_value_diff_with_consensus_attribution(self):
        result = reconcile([ERP, PAYMENT, CRM], MULTI_CONFIG)
        amount = [d for d in result["diffs"] if d["kind"] == "value" and d["field"] == "amount"]
        self.assertEqual(len(amount), 1)
        self.assertEqual(amount[0]["key"], "A2")
        self.assertEqual(amount[0]["consensus"], 200.0)
        self.assertEqual(amount[0]["attribution"]["sources"], ["payment"])

    def test_no_consensus_attributes_all_present_sources(self):
        result = reconcile([ERP, PAYMENT, CRM], MULTI_CONFIG)
        status = [d for d in result["diffs"] if d["kind"] == "value" and d["field"] == "status"]
        self.assertEqual(len(status), 1)
        self.assertIsNone(status[0]["consensus"])
        self.assertEqual(status[0]["attribution"]["sources"], ["erp", "crm"])

    def test_diff_order_is_key_sorted(self):
        result = reconcile([ERP, PAYMENT, CRM], MULTI_CONFIG)
        kinds = [(d["key"], d["kind"]) for d in result["diffs"]]
        self.assertEqual(kinds, [
            ("A2", "duplicate"), ("A2", "value"),
            ("A3", "missing"), ("A3", "value"),
            ("A4", "missing"),
        ])


class ConversionTest(unittest.TestCase):
    def test_unit_conversion_avoids_false_positive(self):
        left = [{"id": 1, "amount": 100.0}]
        right = [{"id": 1, "amount": 10000}]
        config = {"mode": "multi", "key": "id",
                  "conversions": [{"field": "amount", "sources": ["source_1"], "op": "divide", "factor": 100}]}
        self.assertEqual(reconcile([left, right], config)["diffs"], [])

    def test_case_conversion_on_status(self):
        left = [{"id": 1, "status": "PAID"}]
        right = [{"id": 1, "status": "paid"}]
        config = {"mode": "multi", "key": "id",
                  "conversions": [{"field": "status", "op": "lower"}]}
        self.assertEqual(reconcile([left, right], config)["diffs"], [])

    def test_tolerance_within_and_beyond(self):
        left = [{"id": 1, "amount": 100.0}, {"id": 2, "amount": 100.0}]
        right = [{"id": 1, "amount": 100.005}, {"id": 2, "amount": 100.5}]
        config = {"mode": "multi", "key": "id", "tolerance": 0.01}
        diffs = reconcile([left, right], config)["diffs"]
        self.assertEqual([d["key"] for d in diffs], [2])

    def test_unknown_conversion_op_raises(self):
        config = {"mode": "multi", "conversions": [{"field": "amount", "op": "magic"}]}
        with self.assertRaisesRegex(ValueError, "未知口径转换"):
            reconcile([A, B], config)

    def test_conversion_failure_raises_with_context(self):
        left = [{"id": 1, "amount": "abc"}]
        right = [{"id": 1, "amount": "abc"}]
        config = {"mode": "multi", "key": "id",
                  "conversions": [{"field": "amount", "op": "float"}]}
        with self.assertRaisesRegex(ValueError, "口径转换失败"):
            reconcile([left, right], config)


class AttributionReportTest(unittest.TestCase):
    def test_report_summary_and_drilldown(self):
        result = reconcile([ERP, PAYMENT, CRM], MULTI_CONFIG)
        report = result["report"]
        self.assertEqual(report["total"], 5)
        self.assertEqual(report["by_type"], {"duplicate": 1, "value": 2, "missing": 2})
        self.assertEqual(report["by_source"]["erp"]["total"], 2)
        self.assertEqual(report["by_source"]["payment"]["total"], 3)
        self.assertEqual(report["by_source"]["crm"]["total"], 2)
        drill = [result["diffs"][i] for i in report["by_key"]["A2"]]
        self.assertEqual([d["kind"] for d in drill], ["duplicate", "value"])

    def test_result_is_json_serializable(self):
        result = reconcile([ERP, PAYMENT, CRM], MULTI_CONFIG)
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_deterministic_across_runs(self):
        first = reconcile([ERP, PAYMENT, CRM], MULTI_CONFIG)
        second = reconcile([ERP, PAYMENT, CRM], MULTI_CONFIG)
        self.assertEqual(first, second)


class ErrorPathTest(unittest.TestCase):
    def test_too_few_sources(self):
        with self.assertRaisesRegex(ValueError, "至少需要两份"):
            reconcile([A], {"key": "id"})

    def test_row_missing_key_field(self):
        bad = [{"id": 1}, {"amount": 5}]
        with self.assertRaisesRegex(ValueError, "缺少业务键字段"):
            reconcile([A, bad], {"key": "id"})

    def test_source_names_length_mismatch(self):
        config = {"mode": "multi", "source_names": ["only_one"]}
        with self.assertRaisesRegex(ValueError, "不一致"):
            reconcile([A, B], config)

    def test_duplicate_source_names(self):
        config = {"mode": "multi", "source_names": ["x", "x"]}
        with self.assertRaisesRegex(ValueError, "重复"):
            reconcile([A, B], config)

    def test_unknown_mode(self):
        with self.assertRaisesRegex(ValueError, "未知对账模式"):
            reconcile([A, B], {"mode": "triple"})

    def test_negative_tolerance(self):
        with self.assertRaisesRegex(ValueError, "非负数值"):
            reconcile([A, B], {"mode": "multi", "tolerance": -0.1})


class DemoOutputTest(unittest.TestCase):
    def test_demo_output_matches_report(self):
        import subprocess
        import sys
        out = subprocess.run([sys.executable, "demo.py"], capture_output=True, text=True, check=True).stdout
        self.assertIn("差异总数: 5", out)
        self.assertIn("按类型: duplicate=1, missing=2, value=2", out)
        self.assertIn("按来源归因: erp=2, payment=3, crm=2", out)
