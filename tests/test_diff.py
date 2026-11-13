import os
import tempfile
import unittest

from listwatch import (
    ChangeThresholdExceeded,
    Store,
    diff,
    fingerprint,
    merge_pages,
    normalize_text,
    stable_key,
)


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

    def test_stable_key_prefers_detail_link_then_biz_id(self):
        item = {"id": "A-1", "detail_url": "https://s.example.com/p/9", "title": "标题"}
        self.assertEqual(
            stable_key(item), "url:https://s.example.com/p/9"
        )
        self.assertEqual(
            stable_key({"id": "A-1", "title": "标题"}), "id:id:A-1"
        )
        self.assertEqual(
            stable_key({"title": " 标题!"}),
            stable_key({"title": "标题"}),
        )
        # 跟踪参数、fragment 和结尾斜杠不影响稳定键
        self.assertEqual(
            stable_key({"url": "https://s.example.com/p/9?utm_source=x#frag/"}),
            stable_key({"url": "https://s.example.com/p/9"}),
        )

    def test_fingerprint_ignores_whitespace_punctuation_and_status(self):
        base = {"title": "Hello, World!", "summary": "a b"}
        noisy = {"title": "Hello   World", "summary": "a  b！", "status": "offline"}
        self.assertEqual(fingerprint(base), fingerprint(noisy))
        changed = {"title": "Hello, World!", "summary": "a c"}
        self.assertNotEqual(fingerprint(base), fingerprint(changed))
        self.assertEqual(normalize_text("  A，B！"), normalize_text("ab"))

    def test_merge_pages_deduplicates_by_stable_key(self):
        page_one = [
            {"id": "1", "title": "一"},
            {"id": "2", "title": "二"},
        ]
        page_two = [
            {"id": "2", "title": "二（分页边界重复）"},
            {"id": "3", "title": "三"},
        ]
        merged = merge_pages([page_one, page_two])
        self.assertEqual([item["id"] for item in merged], ["1", "2", "3"])
        # 重复条目保留首次出现的版本
        self.assertEqual(merged[1]["title"], "二")

    def test_reordered_items_are_reported_by_relative_order(self):
        old = [{"id": "1", "title": "一"}, {"id": "2", "title": "二"}]
        new = [{"id": "2", "title": "二"}, {"id": "1", "title": "一"}]
        result = diff(old, new)
        self.assertEqual(result["added"], [])
        self.assertEqual(result["removed"], [])
        self.assertEqual(
            {entry["key"]: (entry["old_index"], entry["new_index"])
             for entry in result["reordered"]},
            {"id:id:2": (1, 0), "id:id:1": (0, 1)},
        )
        # 纯新增导致的位移不算顺序变化
        shifted = diff(old, [{"id": "0", "title": "零"}] + old)
        self.assertNotIn("reordered", shifted)

    def test_removed_vs_delisted(self):
        old = [
            {"id": "1", "title": "在售", "status": "online"},
            {"id": "2", "title": "待下架", "status": "online"},
        ]
        new = [
            {"id": "2", "title": "待下架", "status": "offline"},
        ]
        result = diff(old, new)
        # 键完全消失：真删除；键还在但状态变下架：下架
        self.assertEqual([item["id"] for item in result["removed"]], ["1"])
        self.assertEqual([item["id"] for item in result["delisted"]], ["2"])
        self.assertNotIn("updated", result)

    def test_store_keeps_incremental_state_and_raises_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = os.path.join(tmp, "state.json")
            store = Store(state_path)

            first = store.run("news", [{"id": "1", "title": "一"}])
            self.assertTrue(first["baseline"])
            self.assertEqual(first["change_count"], 0)
            self.assertEqual(first["added"], [])

            with self.assertRaises(ChangeThresholdExceeded) as ctx:
                store.run(
                    "news",
                    [
                        {"id": "1", "title": "一"},
                        {"id": "2", "title": "二"},
                        {"id": "3", "title": "三"},
                    ],
                    alert_threshold=1,
                )
            self.assertEqual(ctx.exception.change_count, 2)

            # 告警后状态仍已落盘，新实例能读到最新快照
            reloaded = Store(state_path)
            self.assertEqual(
                [item["id"] for item in reloaded.get_snapshot("news")],
                ["1", "2", "3"],
            )

            # 站点之间状态互不干扰
            other = store.run("shop", [{"id": "x", "title": "x"}])
            self.assertTrue(other["baseline"])
