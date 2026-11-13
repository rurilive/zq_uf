import unittest

from listwatch import diff


class ListwatchTest(unittest.TestCase):
    def test_added(self):
        old = [{"title": "a"}]
        new = [{"title": "a"}, {"title": "b"}]
        self.assertEqual([item["title"] for item in diff(old, new)["added"]], ["b"])

    def test_removed(self):
        old = [{"title": "a"}, {"title": "b"}]
        new = [{"title": "a"}]
        self.assertEqual([item["title"] for item in diff(old, new)["removed"]], ["b"])

    def test_same_list_has_no_change(self):
        items = [{"title": "a"}, {"title": "b"}]
        self.assertEqual(diff(items, items), {"added": [], "removed": []})
