import tempfile
import unittest

from sitemaptool import apply_robots, build_robots

ROBOTS = """User-agent: *
Allow: /private/public
Disallow: /private/
"""


class RobotsRuleTest(unittest.TestCase):
    def test_conflicting_links_are_excluded_and_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            rules_path = f"{tmp}/robots.txt"
            with open(rules_path, "w", encoding="utf-8") as handle:
                handle.write(ROBOTS)
            parsers = build_robots({"shop.example": rules_path})

        entries = [
            {"loc": "http://shop.example/items/1", "lastmod": "", "changefreq": ""},
            {"loc": "http://shop.example/private/1", "lastmod": "", "changefreq": ""},
            {"loc": "http://shop.example/deep/secret/1", "lastmod": "", "changefreq": ""},
            {"loc": "http://shop.example/private/public", "lastmod": "", "changefreq": ""},
        ]
        allowed, excluded = apply_robots(entries, parsers)

        self.assertEqual(
            [item["loc"] for item in allowed],
            [
                "http://shop.example/items/1",
                "http://shop.example/deep/secret/1",
                "http://shop.example/private/public",
            ],
        )
        self.assertEqual(
            [item["loc"] for item in excluded],
            [
                "http://shop.example/private/1",
            ],
        )
        self.assertTrue(all(item["reason"].startswith("Disallow") for item in excluded))

    def test_unknown_site_defaults_to_allow(self):
        entries = [{"loc": "http://other.example/a", "lastmod": "", "changefreq": ""}]
        allowed, excluded = apply_robots(entries, build_robots({}))
        self.assertEqual(len(allowed), 1)
        self.assertEqual(excluded, [])


if __name__ == "__main__":
    unittest.main()
