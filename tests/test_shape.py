import unittest

from jsonshape import (
    MIN_BATCHES,
    MIN_PAGES,
    extract_fingerprint,
    observe,
    report,
    reset,
)


def warm(site, payload, pages=MIN_PAGES):
    """喂够冷启动样本，最后一批结束后站点进入可告警状态。"""
    result = None
    for _ in range(MIN_BATCHES):
        result = observe(site, [dict(payload) for _ in range(pages)])
    return result


class ShapeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_first_observes_without_alert(self):
        self.assertFalse(observe("shop", {"a": 1})["alert"])

    def test_type_change_ignored(self):
        observe("shop", {"a": 1})
        self.assertFalse(observe("shop", {"a": "x"})["alert"])

    def test_fingerprint_recurses_paths_types_and_array_len(self):
        fingerprint = extract_fingerprint(
            {"id": 1, "items": [{"sku": "a"}, {"sku": "b"}]}
        )
        rendered = {".".join(path): entry for path, entry in fingerprint.items()}
        self.assertEqual(rendered["$.id"]["type"], "int")
        self.assertEqual(rendered["$.items"]["type"], "list")
        self.assertEqual(rendered["$.items"]["array_len"], 2)
        self.assertEqual(rendered["$.items.[].sku"]["type"], "str")

    def test_field_addition_and_removal_alert(self):
        warm("shop", {"id": 1, "name": "n"})
        added = observe("shop", {"id": 2, "name": "n", "price": 3})
        self.assertTrue(added["alert"])
        kinds = {alert["kind"] for alert in added["alerts"]}
        self.assertIn("field_added", kinds)
        add_alert = next(a for a in added["alerts"] if a["kind"] == "field_added")
        self.assertEqual(add_alert["path"], "$.price")
        self.assertIsNone(add_alert["baseline"])
        self.assertEqual(add_alert["current"]["type"], "int")
        self.assertEqual(add_alert["sample_pages"], [0])

        removed = observe("shop", {"id": 3})
        removed_kinds = {alert["kind"] for alert in removed["alerts"]}
        self.assertIn("field_removed", removed_kinds)
        remove_alert = next(a for a in removed["alerts"] if a["kind"] == "field_removed")
        self.assertEqual(remove_alert["path"], "$.name")
        self.assertIsNone(remove_alert["current"])
        self.assertEqual(remove_alert["baseline"]["type"], "str")
        self.assertTrue(
            any(a["path"] == "$.name" for a in report("shop")["alerts"])
        )

    def test_type_change_alerts_after_warmup(self):
        warm("shop", {"id": 1})
        result = observe("shop", {"id": "now-a-string"})
        alert = next(a for a in result["alerts"] if a["kind"] == "type_changed")
        self.assertEqual(alert["path"], "$.id")
        self.assertEqual(alert["baseline"]["type"], "int")
        self.assertEqual(alert["current"]["type"], "str")
        self.assertEqual(alert["sample_pages"], [0])

    def test_site_baselines_are_isolated(self):
        warm("shop", {"id": 1, "price": 2})
        self.assertFalse(observe("news", {"id": 1, "price": 2})["alert"])
        self.assertFalse(observe("news", {"id": 1, "price": 2})["alert"])

        shop_change = observe("shop", {"id": "x", "price": 2})
        self.assertTrue(shop_change["alert"])
        news_change = observe("news", {"id": "y", "price": 2})
        self.assertFalse(news_change["alert"])

        self.assertTrue(
            all(alert["site"] == "shop" for alert in report()["alerts"])
        )
        self.assertEqual(report("news")["alerts"], [])

    def test_cold_start_only_learns(self):
        for _ in range(MIN_BATCHES - 1):
            result = observe("shop", [{"id": 1, "v": "a"} for _ in range(2)])
            self.assertFalse(result["alert"])
            self.assertTrue(result["cold_start"])

        changed = observe("shop", [{"id": "x", "v": "a"} for _ in range(2)])
        self.assertFalse(changed["cold_start"])
        self.assertTrue(changed["alert"])
        self.assertEqual(report("shop")["alerts"][0]["kind"], "type_changed")

    def test_consecutive_anomalies_escalate_then_recover(self):
        warm("shop", {"id": 1})
        first = observe("shop", {"id": "x"})
        second = observe("shop", {"id": "x"})
        third = observe("shop", {"id": "x"})
        self.assertEqual(first["severity"], "warning")
        self.assertEqual(first["alerts"][0]["streak"], 1)
        self.assertEqual(second["severity"], "warning")
        self.assertEqual(third["severity"], "critical")
        self.assertEqual(third["alerts"][0]["streak"], 3)

        recovered = observe("shop", {"id": 9})
        self.assertFalse(recovered["alert"])
        again = observe("shop", {"id": "x"})
        self.assertEqual(again["severity"], "warning")
        self.assertEqual(again["alerts"][0]["streak"], 1)

    def test_missing_rate_spike_with_sample_pages(self):
        warm("shop", {"id": 1, "coupon": "c"})
        batch = [{"id": 2, "coupon": "c"}] + [{"id": index} for index in range(4)]
        result = observe("shop", batch)
        alert = next(a for a in result["alerts"] if a["kind"] == "missing_rate_spike")
        self.assertEqual(alert["path"], "$.coupon")
        self.assertEqual(alert["baseline"]["missing_rate"], 0.0)
        self.assertEqual(alert["current"]["missing_rate"], 0.8)
        self.assertEqual(alert["sample_pages"], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
