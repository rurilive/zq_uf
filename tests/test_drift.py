import unittest

from configdrift import baseline, diff, report, set_tolerance

base = {"timeout": "30", "workers": "8", "retry": "3"}


class DriftTest(unittest.TestCase):
    def test_identical_config_passes(self):
        baseline(base)
        result = diff({"host": "web1", "config": dict(base)})
        self.assertTrue(result["passed"])
        self.assertEqual(result["drift"], [])

    def test_any_difference_is_drift(self):
        baseline(base)
        result = diff({"host": "web2", "config": {"timeout": "31", "workers": "8", "retry": "3"}})
        self.assertFalse(result["passed"])
        self.assertEqual([item["key"] for item in result["drift"]], ["timeout"])
        self.assertEqual(result["drift"][0]["current"], "31")
        self.assertEqual(result["drift"][0]["baseline"], "30")

    def test_missing_key_is_drift(self):
        baseline(base)
        result = diff({"host": "web3", "config": {"timeout": "30", "workers": "8"}})
        self.assertEqual([item["key"] for item in result["drift"]], ["retry"])

    def test_within_tolerance_passes(self):
        baseline(base, tolerances={"timeout": 5, "retry": ["3", "4"]})
        result = diff({"host": "web1", "config": {"timeout": "34", "workers": "8", "retry": "4"}})
        self.assertTrue(result["passed"])

    def test_out_of_tolerance_is_drift(self):
        baseline(base, tolerances={"timeout": 5})
        result = diff({"host": "web1", "config": {"timeout": "36", "workers": "8", "retry": "3"}})
        self.assertEqual([item["key"] for item in result["drift"]], ["timeout"])

    def test_range_tolerance(self):
        baseline(base, tolerances={"workers": {"min": 4, "max": 16}})
        self.assertTrue(diff({"host": "h1", "config": {**base, "workers": "12"}})["passed"])
        self.assertFalse(diff({"host": "h2", "config": {**base, "workers": "32"}})["passed"])

    def test_unfixed_drift_not_counted_twice(self):
        baseline(base)
        diff({"host": "web4", "config": {}})
        result = diff({"host": "web4", "config": {}})
        self.assertEqual({item["repeats"] for item in result["drift"]}, {1})

    def test_repeat_drift_after_fix_is_counted(self):
        baseline(base)
        bad = {"timeout": "1", "workers": "8", "retry": "3"}
        diff({"host": "web4", "config": bad})
        diff({"host": "web4", "config": dict(base)})  # 整改
        result = diff({"host": "web4", "config": bad})  # 再次漂移
        self.assertEqual(result["drift"][0]["repeats"], 2)
        self.assertEqual(report()["repeats"], {"timeout": 2})

    def test_fix_requires_match_with_baseline(self):
        baseline(base)
        diff({"host": "web6", "config": {"timeout": "1", "workers": "8", "retry": "3"}})
        still_bad = diff({"host": "web6", "config": {"timeout": "2", "workers": "8", "retry": "3"}})
        self.assertFalse(still_bad["passed"])
        self.assertFalse(report("timeout")["items"][0]["fixed"])
        fixed = diff({"host": "web6", "config": dict(base)})
        self.assertTrue(fixed["passed"])
        self.assertTrue(report("timeout")["items"][0]["fixed"])

    def test_stats_by_key_and_host(self):
        baseline(base)
        diff({"host": "a", "config": {"timeout": "1", "workers": "8", "retry": "3"}})
        diff({"host": "b", "config": {"timeout": "1", "workers": "1", "retry": "3"}})
        r = report()
        self.assertEqual(r["by_key"], {"timeout": 2, "workers": 1})
        self.assertEqual(r["by_host"], {"a": 1, "b": 2})

    def test_history_records_drift_and_fix_in_order(self):
        baseline(base)
        diff({"host": "web7", "config": {"timeout": "1", "workers": "8", "retry": "3"}})
        diff({"host": "web7", "config": dict(base)})
        kinds = [(e["kind"], e["key"], e["host"]) for e in report()["history"]]
        self.assertEqual(kinds, [("drift", "timeout", "web7"), ("fixed", "timeout", "web7")])

    def test_report_filters_by_key(self):
        baseline(base)
        diff({"host": "web5", "config": {"timeout": "1", "workers": "2"}})
        items = report("timeout")["items"]
        self.assertEqual({item["key"] for item in items}, {"timeout"})

    def test_report_filters_by_host(self):
        baseline(base)
        diff({"host": "a", "config": {}})
        diff({"host": "b", "config": {}})
        items = report(host="a")["items"]
        self.assertEqual({item["host"] for item in items}, {"a"})

    def test_set_tolerance_requires_double_confirm(self):
        baseline(base)
        pending = set_tolerance("timeout", 10)
        self.assertTrue(pending["pending"])
        # 未确认前不生效
        result = diff({"host": "h", "config": {"timeout": "39", "workers": "8", "retry": "3"}})
        self.assertFalse(result["passed"])
        applied = set_tolerance("timeout", 10, confirm=pending["token"])
        self.assertTrue(applied["applied"])
        result = diff({"host": "h", "config": {"timeout": "39", "workers": "8", "retry": "3"}})
        self.assertTrue(result["passed"])

    def test_set_tolerance_wrong_token_rejected(self):
        baseline(base)
        set_tolerance("timeout", 10)
        with self.assertRaises(ValueError):
            set_tolerance("timeout", 10, confirm="bad-token")

    def test_set_tolerance_unknown_key_rejected(self):
        baseline(base)
        with self.assertRaises(ValueError):
            set_tolerance("nope", 10)


if __name__ == "__main__":
    unittest.main()
