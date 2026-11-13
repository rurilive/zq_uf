import unittest

from siteweight import (
    BASE_WEIGHT,
    FAIL_THRESHOLD,
    MIN_SAMPLES,
    MIN_WEIGHT,
    RECOVER_THRESHOLD,
    STARVATION_LIMIT,
    SITES,
    SiteWeightError,
    UnknownSiteError,
    feedback,
    next,
    next_site,
    reset,
    stats,
    weight_changes,
    weights,
)
import siteweight.weight as weight_module


class WeightTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_round_robin(self):
        picked = [next_site() for _ in range(6)]
        self.assertEqual(picked, SITES * 2)

    def test_feedback_does_not_reorder(self):
        feedback("shop", False)
        feedback("shop", False)
        self.assertEqual(next_site(), "shop")

    def test_all_sites_equal(self):
        counts = {site: 0 for site in SITES}
        for _ in range(30):
            counts[next_site()] += 1
        self.assertEqual(set(counts.values()), {10})

    def test_next_alias_matches_next_site(self):
        self.assertIs(next, next_site)


class DynamicWeightTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_cold_start_keeps_base_weight(self):
        self.assertEqual(weights(), {site: BASE_WEIGHT for site in SITES})
        for _ in range(MIN_SAMPLES - 1):
            feedback("shop", False)
        self.assertEqual(weights()["shop"], BASE_WEIGHT)
        self.assertEqual(weight_changes(), [])

    def test_weight_adjusts_with_success_rate(self):
        for _ in range(MIN_SAMPLES):
            feedback("shop", True)
        for _ in range(MIN_SAMPLES):
            feedback("news", False)
        current = weights()
        self.assertEqual(current["shop"], BASE_WEIGHT)
        self.assertEqual(current["news"], MIN_WEIGHT)
        self.assertEqual(current["blog"], BASE_WEIGHT)
        changes = weight_changes()
        self.assertTrue(any(e["site"] == "news" for e in changes))
        news_events = [e for e in changes if e["site"] == "news"]
        news_event = news_events[0]
        self.assertEqual(
            (news_event["old"], news_event["new"]),
            (BASE_WEIGHT, MIN_WEIGHT),
        )
        self.assertEqual(news_event["reason"], "consecutive_failures")

    def test_frequency_factor_raises_active_sites(self):
        for _ in range(MIN_SAMPLES):
            feedback("shop", True)
        for _ in range(MIN_SAMPLES):
            feedback("news", True)
        self.assertEqual(weights()["shop"], BASE_WEIGHT)
        self.assertEqual(weights()["news"], BASE_WEIGHT)
        for _ in range(5):
            feedback("shop", True)
        self.assertGreater(weights()["shop"], weights()["news"])
        self.assertEqual(weights()["shop"], BASE_WEIGHT)

    def test_demotion_and_recovery(self):
        for _ in range(FAIL_THRESHOLD):
            feedback("news", False)
        demoted = weights()["news"]
        self.assertLess(demoted, BASE_WEIGHT)
        self.assertTrue(stats()["sites"]["news"]["penalized"])
        for _ in range(RECOVER_THRESHOLD):
            feedback("news", True)
        self.assertFalse(stats()["sites"]["news"]["penalized"])
        self.assertGreater(weights()["news"], demoted)
        reasons = [e["reason"] for e in weight_changes() if e["site"] == "news"]
        self.assertIn("consecutive_failures", reasons)
        self.assertIn("recovered", reasons)
        self.assertEqual(reasons.index("consecutive_failures"), 0)

    def test_recovery_needs_full_success_streak(self):
        for _ in range(FAIL_THRESHOLD):
            feedback("blog", False)
        penalized_weight = weights()["blog"]
        feedback("blog", True)
        self.assertTrue(stats()["sites"]["blog"]["penalized"])
        self.assertEqual(weights()["blog"], penalized_weight)
        feedback("blog", False)
        self.assertTrue(stats()["sites"]["blog"]["penalized"])
        for _ in range(RECOVER_THRESHOLD):
            feedback("blog", True)
        self.assertFalse(stats()["sites"]["blog"]["penalized"])

    def test_sliding_window_caps_history(self):
        for _ in range(weight_module.WINDOW_SIZE):
            feedback("shop", False)
        self.assertEqual(
            stats()["sites"]["shop"]["feedback"],
            weight_module.WINDOW_SIZE,
        )
        for _ in range(5):
            feedback("shop", True)
        self.assertEqual(
            stats()["sites"]["shop"]["feedback"],
            weight_module.WINDOW_SIZE,
        )
        self.assertGreater(weights()["shop"], MIN_WEIGHT)

    def test_feedback_returns_total_count(self):
        self.assertEqual(feedback("shop", True), 1)
        self.assertEqual(feedback("news", False), 2)


class GuaranteeAndStatsTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_wait_stats_equal_weight(self):
        for _ in range(6):
            next_site()
        for site in SITES:
            site_stats = stats()["sites"][site]
            self.assertEqual(site_stats["picks"], 2)
            self.assertEqual(site_stats["max_wait"], 3)
        expected_avg = {"shop": 2.0, "news": 2.5, "blog": 3.0}
        for site, expected in expected_avg.items():
            self.assertEqual(stats()["sites"][site]["avg_wait"], expected)
        self.assertEqual(stats()["sites"][SITES[-1]]["current_wait"], 0)
        self.assertEqual(stats()["total_picks"], 6)

    def test_low_weight_keeps_floor_share(self):
        for _ in range(FAIL_THRESHOLD):
            feedback("blog", False)
        counts = {site: 0 for site in SITES}
        for _ in range(30):
            counts[next_site()] += 1
        self.assertGreater(counts["blog"], 0)
        self.assertGreaterEqual(counts["blog"], 4)
        self.assertLess(counts["blog"], counts["shop"])
        for site in SITES:
            self.assertLessEqual(
                stats()["sites"][site]["max_wait"],
                STARVATION_LIMIT,
            )
        self.assertGreater(stats()["starvation_picks"], 0)

    def test_starvation_force_picks_fairly(self):
        for _ in range(FAIL_THRESHOLD):
            feedback("news", False)
        forced = []
        before = stats()["starvation_picks"]
        for _ in range(24):
            next_site()
            after = stats()["starvation_picks"]
            if after > before:
                forced.append(stats()["total_picks"])
                before = after
        self.assertTrue(forced)
        self.assertEqual(stats()["sites"]["news"]["max_wait"], STARVATION_LIMIT)


class ErrorPathTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_feedback_unknown_site(self):
        with self.assertRaises(UnknownSiteError):
            feedback("ghost", True)
        self.assertEqual(weight_changes(), [])
        self.assertEqual(stats()["total_picks"], 0)

    def test_feedback_non_bool_result(self):
        for bad in (None, 1, 0, "true", ()):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    feedback("shop", bad)
        self.assertEqual(stats()["sites"]["shop"]["feedback"], 0)

    def test_next_with_empty_sites(self):
        original = weight_module.SITES[:]
        weight_module.SITES.clear()
        try:
            with self.assertRaises(SiteWeightError):
                next_site()
        finally:
            weight_module.SITES.extend(original)
            reset()

    def test_reset_clears_everything(self):
        feedback("shop", False)
        next_site()
        reset()
        self.assertEqual(stats()["total_picks"], 0)
        self.assertEqual(weight_changes(), [])
        self.assertEqual(weights(), {site: BASE_WEIGHT for site in SITES})
