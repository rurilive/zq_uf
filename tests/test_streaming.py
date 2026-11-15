import unittest

from auditsearch import (
    CursorExpired,
    CursorInvalid,
    SearchTimeout,
    configure,
    export,
    load,
    page,
    search,
)

DEFAULTS = {
    "page_size": 100,
    "max_hits": 100000,
    "cursor_ttl": 300.0,
    "timeout": None,
    "index_fields": ("user", "type"),
}


def make_events(n=50):
    return [
        {
            "time": i,
            "user": "u%d" % (i % 3),
            "type": "select" if i % 2 else "drop",
            "i": i,
        }
        for i in range(n)
    ]


class StreamingSearchTest(unittest.TestCase):
    def setUp(self):
        configure(**DEFAULTS)

    def test_filter_by_user_type_and_time_range(self):
        load(make_events())
        res = search({"user": "u0", "type": "drop", "time_from": 6, "time_to": 12})
        self.assertEqual([h["i"] for h in res["hits"]], [6, 12])

    def test_results_sorted_by_time(self):
        load([{"time": t, "i": t} for t in (5, 1, 9, 3)])
        self.assertEqual([h["i"] for h in search({})["hits"]], [1, 3, 5, 9])

    def test_pagination_no_dup_no_miss(self):
        load(make_events(25))
        configure(page_size=10)
        first = search({})
        self.assertEqual(len(first["hits"]), 10)
        self.assertTrue(first["truncated"])
        second = page(first["cursor"])
        third = page(second["cursor"])
        got = [h["i"] for h in first["hits"] + second["hits"] + third["hits"]]
        self.assertEqual(got, list(range(25)))
        self.assertFalse(third["truncated"])
        self.assertIsNone(third["cursor"])

    def test_cursor_carries_query(self):
        load(make_events(30))
        configure(page_size=4)
        first = search({"user": "u1"})
        second = page(first["cursor"])
        self.assertTrue(second["page"]["returned"] > 0)
        for hit in first["hits"] + second["hits"]:
            self.assertEqual(hit["user"], "u1")

    def test_cursor_tampered_raises(self):
        load(make_events(30))
        configure(page_size=5)
        cursor = search({})["cursor"]
        cursor["last"][2] += 1  # 篡改位置指纹
        with self.assertRaises(CursorInvalid):
            page(cursor)

    def test_cursor_expired_raises(self):
        configure(page_size=5, cursor_ttl=-1)
        load(make_events(30))
        cursor = search({})["cursor"]
        with self.assertRaises(CursorExpired):
            page(cursor)

    def test_data_change_during_paging_warns(self):
        load(make_events(30))
        configure(page_size=5)
        cursor = search({})["cursor"]
        load(make_events(30))  # 翻页期间数据变更
        res = page(cursor)
        self.assertIn("warning", res)

    def test_fields_projection(self):
        load(make_events(5))
        hits = search({}, fields=["user"])["hits"]
        self.assertEqual(set(hits[0].keys()), {"user"})

    def test_max_hits_truncates_and_tells(self):
        load(make_events(50))
        res = search({}, max_hits=10)
        self.assertEqual(len(res["hits"]), 10)
        self.assertTrue(res["truncated"])

    def test_timeout_interrupts(self):
        load(make_events(5000))
        with self.assertRaises(SearchTimeout):
            search({}, timeout=0)

    def test_export_uses_same_query(self):
        load(make_events(40))
        got = list(export({"type": "select"}))
        self.assertEqual([h["i"] for h in got], list(range(1, 40, 2)))

    def test_index_fallback_to_scan(self):
        load(make_events(20))
        by_index = search({"user": "u1"})
        self.assertTrue(by_index["page"]["index_used"])
        degraded = search({"i": 7})  # i 无索引，降级全量扫描
        self.assertFalse(degraded["page"]["index_used"])
        self.assertEqual([h["i"] for h in degraded["hits"]], [7])

    def test_page_size_configurable(self):
        configure(page_size=3)
        load(make_events(10))
        res = search({})
        self.assertEqual(len(res["hits"]), 3)
        self.assertEqual(res["page"]["size"], 3)


if __name__ == "__main__":
    unittest.main()
