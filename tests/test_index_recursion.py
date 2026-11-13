import os
import tempfile
import unittest

from sitemaptool import load_sources

INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>nested/child.xml</loc><lastmod>2026-04-01</lastmod></sitemap>
</sitemapindex>
"""

CHILD = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>pages.xml</loc></sitemap>
  <sitemap><loc>../index.xml</loc></sitemap>
</sitemapindex>
"""

PAGES = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>http://shop.example/p1</loc><lastmod>2026-05-01</lastmod></url>
  <url><loc>http://shop.example/p2</loc><changefreq>daily</changefreq></url>
</urlset>
"""


class IndexRecursionTest(unittest.TestCase):
    def test_nested_index_expands_and_cycles_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            nested = os.path.join(tmp, "nested")
            os.makedirs(nested)
            with open(os.path.join(tmp, "index.xml"), "w", encoding="utf-8") as f:
                f.write(INDEX)
            with open(os.path.join(nested, "child.xml"), "w", encoding="utf-8") as f:
                f.write(CHILD)
            with open(os.path.join(nested, "pages.xml"), "w", encoding="utf-8") as f:
                f.write(PAGES)

            entries = load_sources([os.path.join(tmp, "index.xml")])

        self.assertEqual(
            [entry["loc"] for entry in entries],
            ["http://shop.example/p1", "http://shop.example/p2"],
        )
        self.assertEqual(entries[1]["changefreq"], "daily")

    def test_multiple_entry_points_share_dedup_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = os.path.join(tmp, "index.xml")
            pages = os.path.join(tmp, "pages.xml")
            with open(index, "w", encoding="utf-8") as f:
                f.write(INDEX.replace("nested/child.xml", "pages.xml"))
            with open(pages, "w", encoding="utf-8") as f:
                f.write(PAGES)
            entries = load_sources([index, pages])
        self.assertEqual(len(entries), 2)


if __name__ == "__main__":
    unittest.main()
