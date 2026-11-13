import unittest

from linkfollow import discover

PAGE = '<a href="http://site/a">A</a><a href="/b">B</a>'


class LinkfollowTest(unittest.TestCase):
    def test_extract_links(self):
        self.assertEqual(discover(PAGE), ["http://site/a", "/b"])

    def test_empty_page(self):
        self.assertEqual(discover("<html></html>"), [])

    def test_depth_does_not_change_result(self):
        self.assertEqual(discover(PAGE, depth=3), discover(PAGE))
