import unittest

from seedexpand import budget, expand


class ExpandTest(unittest.TestCase):
    def test_all_links_added(self):
        page = {"url": "https://a/", "links": ["https://a/1", "https://a/2"]}
        self.assertEqual(expand(page, 0)["added"], ["https://a/1", "https://a/2"])

    def test_depth_echoed(self):
        self.assertEqual(expand({"links": []}, 2)["depth"], 2)

    def test_budget_has_no_limit(self):
        self.assertIsNone(budget("a")["limit"])
