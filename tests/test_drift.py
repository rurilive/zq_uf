import unittest

from configdrift import baseline, diff, history, report, set_range

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

    def test_missing_key_is_drift(self):
        baseline(base)
        result = diff({"host": "web3", "config": {"timeout": "30", "workers": "8"}})
        self.assertEqual([item["key"] for item in result["drift"]], ["retry"])

    def test_repeats_always_one(self):
        baseline(base)
        diff({"host": "web4", "config": {}})
        diff({"host": "web4", "config": {}})
        self.assertEqual({item["repeats"] for item in report(host="web4")["items"]}, {1})
        self.assertEqual(report(host="web4")["repeats"], {})

    def test_report_filters_by_key(self):
        baseline(base)
        diff({"host": "web5", "config": {"timeout": "1", "workers": "2"}})
        items = report("timeout")["items"]
        self.assertEqual({item["key"] for item in items}, {"timeout"})

    def test_value_within_tolerance_is_not_drift(self):
        baseline(base, ranges={"timeout": {"tolerance": 5}})
        result = diff({"host": "app1", "config": {"timeout": "33", "workers": "8", "retry": "3"}})
        self.assertTrue(result["passed"])

    def test_value_outside_tolerance_is_drift(self):
        baseline(base, ranges={"timeout": {"tolerance": 5}})
        result = diff({"host": "app2", "config": {"timeout": "40", "workers": "8", "retry": "3"}})
        self.assertEqual([item["key"] for item in result["drift"]], ["timeout"])

    def test_allowed_values_range(self):
        baseline(base, ranges={"workers": ["8", "16"]})
        result = diff({"host": "app3", "config": {"timeout": "30", "workers": "16", "retry": "3"}})
        self.assertTrue(result["passed"])

    def test_set_range_requires_confirmation(self):
        baseline(base)
        pending = set_range("timeout", {"tolerance": 10})
        self.assertTrue(pending["pending"])
        result = diff({"host": "app4", "config": {"timeout": "35", "workers": "8", "retry": "3"}})
        self.assertFalse(result["passed"])  # 未确认，范围未生效
        done = set_range("timeout", {"tolerance": 10}, confirm=True)
        self.assertFalse(done["pending"])
        result = diff({"host": "app4", "config": {"timeout": "35", "workers": "8", "retry": "3"}})
        self.assertTrue(result["passed"])

    def test_set_range_rejects_unknown_key(self):
        baseline(base)
        with self.assertRaises(ValueError):
            set_range("nope", {"tolerance": 1})

    def test_fix_then_recheck_passes(self):
        baseline(base)
        diff({"host": "db1", "config": {"timeout": "99", "workers": "8", "retry": "3"}})
        result = diff({"host": "db1", "config": dict(base)})
        self.assertTrue(result["passed"])
        item = report("timeout", host="db1")["items"][0]
        self.assertTrue(item["fixed"])

    def test_repeated_drift_is_counted(self):
        baseline(base)
        diff({"host": "db2", "config": {"timeout": "1", "workers": "8", "retry": "3"}})
        diff({"host": "db2", "config": dict(base)})  # 整改
        diff({"host": "db2", "config": {"timeout": "2", "workers": "8", "retry": "3"}})  # 又漂移
        item = report("timeout", host="db2")["items"][0]
        self.assertEqual(item["repeats"], 2)
        self.assertFalse(item["fixed"])
        self.assertEqual(report(host="db2")["repeats"], {"timeout": 1})

    def test_stats_by_key_and_host(self):
        baseline(base)
        diff({"host": "db3", "config": {"timeout": "1", "workers": "1", "retry": "3"}})
        diff({"host": "db4", "config": {"timeout": "1", "workers": "8", "retry": "3"}})
        result = report(host="db3")
        self.assertEqual(result["by_key"], {"timeout": 1, "workers": 1})
        self.assertEqual(result["by_host"], {"db3": 2})
        self.assertEqual(report(host="db4")["by_key"], {"timeout": 1})

    def test_history_reviews_events_in_order(self):
        baseline(base)
        diff({"host": "db5", "config": {"timeout": "1", "workers": "8", "retry": "3"}})
        diff({"host": "db5", "config": dict(base)})
        events = history(key="timeout", host="db5")
        self.assertEqual([e["type"] for e in events], ["drift", "fixed"])
        self.assertTrue(all("time" in e for e in events))


if __name__ == "__main__":
    unittest.main()
