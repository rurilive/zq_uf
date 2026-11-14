import unittest

from sitemapinc import diff, merge
from sitemapinc import sitemap


class SitemapTest(unittest.TestCase):
    def setUp(self):
        sitemap._index.clear()

    def test_merge_keeps_new(self):
        self.assertEqual(merge({"a": 1}, {"b": 2})["b"], 2)

    def test_merge_overwrites(self):
        self.assertEqual(merge({"a": 1}, {"a": 2})["a"], 2)

    def test_diff_shape(self):
        self.assertEqual(set(diff()), {"added", "removed", "updated"})
