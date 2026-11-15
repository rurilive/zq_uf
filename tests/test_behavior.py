import csv
import os
import tempfile
import unittest

from linksieve import LinkStore, NormalizeRules, due, fingerprint, legacy_fingerprint
from linksieve.links import RULE_ALIAS, RULE_COLD_WAIT, RULE_HOT_WAIT, RULE_NEW
from linksieve.normalize import normalize


class FingerprintTest(unittest.TestCase):
    def test_tracking_params_are_ignored(self):
        a = fingerprint("https://shop/p?id=7&utm_source=x")
        b = fingerprint("https://shop/p?id=7&fbclid=abc")
        c = fingerprint("https://shop/p?id=7")
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_query_order_is_ignored(self):
        a = fingerprint("https://shop/p?id=7&lang=en")
        b = fingerprint("https://shop/p?lang=en&id=7")
        self.assertEqual(a, b)

    def test_fragment_is_ignored(self):
        self.assertEqual(
            fingerprint("https://shop/p?id=7#comments"),
            fingerprint("https://shop/p?id=7"),
        )

    def test_path_is_preserved(self):
        self.assertNotEqual(fingerprint("https://shop/a"), fingerprint("https://shop/b"))
        self.assertNotEqual(fingerprint("https://shop/A"), fingerprint("https://shop/a"))

    def test_pagination_is_preserved(self):
        self.assertNotEqual(
            fingerprint("https://shop/list?page=1"), fingerprint("https://shop/list?page=2")
        )
        self.assertNotEqual(
            fingerprint("https://shop/list?offset=0&utm_source=x"),
            fingerprint("https://shop/list?offset=20&utm_source=x"),
        )

    def test_pagination_name_is_not_treated_as_tracking(self):
        # "p" is a page parameter even though a bare "_p" style prefix exists.
        self.assertNotEqual(fingerprint("https://shop/p?p=1"), fingerprint("https://shop/p?p=2"))

    def test_host_and_default_port_normalised(self):
        self.assertEqual(
            fingerprint("HTTPS://SHOP.example:443/p"),
            fingerprint("https://shop.example/p"),
        )

    def test_legacy_fingerprint_still_available(self):
        self.assertNotEqual(
            legacy_fingerprint("https://shop/p?utm_source=x"),
            legacy_fingerprint("https://shop/p"),
        )

    def test_empty_urls_do_not_crash(self):
        self.assertEqual(fingerprint(None), fingerprint(""))


class DueTest(unittest.TestCase):
    def setUp(self):
        self.store = LinkStore()

    def test_new_target_is_due_and_reports_rule(self):
        result = self.store.due("https://b/1", 100)
        self.assertTrue(result["due"])
        self.assertEqual(result["matched_rule"], RULE_NEW)
        self.assertTrue(result["explanation"])

    def test_second_call_waits(self):
        self.store.due("https://c/1", 100)
        result = self.store.due("https://c/1", 200)
        self.assertFalse(result["due"])

    def test_after_interval_is_due(self):
        self.store.due("https://d/1", 100)
        self.assertTrue(self.store.due("https://d/1", 3700)["due"])

    def test_alias_is_not_due_again(self):
        first = self.store.due("https://e/1?utm_source=a", 100)
        self.assertTrue(first["due"])
        alias = self.store.due("https://e/1#top", 200)
        self.assertFalse(alias["due"])
        self.assertTrue(alias["alias"])
        self.assertEqual(alias["alias_of"], "https://e/1?utm_source=a")
        self.assertEqual(alias["fingerprint"], first["fingerprint"])

    def test_first_spelling_stays_canonical(self):
        self.store.due("https://f/1?utm_medium=mail", 100)
        self.store.due("https://f/1?utm_source=web", 200)
        report = self.store.alias_report("f")
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["address"], "https://f/1?utm_medium=mail")
        self.assertEqual(report[0]["alias_count"], 1)

    def test_alias_hits_are_counted(self):
        self.store.due("https://g/1", 100)
        for t in range(5):
            self.store.due("https://g/1#frag", 200 + t)
        report = self.store.alias_report("g")
        self.assertEqual(report[0]["alias_count"], 1)
        self.assertEqual(report[0]["samples"], ["https://g/1#frag"])

    def test_earlier_spelling_takes_over_as_canonical(self):
        self.store.due("https://o/1#late", 200)
        result = self.store.due("https://o/1?utm_source=x", 100)
        self.assertEqual(result["address"], "https://o/1?utm_source=x")
        self.assertFalse(result["alias"])
        report = self.store.alias_report("o")
        self.assertEqual(report[0]["address"], "https://o/1?utm_source=x")
        self.assertEqual(report[0]["samples"], ["https://o/1#late"])

    def test_pagination_is_a_separate_target(self):
        self.assertTrue(self.store.due("https://h/list?page=1", 100)["due"])
        second = self.store.due("https://h/list?page=2", 100)
        self.assertTrue(second["due"])
        self.assertFalse(second["alias"])

    def test_result_shape_keeps_old_keys(self):
        result = self.store.due("https://i/1", 100)
        for key in ("url", "fingerprint", "due", "interval", "reason"):
            self.assertIn(key, result)
        for key in ("matched_rule", "explanation", "canonical_url", "alias"):
            self.assertIn(key, result)


class AdaptiveIntervalTest(unittest.TestCase):
    def test_hot_page_returns_sooner(self):
        store = LinkStore()
        hot = "https://news/hot"
        cold = "https://news/cold"
        store.due(hot, 0)
        store.due(cold, 0)
        for i in range(4):
            now = 10 * (i + 1)
            store.due(hot, now)
            store.observe(hot, now, changed=True)
            store.due(cold, now)
            store.observe(cold, now, changed=False)

        hot_result = store.due(hot, 10 * 5)
        cold_result = store.due(cold, 10 * 5)
        self.assertLess(hot_result["interval"], store.base_interval)
        self.assertGreater(cold_result["interval"], store.base_interval)
        self.assertEqual(hot_result["matched_rule"], RULE_HOT_WAIT)
        self.assertEqual(cold_result["matched_rule"], RULE_COLD_WAIT)

    def test_interval_is_bounded(self):
        store = LinkStore()
        url = "https://x/1"
        store.due(url, 0)
        for i in range(50):
            now = 10 * (i + 1)
            store.due(url, now)
            store.observe(url, now, changed=True)
        self.assertGreaterEqual(store.due(url, 10**9)["interval"], 60)
        for i in range(50):
            now = 10**3 + 10 * i
            store.due(url, now)
            store.observe(url, now, changed=False)
        self.assertLessEqual(store.due(url, 10**9)["interval"], store.base_interval * 8)


class AliasAlertTest(unittest.TestCase):
    def test_alerts_and_export(self):
        store = LinkStore(alias_min_count=3)
        store.due("https://site/a?id=1", 100)
        for n in range(4):
            store.due(f"https://site/a?id=1&utm_campaign={n}", 200 + n)
        alerts = store.alerts("site")
        self.assertTrue(alerts)
        self.assertEqual(alerts[0]["alias_count"], 4)

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "aliases.csv")
            rows = store.export_aliases(path, site="site")
            self.assertEqual(len(rows), 1)
            with open(path, newline="", encoding="utf-8") as handle:
                written = list(csv.DictReader(handle))
            self.assertEqual(written[0]["alias_count"], "4")
            self.assertIn("utm_campaign", written[0]["samples"])

    def test_trial_alerts_by_day(self):
        store = LinkStore(alias_ratio_threshold=0.5)
        store.start_trial("trial-site")
        base = 0
        store.due("https://trial-site/x", base)
        for i in range(3):
            store.due(f"https://trial-site/x#c{i}", base + i)
        alerts = store.site_alerts("trial-site")
        self.assertTrue(alerts)
        self.assertGreaterEqual(alerts[0]["alias_ratio"], 0.5)


class RejudgeTest(unittest.TestCase):
    def test_rejudge_merges_history_under_new_rules(self):
        store = LinkStore()
        # "share" is not a default tracking parameter, so these start apart.
        store.due("https://r/1?share=fb", 100)
        store.due("https://r/1?share=tw", 100)
        self.assertEqual(len(store.alias_report("r")), 0)

        rules = NormalizeRules()
        rules.tracking_params = frozenset(rules.tracking_params | {"share"})
        report = store.rejudge(rules)
        self.assertEqual(report["after_targets"], 1)
        self.assertEqual(report["before_targets"], 2)
        self.assertEqual(report["merged"], 1)
        self.assertEqual(store.alias_report("r")[0]["alias_count"], 1)

    def test_rejudge_keeps_earliest_address_canonical(self):
        store = LinkStore()
        store.due("https://s/1?share=early", 100)
        store.due("https://s/1?share=late", 500)
        rules = NormalizeRules()
        rules.tracking_params = frozenset(rules.tracking_params | {"share"})
        store.rejudge(rules)
        report = store.alias_report("s")
        self.assertEqual(report[0]["address"], "https://s/1?share=early")

    def test_rejudge_can_split_when_rule_is_removed(self):
        store = LinkStore()
        rules = NormalizeRules()
        rules.tracking_params = frozenset(rules.tracking_params | {"share"})
        store.rejudge(rules)
        store.due("https://u/1?share=a", 100)
        store.due("https://u/1?share=b", 100)
        report = store.rejudge(NormalizeRules())
        self.assertEqual(report["after_targets"], 2)


class RolloutTest(unittest.TestCase):
    def test_legacy_site_keeps_old_behaviour(self):
        store = LinkStore()
        store.rollback("old-site")
        self.assertEqual(store.site_mode("old-site"), "legacy")
        first = store.due("https://old-site/p?utm_source=x", 100)
        second = store.due("https://old-site/p#top", 200)
        self.assertTrue(first["due"])
        self.assertTrue(second["due"])  # old behaviour: whole URL differs

    def test_trial_is_isolated_per_site(self):
        store = LinkStore()
        store.start_trial("one-site")
        store.due("https://one-site/p?utm_source=x", 100)
        alias = store.due("https://one-site/p#a", 200)
        self.assertFalse(alias["due"])
        metrics = store.daily_metrics("one-site")
        day = next(iter(metrics["one-site"].values()))
        self.assertEqual(day["alias_ratio"], 0.5)
        self.assertEqual(day["duplicate_fetches"], 1)

    def test_rollback_only_affects_one_site(self):
        store = LinkStore()
        store.due("https://keep/p?utm_source=x", 100)
        store.due("https://drop/p?utm_source=x", 100)
        store.rollback("drop")
        keep = store.due("https://keep/p#a", 200)
        drop = store.due("https://drop/p#a", 200)
        self.assertFalse(keep["due"])
        self.assertTrue(drop["due"])
        self.assertEqual(store.site_mode("keep"), "active")

    def test_promote_after_trial(self):
        store = LinkStore()
        store.start_trial("t")
        store.promote("t")
        self.assertEqual(store.site_mode("t"), "active")


class DailyMetricsTest(unittest.TestCase):
    def test_metrics_are_per_day(self):
        store = LinkStore()
        store.due("https://m/1", 0)
        store.due("https://m/1#a", 60)
        day_one = 0
        day_two = 86400
        store.due("https://m/2", day_two)
        metrics = store.daily_metrics("m")
        days = sorted(metrics["m"])
        self.assertEqual(len(days), 2)
        first = metrics["m"][days[0]]
        self.assertEqual(first["links_seen"], 2)
        self.assertEqual(first["aliases"], 1)
        self.assertAlmostEqual(first["alias_ratio"], 0.5)


class DefaultApiTest(unittest.TestCase):
    def test_module_level_due_still_works(self):
        result = due("https://api/1", 100)
        self.assertTrue(result["due"])
        self.assertEqual(result["fingerprint"], fingerprint("https://api/1"))


if __name__ == "__main__":
    unittest.main()
