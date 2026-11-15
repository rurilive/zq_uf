import unittest

from seedscore import shadow


class ShadowTest(unittest.TestCase):
    def setUp(self):
        self.seeds = [
            {"url": "https://news.example.org/", "source": "manual"},
            {"url": "https://shop.example.com/", "source": "sitemap"},
            {
                "url": "https://blog.example.net/",
                "source": "other",
                "is_new": True,
            },
            {"url": "https://junk.example/login/cart/1/2/3/4/5", "source": "referral"},
        ]

    def test_no_reference(self):
        report = shadow(self.seeds)
        self.assertEqual(report["verdict"], "no_reference")
        self.assertEqual(report["counts"]["human_kept"], 0)

    def test_safe_when_human_matches(self):
        report = shadow(
            self.seeds,
            human=["https://news.example.org/", "https://shop.example.com/"],
            sample_size=10,
        )
        self.assertEqual(report["verdict"], "safe", report["false_kills"])
        self.assertEqual(report["counts"]["false_kills"], 0)
        self.assertEqual(report["counts"]["missed"], 0)
        self.assertAlmostEqual(report["recall"], 1.0)

    def test_false_kill_blocks_enable(self):
        report = shadow(
            self.seeds,
            human=[
                "https://news.example.org/",
                "https://junk.example/login/cart/1/2/3/4/5",
            ],
            sample_size=10,
        )
        self.assertEqual(report["verdict"], "blocked")
        self.assertEqual(report["counts"]["false_kills"], 1)
        self.assertEqual(report["false_kills"][0]["target"], "junk.example/login/cart/1/2/3/4/5")
        self.assertIn("score", report["false_kills"][0])
        self.assertIn("notes", report["false_kills"][0])

    def test_new_site_false_kills_reported_separately(self):
        seeds = [
            {"url": "https://ok.example/", "source": "manual"},
            {"url": "https://fresh.example/deep/a/b/c", "source": "referral", "is_new": True},
        ]
        report = shadow(seeds, human=["https://ok.example/", "https://fresh.example/deep/a/b/c"])
        self.assertEqual(report["counts"]["false_kills"], 1)
        self.assertEqual(len(report["new_site_false_kills"]), 1)
        self.assertTrue(report["new_site_false_kills"][0]["new_site"])

    def test_insufficient_coverage_when_risky_new_sites_unreviewed(self):
        # A new site below threshold is at risk of being a false kill, but the
        # reviewed sample does not include it, so absence of a human reference
        # for it is not proof that it is safe to enable.
        seeds = [
            {"url": "https://news.example.org/", "source": "manual"},
            {
                "url": "https://fresh.example/login/cart/1/2/3",
                "source": "referral",
                "is_new": True,
            },
        ]
        report = shadow(seeds, human=["https://news.example.org/"], sample_size=0)
        self.assertEqual(report["verdict"], "insufficient_coverage")
        self.assertGreater(report["counts"]["unreviewed_new"], 0)
        self.assertEqual(report["false_kills"], [])

    def test_safe_once_risky_new_sites_are_reviewed(self):
        seeds = [
            {"url": "https://news.example.org/", "source": "manual"},
            {
                "url": "https://fresh.example/login/cart/1/2/3",
                "source": "referral",
                "is_new": True,
            },
        ]
        report = shadow(seeds, human=["https://news.example.org/"], sample_size=10)
        self.assertEqual(report["verdict"], "safe", report["verdict_reason"])
        self.assertGreater(report["sample"]["sample_new_sites"], 0)

    def test_dead_human_pick_is_not_a_scoring_false_kill(self):
        seeds = [
            {"url": "https://ok.example/", "source": "manual"},
            {"url": "https://dead.example/", "status": "dead"},
        ]
        report = shadow(seeds, human=["https://ok.example/", "https://dead.example/"])
        self.assertEqual(report["counts"]["false_kills"], 0)
        self.assertEqual(report["counts"]["dead_reference"], 1)
        self.assertEqual(report["verdict"], "safe")

    def test_sampling_is_deterministic(self):
        first = shadow(self.seeds, human=[], sample_size=2, seed=7)
        second = shadow(self.seeds, human=[], sample_size=2, seed=7)
        self.assertEqual(first["sampled"], second["sampled"])

    def test_shadow_does_not_admit(self):
        report = shadow(self.seeds, human=[])
        self.assertNotIn("kept", report)
        self.assertEqual(report["mode"], "shadow")

    def test_duplicate_targets_count_once(self):
        seeds = [
            {"url": "https://a.example/", "source": "manual"},
            {"url": "https://www.a.example/?utm_source=x", "source": "crawl"},
        ]
        report = shadow(seeds, human=["https://a.example/"])
        self.assertEqual(report["counts"]["shadow_kept"], 1)
        self.assertEqual(report["counts"]["human_kept"], 1)


if __name__ == "__main__":
    unittest.main()
