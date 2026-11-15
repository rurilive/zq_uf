import unittest

from seedexpand import budget, canonical_url, configure, expand, reset, score_link


class ExpandTest(unittest.TestCase):
    def test_all_links_added(self):
        page = {"url": "https://a/", "links": ["https://a/1", "https://a/2"]}
        self.assertEqual(expand(page, 0)["added"], ["https://a/1", "https://a/2"])

    def test_depth_echoed(self):
        self.assertEqual(expand({"links": []}, 2)["depth"], 2)

    def test_budget_has_no_limit(self):
        self.assertIsNone(budget("a")["limit"])


class DepthTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_links_enter_next_layer(self):
        result = expand({"url": "https://d/", "links": ["https://d/1"]}, 0)
        self.assertEqual(result["added_detail"][0]["depth"], 1)
        self.assertEqual(result["depth_counts"][1], 1)

    def test_category_layers_tracked_separately(self):
        expand({"url": "https://d/", "links": ["https://d/1"]}, 0)
        expand({"url": "https://d/1", "links": ["https://d/2"]}, 1)
        snapshot = budget("d")
        self.assertEqual(snapshot["depth_counts"][1], 1)
        self.assertEqual(snapshot["depth_counts"][2], 1)
        self.assertEqual(snapshot["layers"][1]["used"], 1)
        self.assertEqual(snapshot["layers"][2]["used"], 1)

    def test_stops_at_max_depth(self):
        expand({"url": "https://d/", "links": ["https://d/1"]}, 0)
        expand({"url": "https://d/1", "links": ["https://d/2"]}, 1)
        expand({"url": "https://d/2", "links": ["https://d/3"]}, 2)
        result = expand({"url": "https://d/3", "links": ["https://d/4"]}, 3)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["truncated"][0]["reason"], "max-depth")
        self.assertEqual(result["truncated"][0]["depth"], 4)

    def test_max_depth_is_configurable(self):
        configure("d", max_depth=1)
        expand({"url": "https://d/", "links": ["https://d/1"]}, 0)
        result = expand({"url": "https://d/1", "links": ["https://d/2"]}, 1)
        self.assertEqual(result["added"], [])
        self.assertEqual(len(result["truncated"]), 1)


class QuotaTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_layer_limit_truncates(self):
        configure("q", quotas={1: 2})
        result = expand(
            {"url": "https://q/", "links": ["https://q/a", "https://q/b", "https://q/c"]},
            0,
        )
        self.assertEqual(len(result["added"]), 2)
        self.assertEqual(len(result["truncated"]), 1)
        self.assertEqual(result["truncated"][0]["reason"], "depth-quota")
        self.assertEqual(result["layer_budget"]["limit"], 2)

    def test_quota_accumulates_across_batches(self):
        configure("q", quotas={1: 2})
        expand({"url": "https://q/", "links": ["https://q/a", "https://q/b"]}, 0)
        result = expand({"url": "https://q/2", "links": ["https://q/c"]}, 0)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["truncated"][0]["reason"], "depth-quota")
        self.assertEqual(budget("q")["layers"][1]["remaining"], 0)

    def test_truncation_keeps_high_value_links(self):
        configure("q", quotas={1: 2})
        result = expand(
            {
                "url": "https://q/",
                "links": [
                    "https://q/about",
                    "https://q/item/1",
                    "https://q/category/1",
                ],
            },
            0,
        )
        self.assertEqual(result["added"], ["https://q/item/1", "https://q/category/1"])
        self.assertEqual(result["truncated"][0]["url"], "https://q/about")

    def test_nav_links_capped_per_layer(self):
        configure("q", quotas={1: 10}, category_caps={"nav": 1})
        result = expand(
            {"url": "https://q/", "links": ["https://q/about", "https://q/contact"]},
            0,
        )
        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(result["truncated"][0]["reason"], "category-cap")
        self.assertEqual(result["truncated"][0]["category"], "nav")

    def test_quota_isolated_per_site(self):
        configure("a", quotas={1: 1})
        expand({"site": "a", "url": "https://a/", "links": ["https://a/1"]}, 0)
        other = expand({"site": "b", "url": "https://b/", "links": ["https://a/2"]}, 0)
        self.assertEqual(other["added"], ["https://a/2"])
        self.assertEqual(budget("a")["layers"][1]["remaining"], 0)

    def test_blank_links_skipped(self):
        result = expand({"url": "https://q/", "links": ["", "https://q/1"]}, 0)
        self.assertEqual(result["added"], ["https://q/1"])

    def test_media_links_not_expanded(self):
        result = expand({"url": "https://q/", "links": ["https://q/a.png"]}, 0)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["truncated"][0]["reason"], "media")


class DedupeTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_duplicate_within_batch(self):
        result = expand(
            {"url": "https://z/", "links": ["https://z/1", "https://z/1"]}, 0
        )
        self.assertEqual(result["added"], ["https://z/1"])
        self.assertEqual(len(result["duplicates"]), 1)
        self.assertEqual(result["duplicates"][0]["reason"], "duplicate-in-batch")

    def test_duplicate_across_batches(self):
        expand({"url": "https://z/", "links": ["https://z/1"]}, 0)
        result = expand({"url": "https://z/2", "links": ["https://z/1"]}, 0)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["duplicates"][0]["reason"], "already-enqueued")
        self.assertEqual(result["duplicates"][0]["first_source"], "https://z/")

    def test_protocol_and_trailing_slash_are_same_target(self):
        expand({"url": "https://z/", "links": ["https://z/item/1"]}, 0)
        result = expand({"url": "https://z/2", "links": ["http://z/item/1/"]}, 0)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["duplicates"][0]["url"], "http://z/item/1/")

    def test_canonical_url_normalizes_variants(self):
        self.assertEqual(canonical_url("http://z/a/"), canonical_url("https://z/a"))
        self.assertEqual(canonical_url("https://z/a#top"), "http://z/a")
        self.assertEqual(canonical_url("https://z:443/a"), "http://z/a")


class SourceTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_added_target_keeps_source_page(self):
        result = expand({"url": "https://s/", "links": ["https://s/1"]}, 0)
        self.assertEqual(result["added_detail"][0]["source"], "https://s/")
        targets = budget("s")["targets"]
        self.assertEqual(targets[0]["source"], "https://s/")
        self.assertEqual(targets[0]["depth"], 1)

    def test_duplicate_keeps_first_source(self):
        expand({"url": "https://s/", "links": ["https://s/1"]}, 0)
        expand({"url": "https://s/2", "links": ["https://s/1"]}, 0)
        self.assertEqual(budget("s")["targets"][0]["source"], "https://s/")


class PriorityTest(unittest.TestCase):
    def test_scores_rank_categories(self):
        self.assertGreater(score_link("https://p/item/1"), score_link("https://p/category/1"))
        self.assertGreater(score_link("https://p/category/1"), score_link("https://p/about"))
        self.assertGreater(score_link("https://p/about"), score_link("https://p/logo.png"))

    def test_query_ids_score_as_content(self):
        self.assertGreater(
            score_link("https://p/detail?id=9"), score_link("https://p/detail")
        )
