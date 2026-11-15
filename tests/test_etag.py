import csv
import io
import unittest
from datetime import date

from etagcache import (
    cache_size,
    cached,
    clear,
    configure,
    eviction_records,
    eviction_stats,
    export_evictions,
    export_stats,
    headers,
    reset,
    stats,
    store,
)

FULL = "full page"


def page(status=200, body=FULL, etag=None, last_modified=None):
    response = {"status": status, "body": body}
    if etag is not None:
        response["etag"] = etag
    if last_modified is not None:
        response["last_modified"] = last_modified
    return response


class EtagTest(unittest.TestCase):
    def setUp(self):
        reset()
        configure(max_per_site=100, max_total=1000)

    def test_first_request_is_unconditional(self):
        self.assertEqual(headers("https://shop/p"), {})

    def test_conditional_headers_sent_from_saved_validators(self):
        store("https://shop/p", page(etag='"v1"', last_modified="Wed, 21 Oct 2015 07:28:00 GMT"))
        self.assertEqual(
            headers("https://shop/p"),
            {
                "If-None-Match": '"v1"',
                "If-Modified-Since": "Wed, 21 Oct 2015 07:28:00 GMT",
            },
        )

    def test_headers_from_response_headers_mapping(self):
        store("https://shop/p", {"status": 200, "body": FULL,
                                 "headers": {"ETag": '"v2"'}})
        self.assertEqual(headers("https://shop/p"), {"If-None-Match": '"v2"'})

    def test_304_keeps_body_and_validators(self):
        store("https://shop/p", page(etag='"v1"'))
        result = store("https://shop/p", page(304, body="", etag='"ignored"'))
        self.assertTrue(result["hit"])
        self.assertTrue(result["not_modified"])
        self.assertFalse(result["content_updated"])
        entry = cached("https://shop/p")
        self.assertEqual(entry["body"], FULL)
        self.assertEqual(entry["etag"], '"v1"')

    def test_304_only_refreshes_fetch_time(self):
        store("https://shop/p", page(etag='"v1"'))
        before = cached("https://shop/p")["fetched_at"]
        import time
        time.sleep(0.01)
        store("https://shop/p", page(304, body=""))
        self.assertGreater(cached("https://shop/p")["fetched_at"], before)

    def test_304_without_cache_is_a_miss(self):
        result = store("https://shop/p", page(304, body=""))
        self.assertFalse(result["hit"])
        self.assertTrue(result["not_modified"])
        self.assertIsNone(cached("https://shop/p"))

    def test_200_same_body_reports_not_updated(self):
        store("https://shop/p", page(etag='"v1"'))
        result = store("https://shop/p", page(etag='"v1"'))
        self.assertTrue(result["hit"])
        self.assertFalse(result["content_updated"])

    def test_200_new_body_reports_updated_and_replaces_validators(self):
        store("https://shop/p", page(body="old", etag='"v1"'))
        result = store("https://shop/p", page(body="new", etag='"v2"'))
        self.assertTrue(result["content_updated"])
        self.assertEqual(cached("https://shop/p")["body"], "new")
        self.assertEqual(headers("https://shop/p"), {"If-None-Match": '"v2"'})

    def test_non_200_leaves_cache_untouched(self):
        store("https://shop/p", page(etag='"v1"'))
        result = store("https://shop/p", page(500, body="oops"))
        self.assertFalse(result["stored"])
        self.assertEqual(cached("https://shop/p")["body"], FULL)

    def test_two_rounds_unchanged_content_survives(self):
        """上线前重点核对：两轮抓取后未修改页面不能变空。"""
        store("https://a/1", page(etag='"v1"'))
        headers("https://a/1")
        store("https://a/1", page(304, body=""))
        store("https://a/1", page(304, body=""))
        self.assertEqual(cached("https://a/1")["body"], FULL)

    def test_per_site_lru_eviction(self):
        configure(max_per_site=2, max_total=100)
        for name in ("a", "b", "c"):
            store(f"https://site1/{name}", page(etag=f'"{name}"'))
        self.assertEqual(cache_size(), 2)
        self.assertIsNone(cached("https://site1/a"))
        self.assertIsNotNone(cached("https://site1/c"))
        self.assertEqual(headers("https://site1/a"), {})

    def test_lru_uses_recent_requests(self):
        configure(max_per_site=2, max_total=100)
        store("https://s/a", page())
        store("https://s/b", page())
        headers("https://s/a")          # a 变得更近
        store("https://s/c", page())
        self.assertIsNone(cached("https://s/b"))
        self.assertIsNotNone(cached("https://s/a"))

    def test_limits_are_per_site(self):
        configure(max_per_site=1, max_total=100)
        store("https://s1/a", page())
        store("https://s2/a", page())
        self.assertEqual(cache_size(), 2)

    def test_total_limit_across_sites(self):
        configure(max_per_site=10, max_total=2)
        store("https://s1/a", page())
        store("https://s2/a", page())
        store("https://s3/a", page())
        self.assertEqual(cache_size(), 2)
        self.assertIsNone(cached("https://s1/a"))

    def test_eviction_reported_and_recorded(self):
        configure(max_per_site=1, max_total=100)
        store("https://s/a", page())
        result = store("https://s/b", page())
        self.assertEqual(result["evicted"], 1)
        records = eviction_records(site="s")
        self.assertEqual([r["url"] for r in records], ["https://s/a"])
        self.assertEqual(records[0]["reason"], "capacity")

    def test_manual_clear_reports_counts_by_site(self):
        store("https://s1/a", page())
        store("https://s1/b", page())
        store("https://s2/a", page())
        result = clear(site="s1")
        self.assertEqual(result["cleared"], 2)
        self.assertEqual(result["by_site"], {"s1": 2})
        self.assertEqual(cache_size(), 1)
        self.assertEqual(headers("https://s1/a"), {})   # 清理后全量下载

    def test_manual_clear_all(self):
        store("https://s1/a", page())
        store("https://s2/a", page())
        self.assertEqual(clear()["cleared"], 2)
        self.assertEqual(cache_size(), 0)

    def test_eviction_stats_separate_capacity_and_manual(self):
        configure(max_per_site=1, max_total=100)
        store("https://s/a", page())
        store("https://s/b", page())      # 淘汰 a（容量）
        clear(site="s")                   # 人工清理 b
        rows = eviction_stats(site="s")
        self.assertEqual(rows[0]["cleared"], 2)
        self.assertEqual(rows[0]["capacity"], 1)
        self.assertEqual(rows[0]["manual"], 1)

    def test_eviction_records_filter_by_site_and_reason(self):
        configure(max_per_site=1, max_total=100)
        store("https://s1/a", page())
        store("https://s1/b", page())
        clear(site="s2")  # 无命中，不产生记录
        store("https://s2/a", page())
        clear(site="s2")
        self.assertEqual(len(eviction_records(site="s1")), 1)
        self.assertEqual(eviction_records(site="s2")[0]["reason"], "manual")
        self.assertEqual(len(eviction_records(reason="manual")), 1)

    def test_eviction_records_filter_by_day(self):
        store("https://s/a", page())
        clear(site="s")
        today = date.today().isoformat()
        self.assertEqual(len(eviction_records(since=today, until=today)), 1)
        self.assertEqual(eviction_records(until="2000-01-01"), [])

    def test_stats_per_site_and_day(self):
        store("https://s1/a", page(etag='"v1"'))
        store("https://s1/b", page(etag='"v1"'))
        headers("https://s1/a")
        store("https://s1/a", page(304, body=""))
        store("https://s1/b", page(body="changed"))
        rows = stats(site="s1")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["requests"], 4)
        self.assertEqual(row["hits"], 2)
        self.assertEqual(row["misses"], 2)
        self.assertEqual(row["not_modified"], 1)
        self.assertEqual(row["content_updated"], 3)
        self.assertEqual(row["day"], date.today().isoformat())
        self.assertEqual(stats(site="other"), [])

    def test_stats_filter_by_day(self):
        store("https://s/a", page())
        self.assertEqual(len(stats(day=date.today())), 1)
        self.assertEqual(stats(day="2000-01-01"), [])

    def test_export_stats_csv(self):
        store("https://s/a", page(etag='"v1"'))
        store("https://s/a", page(304, body=""))
        text = export_stats()
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["site"], "s")
        self.assertEqual(rows[0]["hits"], "1")
        self.assertEqual(rows[0]["not_modified"], "1")

    def test_export_evictions_csv(self):
        store("https://s/a", page())
        clear(site="s")
        rows = list(csv.DictReader(io.StringIO(export_evictions())))
        self.assertEqual(rows[0]["url"], "https://s/a")
        self.assertEqual(rows[0]["reason"], "manual")

    def test_export_to_file(self):
        import os
        import tempfile
        store("https://s/a", page())
        clear(site="s")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "evictions.csv")
            export_evictions(path=path)
            self.assertTrue(os.path.exists(path))
            with open(path, encoding="utf-8") as fh:
                self.assertIn("https://s/a", fh.read())


if __name__ == "__main__":
    unittest.main()
