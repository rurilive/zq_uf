import unittest

from detailcrawl import crawl


class DetailcrawlTest(unittest.TestCase):
    def test_order_preserved(self):
        pages = {"http://s/1": "a", "http://s/2": "b"}
        self.assertEqual(crawl(["http://s/1", "http://s/2"], pages.get), ["a", "b"])

    def test_empty_urls(self):
        self.assertEqual(crawl([], lambda url: "x"), [])

    def test_duplicate_urls_fetched_twice(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return "ok"

        crawl(["http://s/1", "http://s/1"], fetch)
        self.assertEqual(calls, ["http://s/1", "http://s/1"])
