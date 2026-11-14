import unittest

from jsonshape import fingerprint, observe, report, reset
from jsonshape.shape import ESCALATE_BATCHES, MIN_SAMPLES


class ShapeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_first_observes_without_alert(self):
        self.assertFalse(observe("shop", {"a": 1})["alert"])

    def test_baseline_isolated_per_site(self):
        # 旧实现全局共享基线；新实现按站点各自冷启动，互不告警
        observe("shop", {"a": 1})
        self.assertFalse(observe("news", {"b": 1})["alert"])

    def test_type_change_ignored(self):
        observe("shop", {"a": 1})
        self.assertFalse(observe("shop", {"a": "x"})["alert"])

    def test_fingerprint_recursive_paths_types_and_lengths(self):
        fp = fingerprint({
            "id": 1,
            "tags": ["a", "bb"],
            "meta": {"name": "x"},
        })
        self.assertEqual(fp["$"]["types"], ["dict"])
        self.assertEqual(fp["$.id"]["types"], ["int"])
        self.assertEqual(fp["$.tags"]["types"], ["list"])
        self.assertEqual(fp["$.tags[0]"]["types"], ["str"])
        self.assertEqual(fp["$.meta.name"]["types"], ["str"])
        self.assertEqual(fp["$.tags"]["lengths"], [2])

    def test_cold_start_only_learns(self):
        for page in range(MIN_SAMPLES):
            result = observe("shop", {"id": page, "ok": True})
            self.assertFalse(result["alert"])
            self.assertIsNone(result["severity"])
        # 基线建立后，结构正常的批次仍不告警
        self.assertFalse(observe("shop", {"id": 99, "ok": False})["alert"])

    def test_field_added_alert(self):
        for page in range(MIN_SAMPLES):
            observe("shop", {"id": page})
        result = observe("shop", {"id": 1, "name": "x"})
        self.assertTrue(result["alert"])
        self.assertEqual(result["severity"], "warning")
        alert = result["alerts"][0]
        self.assertEqual(alert["kind"], "field_added")
        self.assertEqual(alert["path"], "$.name")
        self.assertIsNone(alert["baseline"])
        self.assertEqual(alert["current"]["sample"], "'x'")
        self.assertEqual(alert["sample_page"], MIN_SAMPLES + 1)

    def test_field_removed_alert(self):
        for page in range(MIN_SAMPLES):
            observe("shop", {"id": page, "name": "n"})
        result = observe("shop", {"id": 1})
        alert = next(a for a in result["alerts"] if a["kind"] == "field_removed")
        self.assertEqual(alert["path"], "$.name")
        self.assertIsNone(alert["current"])
        self.assertEqual(alert["baseline"]["types"], ["str"])
        self.assertGreaterEqual(alert["sample_page"], MIN_SAMPLES + 1)

    def test_type_change_alert_after_baseline(self):
        for _ in range(MIN_SAMPLES):
            observe("shop", {"id": 1})
        result = observe("shop", {"id": "1"})
        alert = next(a for a in result["alerts"] if a["kind"] == "type_changed")
        self.assertEqual(alert["path"], "$.id")
        self.assertEqual(alert["baseline"]["types"], ["int"])
        self.assertEqual(alert["current"]["types"], ["str"])
        self.assertEqual(alert["current"]["sample"], "'1'")

    def test_nested_field_change_alert(self):
        for _ in range(MIN_SAMPLES):
            observe("shop", {"meta": {"name": "x"}})
        result = observe("shop", {"meta": {"name": "x", "rank": 1}})
        paths = {alert["path"] for alert in result["alerts"]}
        self.assertIn("$.meta.rank", paths)

    def test_missing_rate_spike_alert(self):
        observe("shop", {"id": 1, "coupon": "c"})
        observe("shop", {"id": 2, "coupon": "c"})
        observe("shop", {"id": 3})  # coupon 基线出现率 2/3，属可选字段
        observe("shop", {"id": 4})
        observe("shop", {"id": 5})
        observe("shop", {"id": 6})
        result = observe("shop", {"id": 7})
        alert = next(a for a in result["alerts"] if a["kind"] == "missing_rate_spike")
        self.assertEqual(alert["path"], "$.coupon")
        self.assertAlmostEqual(alert["baseline"]["missing_rate"], 1 / 3, places=1)
        self.assertAlmostEqual(alert["current"]["missing_rate"], 4 / 6, places=1)
        self.assertEqual(alert["current"]["sample"], "<missing>")

    def test_consecutive_anomalies_escalate_then_recover(self):
        for _ in range(MIN_SAMPLES):
            observe("shop", {"id": 1, "name": "n"})
        first = observe("shop", {"id": 1})
        second = observe("shop", {"id": 2})
        third = observe("shop", {"id": 3})
        self.assertEqual(first["severity"], "warning")
        self.assertEqual(second["severity"], "warning")
        self.assertEqual(third["severity"], "critical")
        self.assertEqual(third["alerts"][0]["consecutive"], ESCALATE_BATCHES)
        critical = [a for a in report("shop")["alerts"] if a["severity"] == "critical"]
        self.assertTrue(critical)

        recovered = observe("shop", {"id": 4, "name": "n"})
        self.assertFalse(recovered["alert"])
        again = observe("shop", {"id": 5})
        self.assertTrue(again["alert"])
        self.assertEqual(again["severity"], "warning")
        self.assertEqual(again["alerts"][0]["consecutive"], 1)

    def test_site_baselines_are_isolated(self):
        for _ in range(MIN_SAMPLES):
            observe("shop", {"id": 1})
            observe("news", {"title": "t"})
        self.assertTrue(observe("shop", {"id": 2, "extra": 1})["alert"])
        self.assertFalse(observe("news", {"title": "new"})["alert"])
        self.assertEqual(len(report("shop")["alerts"]), 1)
        self.assertEqual(len(report("news")["alerts"]), 0)
        self.assertEqual(len(report()["alerts"]), 1)
