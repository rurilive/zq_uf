import unittest

from auditquery import configure, load, page, search

_DEFAULTS = {"page_size": 10, "max_page_size": 500,
             "max_hits": 100_000, "use_index": True}


def make_history(n, base_ts=1_000_000):
    """一段历史审计数据：ts 连续，操作人/类型交替。"""
    return [{"ts": base_ts + i,
             "user": ("etl", "bi", "app")[i % 3],
             "op": ("select", "drop")[i % 2],
             "rid": i} for i in range(n)]


def collect_all(first):
    """沿游标翻到底，返回全部命中。"""
    out, cur = list(first["hits"]), first["cursor"]
    while cur:
        res = page(cur)
        out.extend(res["hits"])
        cur = res["cursor"]
    return out


class SearchTest(unittest.TestCase):
    def tearDown(self):
        configure(**_DEFAULTS)

    def test_exact_match(self):
        load([{"user": "a"}, {"user": "b"}])
        self.assertEqual(len(search({"user": "a"})["hits"]), 1)

    def test_load_counts(self):
        self.assertEqual(load([{"i": 1}])["loaded"], 1)

    def test_time_range_and_operator_filter(self):
        load(make_history(100))
        res = search({"user": "etl", "start": 1_000_010, "end": 1_000_050})
        for hit in res["hits"]:
            self.assertEqual(hit["user"], "etl")
            self.assertTrue(1_000_010 <= hit["ts"] <= 1_000_050)
        self.assertEqual(res["total"],
                         len([e for e in make_history(100)
                              if e["user"] == "etl"
                              and 1_000_010 <= e["ts"] <= 1_000_050]))

    def test_time_desc_order(self):
        load(make_history(50))
        hits = collect_all(search({"op": "select"}, size=7))
        ts = [h["ts"] for h in hits]
        self.assertEqual(ts, sorted(ts, reverse=True))

    def test_pagination_no_dup_no_miss_on_history(self):
        """历史数据上翻页验证：全集不重不漏。"""
        events = make_history(5_000)
        load(events)
        query = {"user": "bi", "start": 1_000_100, "end": 1_004_900}
        expected = [e for e in events
                    if e["user"] == "bi"
                    and 1_000_100 <= e["ts"] <= 1_004_900]
        first = search(query, size=97)
        self.assertEqual(first["total"], len(expected))
        hits = collect_all(first)
        self.assertEqual(len(hits), len(expected))              # 不漏
        self.assertEqual(len({h["rid"] for h in hits}), len(hits))  # 不重
        self.assertEqual({h["rid"] for h in hits},
                         {e["rid"] for e in expected})

    def test_cursor_stable_under_insert(self):
        """翻页途中插入新数据：游标不重复，且给出变化提示。"""
        load(make_history(200))
        first = search({}, size=30)
        seen = {h["rid"] for h in first["hits"]}
        cur = first["cursor"]
        load(make_history(220))  # 模拟新数据写入（多了 20 条更新的）
        rest = []
        first_page = page(cur)
        self.assertTrue(first_page["stale"])  # 数据变化有提示
        rest.extend(first_page["hits"])
        cur = first_page["cursor"]
        while cur:
            res = page(cur)
            self.assertFalse(res["stale"])  # 新游标已对齐，不再误报
            rest.extend(res["hits"])
            cur = res["cursor"]
        self.assertFalse(seen & {h["rid"] for h in rest})  # 不重复
        # 旧数据（rid < 200）仍被完整覆盖
        self.assertEqual({h["rid"] for h in rest if h["rid"] < 200},
                         set(range(200)) - seen)

    def test_scan_fallback_matches_index(self):
        """索引不可用时退回扫描，结果与索引路径一致。"""
        load(make_history(1_000))
        query = {"user": "app", "start": 1_000_500}
        via_index = collect_all(search(query, size=13))
        configure(use_index=False)
        res = search(query, size=13)
        self.assertFalse(res["index"])  # 确认走了扫描路径
        via_scan = collect_all(res)
        self.assertEqual([h["rid"] for h in via_index],
                         [h["rid"] for h in via_scan])

    def test_truncated_and_limit_configurable(self):
        load(make_history(300))
        configure(max_hits=50)
        res = search({})
        self.assertTrue(res["truncated"])
        self.assertEqual(res["total"], 50)
        configure(max_hits=1_000)
        self.assertFalse(search({})["truncated"])

    def test_page_size_capped(self):
        load(make_history(100))
        configure(max_page_size=20)
        self.assertLessEqual(len(search({}, size=999)["hits"]), 20)

    def test_result_fields(self):
        load(make_history(25))
        res = search({"user": "etl"}, size=5)
        for key in ("hits", "total", "cursor", "truncated", "stale"):
            self.assertIn(key, res)
        cur = res["cursor"]
        self.assertEqual(cur["query"], {"user": "etl"})  # 游标带查询条件
        self.assertIn("last", cur)                       # 和位置标记


if __name__ == "__main__":
    unittest.main()
