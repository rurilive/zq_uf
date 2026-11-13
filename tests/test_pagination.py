import unittest

from siteprobe import Samples, probe
from tests.fixtures import FakeFetcher, html_response, make_html

BASE = "http://demo.test"


def build_report(list_html, list_urls=None):
    pages = {
        BASE + "/": html_response(make_html("首页", [("/list", "列表")])),
    }
    list_urls = list_urls or [BASE + "/list"]
    for index, url in enumerate(list_urls):
        pages[url] = html_response(list_html[index] if isinstance(list_html, list) else list_html)
    return probe(BASE + "/", Samples(list_urls=list_urls, fetcher=FakeFetcher(pages), rate_per_sec=0))


class PaginationInferenceTest(unittest.TestCase):
    def test_query_style_pagination(self):
        html = make_html("列表", [
            ("/list?page=2", "2"),
            ("/list?page=3", "3"),
            ("/list?page=2", "下一页"),
            ("/article/1.html", "详情一"),
        ])
        report = build_report(html)
        pagination = report["pagination"]
        self.assertEqual(pagination["style"], "query")
        self.assertEqual(pagination["page_param"], "page")
        self.assertEqual(pagination["total_pages"], 3)
        self.assertEqual(pagination["next_href"], BASE + "/list?page=2")
        self.assertIn("page=", pagination["next_selector"])

    def test_path_style_pagination(self):
        html = make_html("列表", [
            ("/news/list_2.html", "2"),
            ("/news/list_3.html", "3"),
            ("/news/1001.html", "新闻详情"),
        ])
        report = build_report(html, list_urls=[BASE + "/news/list_1.html"])
        pagination = report["pagination"]
        self.assertEqual(pagination["style"], "path")
        self.assertIsNone(pagination["page_param"])
        self.assertIn("{page}", pagination["path_pattern"])
        self.assertEqual(pagination["total_pages"], 3)

    def test_path_pagination_not_confused_by_detail_ids(self):
        html = make_html("列表", [
            ("/news/list_2.html", "2"),
            ("/news/1001.html", "新闻详情一"),
            ("/news/1002.html", "新闻详情二"),
        ])
        report = build_report(html, list_urls=[BASE + "/news/list_1.html"])
        self.assertEqual(report["pagination"]["style"], "path")
        self.assertEqual(report["pagination"]["total_pages"], 2)
        self.assertEqual(report["details"]["candidates"], 2)

    def test_next_selector_uses_class(self):
        html = make_html("列表", [
            (("/list?page=2", "next-page"), "下一页"),
            ("/list?page=3", "3"),
        ])
        report = build_report(html)
        self.assertEqual(report["pagination"]["next_selector"], "a.next-page")

    def test_detail_links_and_pattern(self):
        html = make_html("列表", [
            ("/list?page=2", "2"),
            ("/article/101.html", "详情1"),
            ("/article/102.html", "详情2"),
            ("/article/103.html", "详情3"),
            ("/article/104.html", "详情4"),
        ])
        report = build_report(html)
        details = report["details"]
        self.assertEqual(details["detail_pattern"], "/article/(\\d+).html")
        self.assertEqual(len(details["urls"]), 3)
        self.assertEqual(details["candidates"], 4)
        kinds = [page["kind"] for page in report["pages"]]
        self.assertEqual(kinds.count("detail"), 3)


if __name__ == "__main__":
    unittest.main()
