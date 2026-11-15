import unittest

from linksieve import LinkStore


class FingerprintRulesTest(unittest.TestCase):
    def setUp(self):
        self.store = LinkStore()

    def same(self, a, b):
        self.assertEqual(self.store.fingerprint(a), self.store.fingerprint(b), (a, b))

    def different(self, a, b):
        self.assertNotEqual(self.store.fingerprint(a), self.store.fingerprint(b), (a, b))

    def test_tracking_params_ignored(self):
        self.same("https://a/p?utm_source=x&utm_medium=y", "https://a/p")
        self.same("https://a/p?fbclid=1&gclid=2", "https://a/p")
        self.same("https://a/p?mtm_campaign=z", "https://a/p")

    def test_parameter_order_ignored(self):
        self.same("https://a/p?id=3&sort=asc", "https://a/p?sort=asc&id=3")

    def test_fragment_ignored(self):
        self.same("https://a/p#intro", "https://a/p")
        self.same("https://a/p#comments", "https://a/p#intro")

    def test_path_preserved(self):
        self.different("https://a/p", "https://a/q")
        self.different("https://a/P", "https://a/p")

    def test_pagination_preserved(self):
        self.different("https://a/p?page=1", "https://a/p?page=2")
        self.same("https://a/p?page=2", "https://a/p?page=2&utm_source=x")
        self.different("https://a/list?page=1", "https://a/list?page=2")

    def test_pagination_not_treated_as_tracking(self):
        self.different("https://a/p?start=0", "https://a/p?start=10")

    def test_business_params_preserved(self):
        self.different("https://a/p?ref=1", "https://a/p?ref=2")
        self.different("https://a/p?source=news", "https://a/p?source=email")

    def test_host_case_and_default_port_ignored(self):
        self.same("https://A.Example/p", "https://a.example/p")
        self.same("https://a.example:443/p", "https://a.example/p")
        self.different("http://a.example/p", "https://a.example/p")

    def test_trailing_slash_ignored(self):
        self.same("https://a.example/p/", "https://a.example/p")
        self.same("https://a.example", "https://a.example/")

    def test_empty_url_is_stable(self):
        self.assertEqual(self.store.fingerprint(""), self.store.fingerprint(None))
        self.assertEqual(self.store.fingerprint(""), self.store.fingerprint("  "))


class DueResultTest(unittest.TestCase):
    def setUp(self):
        self.store = LinkStore()

    def test_new_target_is_due_with_rules(self):
        result = self.store.due("https://s/p?utm_source=x#top", 100)
        self.assertTrue(result["due"])
        self.assertEqual(result["reason"], "new")
        self.assertIn("tracking_param:utm_source", result["rules_hit"])
        self.assertIn("fragment_ignored", result["rules_hit"])
        self.assertEqual(result["canonical"], "https://s/p")
        self.assertEqual(result["ruleset"], "content-v1")

    def test_alias_keeps_first_url(self):
        first = self.store.due("https://s/p?utm_source=a", 100)
        alias = self.store.due("https://s/p?utm_source=b", 200)
        self.assertFalse(first["is_alias"])
        self.assertTrue(alias["is_alias"])
        self.assertEqual(alias["url"], "https://s/p?utm_source=b")
        self.assertEqual(alias["kept_url"], "https://s/p?utm_source=a")
        self.assertEqual(alias["alias_of"], "https://s/p?utm_source=a")
        self.assertEqual(alias["fingerprint"], first["fingerprint"])

    def test_second_call_waits_interval_and_explains(self):
        self.store.due("https://s/p", 100)
        result = self.store.due("https://s/p", 200)
        self.assertFalse(result["due"])
        self.assertEqual(result["reason"], "interval_not_elapsed")
        self.assertIn("remaining", result["explanation"])
        self.assertEqual(result["next_due_at"], 3700)

    def test_due_after_interval(self):
        self.store.due("https://s/p", 100)
        result = self.store.due("https://s/p", 3700)
        self.assertTrue(result["due"])
        self.assertEqual(result["reason"], "interval_elapsed")

    def test_alias_does_not_reset_interval(self):
        self.store.due("https://s/p", 100)
        self.store.due("https://s/p?utm_source=x", 3500)
        result = self.store.due("https://s/p?utm_source=y", 3700)
        self.assertTrue(result["due"])

    def test_explain_does_not_mutate(self):
        self.store.due("https://s/p?utm_source=a", 100)
        self.assertFalse(self.store.explain("https://s/p?utm_source=a")["is_alias"])
        self.assertTrue(self.store.explain("https://s/p#x")["is_alias"])
        self.assertEqual(len(self.store._targets), 1)


class AdaptiveIntervalTest(unittest.TestCase):
    def setUp(self):
        self.store = LinkStore()

    def test_changing_page_returns_sooner(self):
        self.store.due("https://s/hot", 0)
        self.store.observe("https://s/hot", "v1", 0)
        self.store.due("https://s/hot", 3600)
        self.store.observe("https://s/hot", "v2", 3600)
        self.store.due("https://s/hot", 7200)
        result = self.store.observe("https://s/hot", "v3", 7200)
        self.assertEqual(result["interval_after"], 900)
        self.assertTrue(result["changed"])

    def test_stable_page_returns_later(self):
        self.store.due("https://s/stable", 0)
        self.store.observe("https://s/stable", "v1", 0)
        self.store.due("https://s/stable", 3600)
        result = self.store.observe("https://s/stable", "v1", 3600)
        self.assertFalse(result["changed"])
        self.assertEqual(result["interval_after"], 5400)

    def test_interval_bounds(self):
        self.store.due("https://s/hot", 0)
        self.store.observe("https://s/hot", "v0", 0)
        digest = 1
        for step in range(1, 12):
            self.store.due("https://s/hot", step * 100000)
            self.store.observe("https://s/hot", f"v{step}", step * 100000)
        self.assertEqual(self.store._targets[
            self.store.fingerprint("https://s/hot")].interval, 300.0)

        self.store.due("https://s/cold", 0)
        self.store.observe("https://s/cold", "same", 0)
        now = 0
        for _ in range(30):
            now += 10 ** 6
            self.store.due("https://s/cold", now)
            self.store.observe("https://s/cold", "same", now)
        self.assertEqual(self.store._targets[
            self.store.fingerprint("https://s/cold")].interval, 604800.0)

    def test_observe_unknown_url(self):
        self.assertFalse(self.store.observe("https://s/none", "v1")["known"])


class RejudgeTest(unittest.TestCase):
    def test_new_rules_merge_history(self):
        store = LinkStore()
        store.due("https://s/p?utm_source=a", 100)
        store.due("https://s/p?utm_source=b", 200)
        store.due("https://s/q", 300)
        # 放宽规则：把 "ref" 也视为追踪参数后，两条历史目标合并。
        store.due("https://s/p?ref=x", 400)  # 目前是新目标
        summary = store.set_rules(
            store.default_rules.evolve(
                version="content-v2",
                tracking_params=store.default_rules.tracking_params | {"ref"},
            )
        )
        self.assertEqual(summary["targets_before"], 3)
        self.assertEqual(summary["targets_after"], 2)
        self.assertEqual(summary["merged"], 1)

    def test_rejudge_keeps_earliest_url_and_history(self):
        store = LinkStore()
        store.due("https://s/p?utm_source=b", 100)
        store.due("https://s/p?utm_source=a", 200)
        store.due("https://s/p?utm_source=c", 3700)
        store.observe("https://s/p?utm_source=a", "v1", 3700)
        result = store.explain("https://s/p", 3700)
        self.assertEqual(result["kept_url"], "https://s/p?utm_source=b")
        target = store._targets[result["fingerprint"]]
        self.assertEqual(target.fetches, 2)
        store.rejudge()
        self.assertEqual(
            store.explain("https://s/p")["kept_url"], "https://s/p?utm_source=b"
        )

    def test_rejudge_can_split_targets(self):
        store = LinkStore()
        store.due("https://s/p?a=1&b=2", 100)
        store.due("https://s/p?b=2&a=1", 200)
        self.assertEqual(len(store._targets), 1)
        summary = store.set_rules(
            store.default_rules.evolve(
                version="content-v2", sort_query=False
            )
        )
        self.assertEqual(summary["targets_before"], 1)
        self.assertEqual(summary["targets_after"], 2)
        self.assertEqual(summary["merged"], 0)
        self.assertEqual(summary["split"], 1)


class CanaryTest(unittest.TestCase):
    def setUp(self):
        self.store = LinkStore()
        for i in range(6):
            self.store.due(f"https://hot/p?utm_source={i}", 100)

    def test_canary_only_affects_one_site(self):
        self.store.start_canary("other.test", tracking_prefixes=())
        self.assertEqual(self.store.rules_for("other.test").version,
                         "content-v1+canary")
        self.assertEqual(self.store.rules_for("hot").version, "content-v1")

    def test_rollback_restores_only_that_site(self):
        self.store.start_canary("hot", tracking_prefixes=())
        self.assertEqual(self.store.rules_for("hot").version, "content-v1+canary")
        summary = self.store.rollback_site("hot")
        self.assertIn("canary", summary["rules_removed"])
        self.assertEqual(self.store.rules_for("hot").version, "content-v1")
        self.assertEqual(
            len({store for store in self.store.metrics()} - {"hot"}), 0
        )

    def test_promote_keeps_rules_for_site(self):
        self.store.start_canary("hot", strip_trailing_slash=False)
        self.store.promote_site("hot")
        self.assertEqual(self.store.rules_for("hot").version, "content-v1+canary")
        self.assertEqual(self.store.rules_for("elsewhere").version, "content-v1")


class AliasAlertTest(unittest.TestCase):
    def setUp(self):
        self.store = LinkStore(alert_min_aliases=3, alert_min_ratio=0.5)

    def test_alias_alert_and_samples(self):
        self.store.due("https://spam/p", 100)
        for i in range(6):
            self.store.due(f"https://spam/p?utm_source={i}", 100 + i)
        alerts = self.store.alias_alerts()
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["site"], "spam")
        self.assertEqual(alert["alias_variants"], 6)
        self.assertTrue(alert["samples"])
        sample = alert["samples"][0]
        self.assertEqual(sample["kept_url"], "https://spam/p")
        self.assertEqual(len(sample["aliases"]), 6)
        self.assertIn("tracking_param:utm_source", sample["aliases"][0]["rules_hit"])

    def test_no_alert_below_threshold(self):
        self.store.due("https://quiet/p?utm_source=1", 100)
        self.store.due("https://quiet/p?utm_source=2", 100)
        self.assertEqual(self.store.alias_alerts(
            min_aliases=3, min_ratio=0.5), [])

    def test_export_samples(self):
        import json
        import os
        import tempfile

        self.store.due("https://spam/p", 100)
        for i in range(4):
            self.store.due(f"https://spam/p?utm_source={i}", 101 + i)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nested", "aliases.json")
            payload = self.store.export_alias_samples(path)
            with open(path, encoding="utf-8") as handle:
                on_disk = json.load(handle)
        self.assertEqual(on_disk["alerts"], payload["alerts"])
        self.assertTrue(on_disk["samples"])


class DailyMetricsTest(unittest.TestCase):
    def test_daily_alias_ratio_and_duplicate_fetches(self):
        store = LinkStore()
        day1 = 1_700_000_000  # 固定时间，避免跨日
        store.due("https://m/p", day1)
        store.due("https://m/p?utm_source=1", day1 + 10)
        store.due("https://m/p?utm_source=2", day1 + 20)
        store.due("https://m/p", day1 + 3600)
        store.due("https://m/p?utm_source=3", day1 + 3601)

        series = store.daily_series("m")
        self.assertEqual(len(series), 1)
        row = series[0]
        self.assertEqual(row["urls_seen"], 4)
        self.assertEqual(row["alias_urls"], 3)
        self.assertAlmostEqual(row["alias_ratio"], 0.75, places=4)
        self.assertEqual(row["fetches"], 2)
        self.assertEqual(row["duplicate_fetches"], 1)

    def test_metrics_per_site(self):
        store = LinkStore()
        now = 1_700_000_000
        store.due("https://a/p", now)
        store.due("https://b/p", now)
        store.due("https://b/p?utm_source=x", now)
        metrics = store.metrics()
        self.assertEqual(metrics["a"][list(metrics["a"])[0]]["alias_ratio"], 0.0)
        self.assertEqual(metrics["b"][list(metrics["b"])[0]]["alias_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()
