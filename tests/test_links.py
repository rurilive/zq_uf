import unittest

import linksieve
from linksieve import (RuleSet, alias_alerts, alias_samples, daily_metrics,
                       due, explain, fingerprint, get_store, pilot_rules,
                       promote_rules, recanonicalize, record_fetch, reset,
                       rollback_rules)

DAY = 86400
T0 = 1_700_000_000  # 2023-11-14T22:13:20Z


class LinksTest(unittest.TestCase):
    def test_query_changes_fingerprint(self):
        self.assertNotEqual(fingerprint("https://a/p?x=1"), fingerprint("https://a/p?x=2"))

    def test_new_url_is_due(self):
        self.assertTrue(due("https://b/1", 100)["due"])

    def test_second_call_waits_interval(self):
        due("https://c/1", 100)
        self.assertFalse(due("https://c/1", 200)["due"])

    def test_after_interval_is_due(self):
        due("https://d/1", 100)
        self.assertTrue(due("https://d/1", 3700)["due"])


class NormalizeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_tracking_params_order_and_fragment_ignored(self):
        base = fingerprint("https://shop.com/p?a=1&b=2")
        self.assertEqual(base, fingerprint("https://shop.com/p?b=2&a=1&utm_source=x"))
        self.assertEqual(base, fingerprint("https://shop.com/p?a=1&b=2#gohere"))
        self.assertEqual(base, fingerprint("https://SHOP.com/p?a=1&b=2&gclid=9"))

    def test_path_and_pagination_kept(self):
        self.assertNotEqual(fingerprint("https://shop.com/l?page=1"),
                            fingerprint("https://shop.com/l?page=2"))
        self.assertNotEqual(fingerprint("https://shop.com/a"),
                            fingerprint("https://shop.com/b"))

    def test_explain_names_matched_rules(self):
        r = explain("https://shop.com/p?utm_source=x&b=2&a=1#top")
        self.assertIn("drop-tracking-param", r["rules"])
        self.assertIn("sort-params", r["rules"])
        self.assertIn("strip-fragment", r["rules"])
        self.assertEqual(r["ruleset"], "v1")
        self.assertTrue(r["why"])


class AliasTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_earliest_url_stays_canonical_rest_become_aliases(self):
        r1 = due("https://news.com/a?utm_source=x", T0)
        r2 = due("https://news.com/a?gclid=1", T0 + 10)
        self.assertEqual(r1["fingerprint"], r2["fingerprint"])
        self.assertEqual(r2["canonical"], "https://news.com/a?utm_source=x")
        self.assertTrue(r2["is_alias"])
        self.assertFalse(r2["due"])  # 同一目标共享一份调度

    def test_due_explains_why(self):
        due("https://news.com/hot", T0)
        r = due("https://news.com/hot", T0 + 100)
        self.assertEqual(r["reason"], "interval")
        self.assertIn("100s", r["why"])

    def test_alias_alert_and_samples(self):
        due("https://big.com/p", T0)
        for i in range(12):
            due("https://big.com/p?utm_source=s%d" % i, T0 + i)
        alerts = alias_alerts(ratio=0.5, min_aliases=10)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["site"], "big.com")
        self.assertEqual(alerts[0]["aliases"], 12)
        samples = alias_samples("big.com")
        self.assertEqual(samples[0]["alias_count"], 12)
        self.assertTrue(samples[0]["aliases"])


class AdaptiveIntervalTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_changing_page_comes_back_sooner(self):
        due("https://news.com/hot", T0)
        r = record_fetch("https://news.com/hot", T0, changed=True)
        self.assertEqual(r["interval"], 1800)
        record_fetch("https://news.com/hot", T0, changed=True)
        self.assertTrue(due("https://news.com/hot", T0 + 900)["due"])

    def test_static_page_backs_off(self):
        due("https://news.com/cold", T0)
        r = record_fetch("https://news.com/cold", T0, changed=False)
        self.assertEqual(r["interval"], 5400)
        self.assertFalse(due("https://news.com/cold", T0 + 3700)["due"])

    def test_alias_fetch_counts_as_duplicate(self):
        due("https://news.com/a", T0)
        record_fetch("https://news.com/a?utm_source=x", T0 + 1, changed=False)
        m = daily_metrics("news.com")
        day = list(m)[0]
        self.assertEqual(m[day]["duplicate_fetches"], 1)


class RuleRolloutTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_rejudge_history_after_rule_update(self):
        # 旧规则不丢任何参数，两个写法各算一条
        get_store().global_rules = RuleSet(version="v0", tracking_params=frozenset(),
                                           tracking_prefixes=())
        due("https://news.com/a?ref=x", T0)
        due("https://news.com/a", T0 + 5)
        self.assertEqual(len(get_store().targets), 2)
        # 升级规则后重新判定，合并为一个目标，最早入队的地址留下
        get_store().global_rules = RuleSet(version="v1")
        recanonicalize()
        self.assertEqual(len(get_store().targets), 1)
        tgt = list(get_store().targets.values())[0]
        self.assertEqual(tgt.canonical, "https://news.com/a?ref=x")
        self.assertEqual(tgt.aliases, {"https://news.com/a": T0 + 5})

    def test_pilot_one_site_then_rollback(self):
        strict = RuleSet(version="v2", tracking_params=frozenset({"sid"}))
        due("https://pilot.com/p?sid=1", T0)
        due("https://other.com/p?sid=1", T0)
        self.assertNotEqual(fingerprint("https://pilot.com/p?sid=1"),
                            fingerprint("https://pilot.com/p?sid=2"))
        pilot_rules("pilot.com", strict)
        # 试运行站点忽略 sid，其他站点不受影响
        self.assertEqual(fingerprint("https://pilot.com/p?sid=1"),
                         fingerprint("https://pilot.com/p?sid=2"))
        self.assertNotEqual(fingerprint("https://other.com/p?sid=1"),
                            fingerprint("https://other.com/p?sid=2"))
        rollback_rules("pilot.com")
        self.assertNotEqual(fingerprint("https://pilot.com/p?sid=1"),
                            fingerprint("https://pilot.com/p?sid=2"))

    def test_promote_after_pilot(self):
        strict = RuleSet(version="v2", tracking_params=frozenset({"sid"}))
        pilot_rules("pilot.com", strict)
        self.assertTrue(promote_rules("pilot.com"))
        self.assertEqual(fingerprint("https://other.com/p?sid=1"),
                         fingerprint("https://other.com/p?sid=2"))

    def test_daily_metrics_by_day(self):
        due("https://pilot.com/p", T0)
        due("https://pilot.com/p?utm_source=x", T0 + 1)
        due("https://pilot.com/p?utm_source=y", T0 + DAY)
        m = daily_metrics("pilot.com")
        days = sorted(m)
        self.assertEqual(len(days), 2)
        self.assertEqual(m[days[0]]["aliases"], 1)
        self.assertEqual(m[days[0]]["new_targets"], 1)
        self.assertEqual(m[days[0]]["alias_ratio"], 0.5)
        self.assertEqual(m[days[1]]["aliases"], 1)
        self.assertEqual(m[days[1]]["new_targets"], 0)


if __name__ == "__main__":
    unittest.main()
