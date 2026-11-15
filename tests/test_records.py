"""重写记录测试：命中/未命中均记录，前后值与变更可追溯。"""

import unittest

from linkrewrite import Registry, Rule, history, rewrite


class RecordTest(unittest.TestCase):
    def setUp(self):
        self.registry = Registry()
        self.registry.add_rule(
            "shop",
            Rule(
                id="strip",
                priority=100,
                tracking={"delete": ["utm_source", "spm"]},
            ),
        )
        self.registry.add_rule(
            "shop",
            Rule(
                id="page",
                priority=50,
                pagination={
                    "retain": ["page"],
                    "inject": {"page_size": "20"},
                },
            ),
        )

    def test_record_contains_before_after_and_hit_rules(self):
        url = "https://shop/p?utm_source=a&page=3"
        result = rewrite(url, "shop", registry=self.registry)
        self.assertEqual(result["before"], url)
        self.assertNotEqual(result["before"], result["after"])
        hit_ids = [item["rule"] for item in result["hit"]]
        self.assertEqual(hit_ids, ["strip", "page"])
        self.assertTrue(result["matched"])
        self.assertIsInstance(result["seq"], int)
        self.assertIn("timestamp", result)

    def test_changes_record_removed_injected_and_values(self):
        result = rewrite(
            "https://shop/p?utm_source=a&spm=b&page=3",
            "shop",
            registry=self.registry,
        )
        self.assertEqual(
            result["changes"]["tracking"]["removed"],
            {"utm_source": "a", "spm": "b"},
        )
        self.assertEqual(
            result["changes"]["pagination"]["injected"],
            {"page_size": "20"},
        )

    def test_passthrough_is_recorded(self):
        url = "https://unknown/p?page=2"
        result = rewrite(url, "unknown", registry=self.registry)
        self.assertEqual(result["after"], url)
        self.assertEqual(result["hit"], [])
        self.assertFalse(result["matched"])

        logged = history(registry=self.registry)
        self.assertEqual(len(logged), 1)
        self.assertEqual(logged[0]["after"], url)
        self.assertEqual(logged[0]["site"], "unknown")

    def test_history_filter_by_site_and_limit(self):
        rewrite("https://shop/p?utm_source=a", "shop",
                registry=self.registry)
        rewrite("https://news/p?page=1", "news",
                registry=self.registry)
        rewrite("https://shop/p?spm=x", "shop",
                registry=self.registry)

        shop_logs = history(site="shop", registry=self.registry)
        self.assertEqual(len(shop_logs), 2)
        self.assertTrue(
            all(item["site"] == "shop" for item in shop_logs)
        )
        self.assertEqual(
            len(history(limit=1, registry=self.registry)), 1
        )
        self.assertEqual(
            history(limit=1, registry=self.registry)[0]["site"], "shop"
        )

    def test_history_survives_multiple_rewrites_in_order(self):
        urls = [
            "https://shop/p?utm_source=%d" % i for i in range(5)
        ]
        for url in urls:
            rewrite(url, "shop", registry=self.registry)
        logged = history(registry=self.registry)
        self.assertEqual([item["before"] for item in logged], urls)
        self.assertEqual([item["seq"] for item in logged],
                         [1, 2, 3, 4, 5])

    def test_record_pins_revision_at_rewrite_time(self):
        result = rewrite(
            "https://shop/p?page=1", "shop", registry=self.registry
        )
        rev_at_rewrite = result["revision"]
        self.registry.add_rule(
            "shop", Rule(id="later", priority=1,
                         other={"delete": ["junk"]})
        )
        logged = history(site="shop", registry=self.registry)[0]
        self.assertEqual(logged["revision"], rev_at_rewrite)
        self.assertNotEqual(
            self.registry.current_revision("shop"), rev_at_rewrite
        )

    def test_injected_value_change_records_before_and_after(self):
        self.registry.add_rule(
            "shop",
            Rule(
                id="override",
                priority=90,
                pagination={"inject": {"page": "1"}},
            ),
        )
        result = rewrite(
            "https://shop/p?page=7", "shop", registry=self.registry
        )
        self.assertIn("page=1", result["after"])
        self.assertEqual(
            result["changes"]["pagination"]["changed"],
            [{"param": "page", "before": "7", "after": "1"}],
        )


if __name__ == "__main__":
    unittest.main()
