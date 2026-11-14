import time
import unittest

from uafilter import headers, report, stats
from uafilter import ua


class UaTest(unittest.TestCase):
    def setUp(self):
        ua._stats.clear()

    def test_headers_has_ua(self):
        self.assertIn("User-Agent", headers("shop"))

    def test_same_headers_all_sites(self):
        self.assertEqual(headers("shop"), headers("news"))

    def test_report_counts(self):
        report("shop", 403)
        self.assertEqual(report("shop", 403)[403], 2)


class PoolTest(unittest.TestCase):
    def setUp(self):
        ua._stats.clear()
        ua._sites.clear()

    def tearDown(self):
        ua._sites.clear()
        ua._stats.clear()
        ua._now = time.monotonic

    def test_site_specific_pool(self):
        h = headers("video")
        self.assertIn(h["User-Agent"], ua._SITE_CONFIG["video"]["user_agents"])
        self.assertEqual(
            h["Accept-Language"], ua._SITE_CONFIG["video"]["accept_language"]
        )

    def test_language_encoding_fallback_global(self):
        h = headers("forum")
        self.assertEqual(
            h["Accept-Language"], ua._SITE_CONFIG["forum"]["accept_language"]
        )
        self.assertEqual(
            h["Accept-Encoding"], ua._GLOBAL_CONFIG["accept_encoding"]
        )

    def test_encoding_matches_site_config(self):
        self.assertEqual(
            headers("video")["Accept-Encoding"],
            ua._SITE_CONFIG["video"]["accept_encoding"],
        )
        self.assertEqual(
            headers("shop")["Accept-Encoding"],
            ua._GLOBAL_CONFIG["accept_encoding"],
        )

    def test_switch_after_consecutive_403(self):
        first = headers("shop")["User-Agent"]
        for _ in range(ua._CONSECUTIVE_403_LIMIT):
            report("shop", 403)
        self.assertNotEqual(headers("shop")["User-Agent"], first)
        record = ua._sites["shop"]["switches"][-1]
        self.assertEqual(record["from"], first)
        self.assertIn("403", record["reason"])

    def test_single_403_no_switch(self):
        first = headers("shop")["User-Agent"]
        report("shop", 403)
        report("shop", 403)
        self.assertEqual(headers("shop")["User-Agent"], first)

    def test_success_resets_consecutive_403(self):
        first = headers("shop")["User-Agent"]
        report("shop", 403)
        report("shop", 403)
        report("shop", 200)
        report("shop", 403)
        report("shop", 403)
        self.assertEqual(headers("shop")["User-Agent"], first)

    def test_response_feature_triggers_switch(self):
        first = headers("shop")["User-Agent"]
        report("shop", 403, response="Please complete the CAPTCHA to continue")
        self.assertNotEqual(headers("shop")["User-Agent"], first)
        self.assertIn("captcha", ua._sites["shop"]["switches"][-1]["reason"])

    def test_429_triggers_switch(self):
        first = headers("shop")["User-Agent"]
        report("shop", 429)
        self.assertNotEqual(headers("shop")["User-Agent"], first)

    def test_cooldown_skips_banned_ua(self):
        fake = [1000.0]
        ua._now = lambda: fake[0]
        agents = ua._GLOBAL_CONFIG["user_agents"]
        for _ in range(len(agents)):
            report("shop", 403, response="access denied")
        current = headers("shop")["User-Agent"]
        # 全部 UA 都在冷却，维持当前不切换
        report("shop", 403, response="access denied")
        self.assertEqual(headers("shop")["User-Agent"], current)
        self.assertEqual(len(ua._sites["shop"]["bans"]), len(agents))
        # 冷却结束后最早被封的 UA 可重新启用
        fake[0] += ua._COOLDOWN_SECONDS + 1
        report("shop", 403, response="access denied")
        self.assertEqual(headers("shop")["User-Agent"], agents[0])

    def test_stats_output(self):
        report("shop", 403, response="access denied")
        s = stats("shop")["shop"]
        self.assertEqual(s["headers"], headers("shop"))
        self.assertEqual(len(s["switches"]), 1)
        self.assertEqual(sum(s["bans"].values()), 1)
        self.assertTrue(s["cooldowns"])


if __name__ == "__main__":
    unittest.main()
