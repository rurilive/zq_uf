import unittest

from configdrift import baseline, diff, report

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
        self.assertEqual({item["repeats"] for item in report()["items"]}, {1})
        self.assertEqual(report()["repeats"], {})

    def test_report_filters_by_key(self):
        baseline(base)
        diff({"host": "web5", "config": {"timeout": "1", "workers": "2"}})
        items = report("timeout")["items"]
        self.assertEqual({item["key"] for item in items}, {"timeout"})


if __name__ == "__main__":
    unittest.main()
