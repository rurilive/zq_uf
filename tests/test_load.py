import os
import tempfile
import unittest

from sitemaptool import load_sources, parse_urlset

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<urlset>
  <url><loc>http://site/a</loc><lastmod>2026-03-01</lastmod></url>
  <url><loc>http://site/b</loc></url>
</urlset>
"""


class SitemaptoolTest(unittest.TestCase):
    def test_parse_two_urls(self):
        entries = parse_urlset(SAMPLE)
        self.assertEqual([entry["loc"] for entry in entries], ["http://site/a", "http://site/b"])

    def test_missing_fields_are_empty(self):
        entries = parse_urlset(SAMPLE)
        self.assertEqual(entries[1]["lastmod"], "")

    def test_load_multiple_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "a.xml")
            second = os.path.join(tmp, "b.xml")
            open(first, "w", encoding="utf-8").write(SAMPLE)
            open(second, "w", encoding="utf-8").write(SAMPLE)
            entries = load_sources([first, second])
        self.assertEqual(len(entries), 4)
