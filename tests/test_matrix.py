import unittest

from reportcheck import coverage, report
from reportcheck import matrix

api = {
    "name": "GET /orders",
    "environment": "test",
    "types": {"sqli": "hit", "xss": "miss", "ssrf": "skip"},
}


class MatrixTest(unittest.TestCase):
    def setUp(self):
        matrix._reset()

    def types_of(self, rows):
        return [row["type"] for row in rows]

    def test_all_types_listed_with_results(self):
        result = coverage(dict(api))
        self.assertEqual(self.types_of(result["covered"]), ["sqli", "xss"])
        self.assertEqual(result["covered"][0]["result"], "hit")
        self.assertEqual(result["covered"][1]["result"], "miss")
        self.assertEqual(self.types_of(result["skipped"]), ["ssrf"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["total"], 3)

    def test_untested_and_skipped_are_distinct(self):
        result = coverage(
            {
                "name": "GET /orders",
                "environment": "test",
                "expected": ["sqli", "xss", "ssrf", "csrf"],
                "types": {"sqli": "hit", "ssrf": "skip:permission"},
            }
        )
        self.assertEqual(result["missing"], ["xss", "csrf"])
        self.assertEqual(self.types_of(result["skipped"]), ["ssrf"])
        # 未测和跳过不能混在同一栏。
        self.assertNotIn("ssrf", result["missing"])
        self.assertNotIn("xss", self.types_of(result["skipped"]))

    def test_new_api_enters_pending(self):
        result = coverage(
            {
                "name": "POST /pay",
                "environment": "test",
                "expected": ["sqli", "csrf"],
            }
        )
        self.assertTrue(result["new_api"])
        self.assertEqual(result["pending_new"], ["sqli", "csrf"])
        pending = report()["pending_new"]
        self.assertEqual(pending[0]["api"], "POST /pay")
        self.assertEqual(pending[0]["types"], ["sqli", "csrf"])

    def test_new_api_with_partial_results_pends_rest(self):
        result = coverage(
            {
                "name": "POST /pay",
                "environment": "test",
                "expected": ["sqli", "csrf"],
                "types": {"sqli": "miss"},
            }
        )
        self.assertEqual(result["pending_new"], ["csrf"])
        self.assertEqual(result["missing"], ["csrf"])

    def test_same_api_types_do_not_overwrite_each_other(self):
        coverage(
            {
                "name": "GET /orders",
                "environment": "test",
                "version": "1.0",
                "types": {"sqli": "hit"},
            }
        )
        result = coverage(
            {
                "name": "GET /orders",
                "environment": "test",
                "version": "1.0",
                "types": {"xss": "miss"},
            }
        )
        self.assertEqual(self.types_of(result["covered"]), ["sqli", "xss"])

    def test_env_rows_not_split(self):
        coverage({"name": "GET /orders", "environment": "test", "types": {"sqli": "hit"}})
        coverage({"name": "GET /orders", "environment": "prod", "types": {"xss": "hit"}})
        rows = report("GET /orders")["rows"]
        self.assertEqual({row["environment"] for row in rows}, {"test", "prod"})
        by_env = {row["environment"]: row for row in rows}
        # 测试环境只有 sqli 覆盖，线上只有 xss，互不串。
        self.assertEqual(self.types_of(by_env["test"]["covered"]), ["sqli"])
        self.assertEqual(self.types_of(by_env["prod"]["covered"]), ["xss"])

    def test_prod_skip_does_not_affect_test(self):
        coverage(
            {
                "name": "GET /orders",
                "environment": "test",
                "types": {"sqli": "hit"},
            }
        )
        coverage(
            {
                "name": "GET /orders",
                "environment": "prod",
                "types": {"sqli": {"result": "skip", "reason": "environment"}},
            }
        )
        by_env = {row["environment"]: row for row in report("GET /orders")["rows"]}
        self.assertEqual(by_env["test"]["skipped"], [])
        self.assertEqual(by_env["prod"]["skip_reasons"], {"sqli": "环境不支持"})

    def test_skip_reason_three_kinds(self):
        result = coverage(
            {
                "name": "GET /orders",
                "environment": "test",
                "types": {
                    "sqli": "skip:权限不足",
                    "xss": "skip:环境不支持",
                    "ssrf": "skip:没排期",
                },
            }
        )
        labels = {item["type"]: item["reason_label"] for item in result["skipped"]}
        self.assertEqual(
            labels,
            {"sqli": "权限不足", "xss": "环境不支持", "ssrf": "没排期"},
        )

    def test_unknown_skip_reason_rejected(self):
        with self.assertRaises(ValueError):
            coverage(
                {
                    "name": "GET /orders",
                    "environment": "test",
                    "types": {"sqli": "skip:没空"},
                }
            )

    def test_skip_without_reason_is_flagged(self):
        coverage(
            {"name": "GET /orders", "environment": "test", "types": {"sqli": "skip"}}
        )
        self.assertEqual(
            report()["skips_without_reason"],
            [{"api": "GET /orders", "environment": "test", "type": "sqli"}],
        )

    def test_version_coverage_drop_reported(self):
        coverage(
            {
                "name": "GET /orders",
                "environment": "prod",
                "version": "1.0",
                "types": {"sqli": "hit", "xss": "hit"},
            }
        )
        result = coverage(
            {
                "name": "GET /orders",
                "environment": "prod",
                "version": "2.0",
                "types": {"sqli": "hit"},
            }
        )
        self.assertTrue(result["dropped"])
        self.assertEqual(result["coverage_drop"]["lost_types"], ["xss"])
        self.assertAlmostEqual(result["coverage_drop"]["from_coverage"], 1.0)
        self.assertAlmostEqual(result["coverage_drop"]["to_coverage"], 0.5)
        self.assertEqual(len(report()["coverage_drops"]), 1)

    def test_drop_persists_while_still_low(self):
        base = {"name": "GET /orders", "environment": "prod", "expected": ["sqli", "xss"]}
        coverage(dict(base, version="1.0", types={"sqli": "hit", "xss": "hit"}))
        coverage(dict(base, version="2.0", types={"sqli": "hit"}))
        result = coverage(dict(base, version="3.0", types={"sqli": "hit"}))
        # 连续两版都停在低位，下降提示不能因为“和上一版一样”就消失。
        self.assertTrue(result["dropped"])
        self.assertEqual(result["coverage_drop"]["lost_types"], ["xss"])

    def test_backfill_returns_coverage_to_same_measure(self):
        coverage(
            {
                "name": "GET /orders",
                "environment": "prod",
                "version": "1.0",
                "expected": ["sqli", "xss"],
                "types": {"sqli": "hit", "xss": "hit"},
            }
        )
        coverage(
            {
                "name": "GET /orders",
                "environment": "prod",
                "version": "2.0",
                "types": {"sqli": "hit"},
            }
        )
        result = coverage(
            {
                "name": "GET /orders",
                "environment": "prod",
                "version": "2.0",
                "types": {"xss": "miss"},
            }
        )
        self.assertFalse(result["dropped"])
        self.assertEqual(result["coverage_drop"], None)
        self.assertAlmostEqual(result["coverage"], 1.0)
        self.assertEqual(report()["coverage_drops"], [])

    def test_new_type_does_not_trigger_false_drop(self):
        coverage(
            {
                "name": "GET /orders",
                "environment": "test",
                "version": "1.0",
                "types": {"sqli": "hit"},
            }
        )
        result = coverage(
            {
                "name": "GET /orders",
                "environment": "test",
                "version": "2.0",
                "types": {"sqli": "hit", "csrf": "miss"},
            }
        )
        # 覆盖没有下降，只是多了新类型。
        self.assertFalse(result["dropped"])
        self.assertEqual(result["missing"], [])

    def test_report_filters_by_api(self):
        coverage({"name": "GET /orders", "environment": "test", "types": {"sqli": "hit"}})
        coverage({"name": "POST /pay", "environment": "test", "types": {"sqli": "hit"}})
        data = report("GET /orders")
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["rows"][0]["api"], "GET /orders")
        self.assertEqual(report()["count"], 2)

    def test_report_returns_copy(self):
        coverage(dict(api))
        first = report("GET /orders")
        first["rows"][0]["covered"].clear()
        self.assertEqual(len(report("GET /orders")["rows"][0]["covered"]), 2)


if __name__ == "__main__":
    unittest.main()
