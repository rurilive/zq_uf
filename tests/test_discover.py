import unittest

from linkfollow import discover, Page

PAGE = '<a href="http://site/a">A</a><a href="/b">B</a>'


class LinkfollowTest(unittest.TestCase):
    def test_extract_links(self):
        self.assertEqual(discover(PAGE), ["http://site/a", "/b"])

    def test_empty_page(self):
        self.assertEqual(discover("<html></html>"), [])

    def test_depth_does_not_change_result(self):
        self.assertEqual(discover(PAGE, depth=3), discover(PAGE))


SITE = "site.example"


class NormalizationTest(unittest.TestCase):
    def test_session_params_anchor_and_same_path_dedup(self):
        html = (
            '<a href="/item?id=1&sid=abc&PHPSESSID=zz#top">a</a>'
            '<a href="/item?id=2">b</a>'
            '<a href="/item/">c</a>'
        )
        page = Page(html, url="http://site.example/home", site_host=SITE)
        result = discover(page, depth=1)

        self.assertEqual(list(result), ["http://site.example/item?id=1"])
        self.assertNotIn("sid", str(result[0]))
        self.assertNotIn("phpsessid", str(result[0]).lower())
        self.assertNotIn("#", str(result[0]))
        self.assertEqual(result[0].dedup_key, "http://site.example/item")
        self.assertEqual(result[0].source, "http://site.example/home")
        self.assertEqual(
            {(r["reason"]) for r in result.rejected},
            {"duplicate"},
        )

    def test_external_links_filtered_by_default(self):
        html = ('<a href="http://other.example/x">x</a>'
                '<a href="/local">l</a>')
        page = Page(html, url="http://site.example/home", site_host=SITE)
        result = discover(page, depth=1)

        self.assertEqual(list(result), ["http://site.example/local"])
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(result.rejected[0]["reason"], "external")
        self.assertEqual(result.rejected[0]["dedup_key"],
                         "http://other.example/x")

    def test_external_links_can_be_allowed(self):
        page = Page('<a href="http://other.example/x">x</a>',
                    url="http://site.example/home", site_host=SITE)
        result = discover(page, depth=1, allow_external=True)
        self.assertEqual(list(result), ["http://other.example/x"])
        self.assertEqual(result.rejected, [])


class PaginationTest(unittest.TestCase):
    def test_pagination_links_merge_to_parent_without_depth(self):
        html = ('<a href="/list?page=2">下一页</a>'
                '<a href="/list/page/3">3</a>'
                '<a href="/article">a</a>')
        page = Page(html, url="http://site.example/list", site_host=SITE)
        result = discover(page, depth=2)

        self.assertEqual(list(result), ["http://site.example/article"])
        self.assertEqual(
            [str(link) for link in result.pagination],
            ["http://site.example/list?page=2",
             "http://site.example/list/page/3"],
        )
        for link in result.pagination:
            self.assertTrue(link.is_pagination)
            self.assertEqual(link.parent, "http://site.example/list")
            self.assertEqual(link.depth, 2)
        self.assertEqual(result[0].depth, 3)


class LimitTest(unittest.TestCase):
    def test_depth_limit_rejected_with_reason(self):
        page = Page('<a href="/deep">d</a>',
                    url="http://site.example/d", site_host=SITE)
        result = discover(page, depth=2, max_depth=2)

        self.assertEqual(list(result), [])
        self.assertEqual(result.rejected[0]["reason"], "depth_limit")
        self.assertEqual(result.rejected[0]["dedup_key"],
                         "http://site.example/deep")

    def test_per_level_limit_rejected_with_reason(self):
        html = ''.join('<a href="/p%d">p</a>' % i for i in range(3))
        page = Page(html, url="http://site.example/start", site_host=SITE)
        result = discover(page, depth=1, max_per_level=2)

        self.assertEqual(len(result), 2)
        self.assertEqual(result.rejected[0]["reason"], "per_level_limit")

    def test_pagination_exempt_from_depth_limit(self):
        page = Page('<a href="/list?page=9">下一页</a>',
                    url="http://site.example/list", site_host=SITE)
        result = discover(page, depth=5, max_depth=5)
        self.assertEqual(len(result.pagination), 1)
        self.assertEqual(result.pagination[0].depth, 5)
        self.assertEqual(list(result), [])
