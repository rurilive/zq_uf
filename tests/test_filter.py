import unittest

from seedscore import filter


class FilterTest(unittest.TestCase):
    def test_keeps_and_drops(self):
        result = filter(
            [
                {"url": "https://news.example.org/", "source": "manual"},
                {"url": "https://junk.example/login/cart/1/2/3/4/5", "source": "referral"},
            ]
        )
        self.assertEqual([item["url"] for item in result["kept"]], ["https://news.example.org/"])
        self.assertEqual([item["reason"] for item in result["dropped"]], ["low_score"])

    def test_duplicate_targets_keep_highest_score(self):
        result = filter(
            [
                {"url": "https://shop.example.com/", "source": "crawl"},
                {"url": "https://www.shop.example.com/?utm_source=x", "source": "manual"},
            ]
        )
        kept = result["kept"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["source"], "manual")
        self.assertEqual(len(result["duplicates"]), 1)
        self.assertEqual(result["dropped"][0]["reason"], "duplicate")
        self.assertEqual(result["summary"]["duplicates"], 1)

    def test_duplicate_from_different_sources(self):
        result = filter(
            [
                {"url": "https://a.example/page?id=1&utm_medium=email", "source": "rss"},
                {"url": "https://a.example/page?id=1", "source": "manual"},
            ]
        )
        self.assertEqual(result["summary"]["duplicates"], 1)
        self.assertEqual(result["kept"][0]["source"], "manual")

    def test_dead_seeds_are_separated(self):
        result = filter(
            [
                {"url": "https://ok.example/", "source": "manual"},
                {"url": "https://dead.example/", "status": "dead"},
                {"url": "https://gone.example/", "http_status": 410},
            ]
        )
        self.assertEqual(len(result["kept"]), 1)
        self.assertEqual(len(result["dead"]), 2)
        self.assertTrue(all(item["reason"] == "dead" for item in result["dead"]))
        self.assertTrue(all(item["dead_reasons"] for item in result["dead"]))

    def test_dead_target_removed_from_queue(self):
        seeds = [{"url": "https://dead.example/", "status": "dead"}]
        result = filter(seeds, queue=["dead.example/"])
        self.assertEqual(result["kept"], [])
        self.assertEqual(len(result["queue_removed"]), 1)
        self.assertTrue(result["queue_removed"][0]["removed_from_queue"])
        self.assertEqual(result["summary"]["queue_removed"], 1)

    def test_live_variant_keeps_target_in_queue(self):
        # A dead duplicate must not drop a target that another live seed still
        # supplies; otherwise a stale seed would evict a working one.
        seeds = [
            {"url": "https://x.example/page", "status": "dead"},
            {"url": "https://www.x.example/page/", "source": "manual"},
        ]
        result = filter(seeds, queue=["x.example/page"])
        self.assertEqual(result["queue_removed"], [])
        self.assertEqual(len(result["kept"]), 1)
        self.assertEqual(result["kept"][0]["admission"], "existing")
        self.assertEqual(len(result["dead"]), 1)

    def test_existing_queue_order_is_stable(self):
        seeds = [
            {"url": "https://a.example/", "source": "manual"},
            {"url": "https://b.example/", "source": "manual"},
            {"url": "https://c.example/", "source": "manual"},
        ]
        first = filter(seeds)
        order = [item["target"] for item in first["kept"]]
        # Deliberately reverse the input and change scores: the live queue wins.
        changed = [
            {**seed, "source": "referral", "history": {"successes": 1, "attempts": 9}}
            for seed in reversed(seeds)
        ]
        second = filter(changed, queue=order)
        self.assertEqual([item["target"] for item in second["kept"]], order)
        self.assertFalse(second["changes"]["order_changed"] if second.get("changes") else False)

    def test_existing_target_below_threshold_is_retained(self):
        seed = {"url": "https://a.example/"}
        order = [item["target"] for item in filter([seed])["kept"]]
        config = {"threshold": 0.99}
        result = filter([seed], config, queue=order)
        self.assertEqual(len(result["kept"]), 1)
        self.assertEqual(result["kept"][0]["admission"], "existing")
        self.assertIn(
            "existing_queue:below_threshold_retained", result["kept"][0]["notes"]
        )

    def test_new_targets_sorted_by_score(self):
        seeds = [
            {"url": "https://weak.example/page?a=1&b=2&c=3&d=4&e=5"},
            {"url": "https://strong.example/", "source": "manual"},
        ]
        result = filter(seeds)
        self.assertEqual(result["kept"][0]["url"], "https://strong.example/")

    def test_limit_never_evicts_existing(self):
        seeds = [{"url": f"https://s{i}.example/", "source": "manual"} for i in range(3)]
        first = filter(seeds, limit=2)
        self.assertEqual(len(first["kept"]), 2)
        order = [item["target"] for item in first["kept"]]
        second = filter(seeds, limit=1, queue=order)
        self.assertEqual(len(second["kept"]), 2)
        self.assertTrue(all(item["admission"] == "existing" for item in second["kept"]))
        self.assertEqual(second["summary"]["limited"], 1)

    def test_invalid_url_dropped(self):
        result = filter(["not a url", {"url": "https://ok.example/", "source": "manual"}])
        self.assertEqual(result["summary"]["invalid"], 1)
        invalid = [item for item in result["dropped"] if item["reason"] == "invalid_url"]
        self.assertEqual(len(invalid), 1)

    def test_report_shape(self):
        result = filter([{"url": "https://ok.example/", "source": "manual"}])
        for key in ("kept", "dropped", "dead", "reasons", "by_site", "summary", "threshold"):
            self.assertIn(key, result)
        self.assertEqual(result["reasons"], [])
        self.assertIn("ok.example", result["by_site"])

    def test_restored_seed_is_admitted_despite_threshold(self):
        seed = {"url": "https://junk.example/login/cart/1/2/3/4/5", "source": "referral"}
        dropped = filter([seed])["dropped"][0]
        restored = filter([seed], restored=[dropped["url"]])
        self.assertEqual(len(restored["kept"]), 1)
        self.assertTrue(restored["kept"][0]["restored"])
        self.assertEqual(restored["kept"][0]["admission"], "restored")

    def test_restored_target_appended_after_existing(self):
        existing = {"url": "https://a.example/", "source": "manual"}
        junk = {"url": "https://junk.example/login/cart/1/2/3/4/5", "source": "referral"}
        queue = [item["target"] for item in filter([existing])["kept"]]
        result = filter([existing, junk], queue=queue, restored=[junk["url"]])
        self.assertEqual(
            [item["target"] for item in result["kept"]],
            queue + ["junk.example/login/cart/1/2/3/4/5"],
        )

    def test_force_restore_readmits_dead(self):
        seed = {"url": "https://dead.example/", "status": "dead"}
        result = filter([seed], restored=[{"url": seed["url"], "force": True}])
        self.assertEqual(len(result["kept"]), 1)
        self.assertTrue(result["kept"][0]["restored_dead"])
        self.assertTrue(result["kept"][0]["dead"])

    def test_previous_report_yields_changes(self):
        seeds = [{"url": "https://a.example/", "source": "manual"}]
        first = filter(seeds)
        second = filter([{**seeds[0], "source": "manual", "history": {"successes": 9, "attempts": 10}}], previous=first)
        changes = second["changes"]
        self.assertIn("score_changes", changes)
        self.assertEqual(changes["counts"]["added"], 0)
        self.assertEqual(changes["counts"]["removed"], 0)
        self.assertFalse(changes["order_changed"])

    def test_by_site_audit(self):
        result = filter(
            [
                {"url": "https://a.example/", "source": "manual"},
                {"url": "https://a.example/?utm_source=x", "source": "crawl"},
                {"url": "https://b.example/", "status": "dead"},
            ]
        )
        self.assertEqual(result["by_site"]["a.example"]["kept"], 1)
        self.assertEqual(result["by_site"]["a.example"]["duplicates"], 1)
        self.assertEqual(result["by_site"]["b.example"]["dead"], 1)

    def test_restore_from_review_log(self):
        from seedscore import ReviewLog

        seed = {"url": "https://junk.example/login/cart/1/2/3/4/5", "source": "referral"}
        dropped = filter([seed])["dropped"][0]
        log = ReviewLog()
        log.restore(dropped["url"], actor="alice", reason="false kill")
        result = filter([seed], review=log)
        self.assertEqual(len(result["kept"]), 1)
        self.assertTrue(result["kept"][0]["restored"])
        self.assertEqual(result["kept"][0]["restore_record"]["actor"], "alice")

    def test_revoked_restore_is_not_admitted(self):
        from seedscore import ReviewLog

        seed = {"url": "https://junk.example/login/cart/1/2/3/4/5", "source": "referral"}
        log = ReviewLog()
        log.restore(seed["url"], actor="alice", reason="false kill")
        log.revoke(seed["url"], actor="bob", reason="actually junk")
        result = filter([seed], review=log)
        self.assertEqual(result["kept"], [])
        self.assertEqual(result["dropped"][0]["reason"], "low_score")

    def test_review_at_uses_point_in_time_state(self):
        from seedscore import ReviewLog

        seed = {"url": "https://junk.example/login/cart/1/2/3/4/5", "source": "referral"}
        log = ReviewLog()
        log.restore(seed["url"], actor="alice", reason="r", at="2026-06-01T00:00:00Z")
        before = filter([seed], review=log, review_at="2026-01-01T00:00:00Z")
        after = filter([seed], review=log, review_at="2026-07-01T00:00:00Z")
        self.assertEqual(before["kept"], [])
        self.assertEqual(len(after["kept"]), 1)

    def test_restored_seed_protected_from_quota(self):
        seeds = [{"url": f"https://s{i}.example/", "source": "manual"} for i in range(3)]
        junk = {"url": "https://junk.example/login/cart/1/2/3/4/5", "source": "referral"}
        result = filter(seeds + [junk], limit=2, restored=[junk["url"]])
        kept_urls = {item["url"] for item in result["kept"]}
        self.assertIn(junk["url"], kept_urls)
        self.assertEqual(len(result["kept"]), 2)
        self.assertTrue(all(item["reason"] == "limit" for item in result["dropped"]))


if __name__ == "__main__":
    unittest.main()
