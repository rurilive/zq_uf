import unittest

from seedscore import score


class ScoreTest(unittest.TestCase):
    def test_score_is_not_constant(self):
        self.assertNotEqual(score({"url": "https://a"})["score"], score({"url": "https://a"})["score"] + 0.1)

    def test_parts_are_explained(self):
        result = score(
            {
                "url": "https://shop.example.com/",
                "source": "manual",
                "history": {"successes": 10, "failures": 1},
            }
        )
        self.assertAlmostEqual(
            sum(
                result["weights"][key] * result["parts"][key]
                for key in ("source_credibility", "history_yield", "link_quality")
            ),
            result["score"],
            places=5,
        )
        self.assertTrue(result["notes"])

    def test_good_homepage_passes(self):
        result = score({"url": "https://news.example.org/", "source": "manual"})
        self.assertGreaterEqual(result["score"], result["threshold"])
        self.assertEqual(result["reason"], "ok")

    def test_spam_url_is_killed(self):
        result = score(
            {"url": "https://example.net/login/cart/checkout/1/2/3/4/5", "source": "referral"}
        )
        self.assertLess(result["score"], result["threshold"])
        self.assertFalse(result["passes_threshold"])
        self.assertEqual(result["reason"], "low_score")

    def test_trusted_source_beats_referral(self):
        seed = {"url": "https://site.example/page", "history": {"successes": 5, "attempts": 6}}
        trusted = score({**seed, "source": "manual"})
        referral = score({**seed, "source": "referral"})
        self.assertGreater(trusted["score"], referral["score"])

    def test_history_raises_score(self):
        seed = {"url": "https://news.example.org/", "source": "sitemap"}
        without = score(seed)
        with_history = score(
            {
                **seed,
                "history": {
                    "successes": 40,
                    "failures": 2,
                    "last_success": "2026-11-01T00:00:00Z",
                },
            }
        )
        self.assertGreater(with_history["parts"]["history_yield"], without["parts"]["history_yield"])
        self.assertGreater(with_history["score"], without["score"])

    def test_invalid_url_scores_zero_without_raising(self):
        result = score({"url": "not a url"})
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["reason"], "invalid_url")
        self.assertFalse(result["passes_threshold"])

    def test_source_label_from_config(self):
        config = {"sources": {"partner": "trusted"}}
        result = score({"url": "https://a.example/", "source": "partner"}, config)
        self.assertAlmostEqual(result["parts"]["source_credibility"], 0.9)

    def test_percent_credibility(self):
        result = score({"url": "https://a.example/", "source_credibility": 80}, {})
        self.assertAlmostEqual(result["parts"]["source_credibility"], 0.8)

    def test_weights_are_normalised(self):
        config = {"weights": {"source_credibility": 2, "history_yield": 1, "link_quality": 1}}
        result = score({"url": "https://a.example/"}, config)
        self.assertAlmostEqual(result["weights"]["source_credibility"], 0.5)
        self.assertAlmostEqual(sum(result["weights"].values()), 1.0)

    def test_per_site_threshold_and_bonus(self):
        seed = {"url": "https://example.net/casino/1/2/3/x", "source": "referral"}
        plain = score(seed)
        tuned = score(seed, {"sites": {"example.net": {"threshold": 0.3, "bonus": 0.1}}})
        self.assertGreater(tuned["score"], plain["score"])
        self.assertEqual(tuned["threshold"], 0.3)
        self.assertEqual(tuned["site_rule"], "example.net")

    def test_per_site_parts_override(self):
        config = {"sites": {"a.example": {"parts": {"link_quality": 1.0}}}}
        result = score({"url": "https://a.example/login/"}, config)
        self.assertEqual(result["parts"]["link_quality"], 1.0)

    def test_site_rule_suffix_match_is_longest(self):
        config = {
            "sites": {
                "example.com": {"threshold": 0.7},
                "news.example.com": {"threshold": 0.2},
            }
        }
        result = score({"url": "https://news.example.com/"}, config)
        self.assertEqual(result["threshold"], 0.2)
        self.assertEqual(result["site_rule"], "news.example.com")

    def test_site_wildcard(self):
        result = score({"url": "https://x.example/"}, {"sites": {"*": {"threshold": 0.9}}})
        self.assertEqual(result["threshold"], 0.9)
        self.assertEqual(result["site_rule"], "*")

    def test_site_glob_matches_subdomains_only(self):
        config = {"sites": {"*.blog.example": {"threshold": 0.9}}}
        self.assertEqual(score({"url": "https://a.blog.example/"}, config)["threshold"], 0.9)
        # The bare domain is not a subdomain and falls back to the global rule.
        self.assertEqual(score({"url": "https://blog.example/"}, config)["threshold"], 0.5)

    def test_exact_host_beats_glob(self):
        config = {
            "sites": {
                "*.blog.example": {"threshold": 0.9},
                "x.blog.example": {"threshold": 0.2},
            }
        }
        self.assertEqual(score({"url": "https://x.blog.example/"}, config)["threshold"], 0.2)
        self.assertEqual(score({"url": "https://y.blog.example/"}, config)["threshold"], 0.9)

    def test_dead_status_reported(self):
        result = score({"url": "https://gone.example/page", "status": "dead"})
        self.assertTrue(result["dead"])
        self.assertEqual(result["reason"], "dead")
        self.assertIn("status:dead", result["dead_reasons"])

    def test_expired_seed(self):
        result = score(
            {"url": "https://gone.example/page", "expires_at": "2026-01-01T00:00:00Z"},
        )
        self.assertTrue(result["dead"])
        self.assertTrue(any(reason.startswith("expired") for reason in result["dead_reasons"]))

    def test_config_is_not_mutated(self):
        config = {"sites": {"a.example": {"threshold": 0.1}}}
        score({"url": "https://a.example/"}, config)
        self.assertEqual(config, {"sites": {"a.example": {"threshold": 0.1}}})

    def test_input_seed_is_not_mutated(self):
        seed = {"url": "https://a.example/", "source": "manual"}
        score(seed)
        self.assertEqual(seed, {"url": "https://a.example/", "source": "manual"})


if __name__ == "__main__":
    unittest.main()
