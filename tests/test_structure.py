import unittest

from sitemapdiff import DEFAULT_CONFIG, Tracker, diff, snapshot


def links(section, count, prefix="item"):
    return ["https://site.test/%s/%s-%d" % (section, prefix, i) for i in range(count)]


def find(alerts, alert_type, section=None):
    return [
        alert
        for alert in alerts
        if alert["type"] == alert_type and (section is None or alert["section"] == section)
    ]


class FingerprintTest(unittest.TestCase):
    def test_fingerprint_is_order_and_duplicate_independent(self):
        a = snapshot({"links": ["/b/1", "/a/1", "/a/2"]})
        b = snapshot({"links": ["/a/2", "/a/1", "/b/1", "/a/1"]})
        self.assertEqual(a["fingerprint"]["digest"], b["fingerprint"]["digest"])

    def test_fingerprint_changes_with_section_structure(self):
        a = snapshot({"links": ["/a/1", "/a/2"]})
        b = snapshot({"links": ["/a/1", "/a/2", "/b/1"]})
        self.assertNotEqual(a["fingerprint"]["digest"], b["fingerprint"]["digest"])

    def test_section_fingerprint_tracks_links_and_depths(self):
        snap = snapshot({"links": ["/a/1", "/a/1/x", "/a/2/x"]})
        section = snap["sections"]["a"]
        self.assertEqual(section["links"], 3)
        self.assertEqual(section["depths"], {"2": 1, "3": 2})
        self.assertEqual(section["min_depth"], 2)
        self.assertEqual(section["max_depth"], 3)

    def test_section_level_is_configurable(self):
        raw = {"links": ["/a/1", "/a/2"]}
        self.assertEqual(set(snapshot(raw)["sections"]), {"a"})
        self.assertEqual(set(snapshot(raw, config={"section_level": 2})["sections"]), {"a/1", "a/2"})

    def test_mismatched_section_level_is_rejected(self):
        old = snapshot({"links": ["/a/1"]}, config={"section_level": 1})
        new = snapshot({"links": ["/a/1"]}, config={"section_level": 2})
        with self.assertRaises(ValueError):
            diff(old, new)

    def test_unknown_config_is_rejected(self):
        with self.assertRaises(ValueError):
            snapshot({"links": ["/a/1"]}, config={"no_such_option": 1})


class SectionAlertTest(unittest.TestCase):
    def test_new_section_alert_has_samples_and_old_new(self):
        old = snapshot({"links": ["/a/1", "/a/2"]})
        new = snapshot({"links": ["/a/1", "/a/2", "/b/1", "/b/2", "/b/3"]})
        result = diff(old, new)
        alerts = find(result["alerts"], "new_section", "b")
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["severity"], "warning")
        self.assertIsNone(alert["old"])
        self.assertEqual(alert["new"]["links"], 3)
        self.assertEqual(len(alert["samples"]["new"]), 3)
        self.assertEqual(alert["samples"]["old"], [])
        self.assertIn("b", result["changes"]["by_section"])

    def test_new_section_below_min_links_is_ignored(self):
        result = diff(
            snapshot({"links": ["/a/1"]}),
            snapshot({"links": ["/a/1", "/b/1"]}),
            config={"new_section_min_links": 2},
        )
        self.assertEqual(find(result["alerts"], "new_section"), [])

    def test_samples_are_capped_per_section(self):
        old = snapshot({"links": ["/a/1"]})
        new = snapshot({"links": ["/a/1"] + links("b", 20)})
        result = diff(old, new)
        self.assertEqual(len(result["samples"]["b"]["added"]), DEFAULT_CONFIG["sample_size"])
        self.assertEqual(len(find(result["alerts"], "new_section", "b")[0]["samples"]["new"]), 3)

    def test_samples_are_stable(self):
        old = snapshot({"links": ["/a/1"]})
        new = snapshot({"links": ["/a/1"] + links("b", 20)})
        first = diff(old, new)["samples"]
        second = diff(old, new)["samples"]
        self.assertEqual(first, second)


class LevelChangeTest(unittest.TestCase):
    def test_section_moved_under_new_parent(self):
        old = snapshot({"links": links("blog", 3) + links("news", 2)})
        new = snapshot({"links": ["https://site.test/news/blog/blog-%d" % i for i in range(3)] + links("news", 2)})
        result = diff(old, new)
        alerts = find(result["alerts"], "level_change")
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["old"]["section"], "blog")
        self.assertEqual(alert["old"]["depth"], 1)
        self.assertEqual(alert["new"]["section"], "news/blog")
        self.assertEqual(alert["new"]["depth"], 2)
        self.assertEqual(alert["samples"]["old"], links("blog", 3))
        self.assertTrue(all("/news/blog/" in link for link in alert["samples"]["new"]))

    def test_nested_children_report_only_shallowest_move(self):
        old = snapshot({"links": links("blog", 3)})
        new = snapshot(
            {"links": ["https://site.test/news/blog/blog-%d" % i for i in range(3)]}
        )
        keys = [alert["key"] for alert in diff(old, new)["alerts"] if alert["type"] == "level_change"]
        self.assertEqual(keys, ["level_change:blog->news/blog"])

    def test_depth_profile_change_within_section(self):
        old = snapshot({"links": ["/a/1", "/a/2", "/a/1/x", "/a/2/x"]})
        new = snapshot({"links": ["/a/1/x", "/a/2/x", "/a/3/x"]})
        alerts = find(diff(old, new)["alerts"], "level_change", "a")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["old"]["min_depth"], 2)
        self.assertEqual(alerts[0]["new"]["min_depth"], 3)
        self.assertIn("/a/1", alerts[0]["samples"]["old"])

    def test_unrelated_changes_do_not_raise_level_alert(self):
        old = snapshot({"links": links("a", 3)})
        new = snapshot({"links": links("a", 4)})
        self.assertEqual(find(diff(old, new)["alerts"], "level_change"), [])


class BulkRemovalTest(unittest.TestCase):
    def test_large_section_removal_alerts(self):
        old = snapshot({"links": links("a", 50) + links("b", 50)})
        new = snapshot({"links": links("a", 50) + links("b", 5)})
        result = diff(old, new)
        alerts = find(result["alerts"], "bulk_removal", "b")
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["old"]["removed"], 45)
        self.assertEqual(alert["old"]["links"], 50)
        self.assertEqual(len(alert["samples"]["old"]), 3)

    def test_small_removal_below_threshold_is_quiet(self):
        old = snapshot({"links": links("a", 50)})
        new = snapshot({"links": links("a", 48)})
        self.assertEqual(find(diff(old, new)["alerts"], "bulk_removal"), [])

    def test_ratio_and_count_thresholds_are_configurable(self):
        old = snapshot({"links": links("a", 14)})
        new = snapshot({"links": links("a", 11)})
        strict = diff(old, new, config={"bulk_removal_ratio": 0.5})
        loose = diff(old, new, config={"bulk_removal_ratio": 0.1, "bulk_removal_min": 3})
        self.assertEqual(find(strict["alerts"], "bulk_removal"), [])
        self.assertEqual(len(find(loose["alerts"], "bulk_removal", "a")), 1)

    def test_site_wide_removal_reports_once_per_scope(self):
        old = snapshot({"links": links("a", 40) + links("b", 40)})
        new = snapshot({"links": links("a", 10) + links("b", 10)})
        result = diff(old, new)
        alerts = find(result["alerts"], "bulk_removal")
        self.assertEqual([alert["scope"] for alert in alerts], ["site", "section", "section"])


class EscalationTest(unittest.TestCase):
    def build(self):
        old = snapshot({"links": links("a", 10)})
        new = snapshot({"links": links("a", 10) + links("b", 5)})
        return old, new

    def test_repeated_batches_escalate(self):
        tracker = Tracker({"escalate_after": 2})
        old, new = self.build()
        first = tracker.diff(old, new)
        second = tracker.diff(old, new)
        third = tracker.diff(old, new)
        self.assertEqual(first["alerts"][0]["severity"], "warning")
        self.assertEqual(first["escalated"], [])
        self.assertEqual(second["alerts"][0]["severity"], "alert")
        self.assertEqual(second["escalated"][0]["streak"], 2)
        self.assertEqual(third["escalated"][0]["streak"], 3)
        self.assertEqual(third["summary"]["batch"], 3)

    def test_single_batch_does_not_escalate_when_threshold_is_three(self):
        tracker = Tracker({"escalate_after": 3})
        old, new = self.build()
        self.assertEqual(tracker.diff(old, new)["escalated"], [])
        self.assertEqual(tracker.diff(old, new)["escalated"], [])
        self.assertEqual(len(tracker.diff(old, new)["escalated"]), 1)

    def test_alert_absent_in_a_batch_resets_streak(self):
        tracker = Tracker({"escalate_after": 2})
        old, new = self.build()
        tracker.diff(old, new)
        tracker.diff(new, old)  # 结构恢复，告警消失
        again = tracker.diff(old, new)
        self.assertEqual(again["alerts"][0]["severity"], "warning")
        self.assertEqual(again["escalated"], [])

    def test_stateless_diff_never_escalates(self):
        old, new = self.build()
        self.assertEqual(diff(old, new)["alerts"][0]["severity"], "warning")
        self.assertEqual(diff(old, new)["escalated"], [])


if __name__ == "__main__":
    unittest.main()
