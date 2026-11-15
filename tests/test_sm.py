import unittest

from sitemapinc2 import diff, merge


class SmTest(unittest.TestCase):
    def test_new_overwrites(self):
        self.assertEqual(merge({"a": 1}, {"b": 2})["total"], 1)

    def test_deleted_not_counted(self):
        self.assertEqual(merge({"a": 1}, {"b": 2})["deleted"], 0)

    def test_diff_empty(self):
        merge({}, {})
        self.assertEqual(diff()["deleted"], [])
