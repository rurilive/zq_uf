import json
import os
import tempfile
import unittest

from apipage import (
    CursorExpired,
    load_checkpoint,
    resume,
    save_checkpoint,
    walk,
)

PAGES = {1: {"items": [1], "next_page": 2}, 2: {"items": [2], "next_page": 3}, 3: {"items": []}}


class PageTest(unittest.TestCase):
    def test_walk_collects(self):
        self.assertEqual(walk(lambda p: PAGES[p])["items"], [1, 2])

    def test_walk_updates_page(self):
        self.assertEqual(walk(lambda p: PAGES[p])["page"], 3)

    def test_resume_page(self):
        self.assertEqual(resume({"page": 2})["page"], 2)


# 游标 API：首页 fetch(1)，之后 fetch(next_cursor)
CURSOR_PAGES = {
    1: {"items": [{"id": 1}], "next_cursor": "c2"},
    "c2": {"items": [{"id": 2}], "next_cursor": "c3"},
    "c3": {"items": [], "next_cursor": None},
}


class CursorExpiryTest(unittest.TestCase):
    def test_expired_refetches_home_and_skips_seen(self):
        pages = {
            1: {"items": [{"id": 1}], "next_cursor": "old-c2"},
            "old-c2": {"items": [{"id": 2}], "next_cursor": "old-c3"},
            "old-c3": None,
        }

        def fetch(token):
            if token == "old-c3":
                raise CursorExpired("cursor expired")
            return pages[token]

        # 服务端换了新游标，首页内容相同（id=1 已抓过，应整页跳过）
        new_pages = {
            1: {"items": [{"id": 1}], "next_cursor": "new-c2"},
            "new-c2": {"items": [{"id": 3}], "next_cursor": None},
        }
        expired_once = {"done": False}

        def fetch_twice(token):
            if not expired_once["done"]:
                try:
                    return fetch(token)
                except CursorExpired:
                    expired_once["done"] = True
                    raise
            return new_pages[token]

        result = walk(fetch_twice)
        self.assertEqual(result["items"], [{"id": 1}, {"id": 2}, {"id": 3}])
        self.assertEqual(result["expired"], 1)
        self.assertEqual(result["skipped_pages"], 1)
        self.assertEqual(result["duplicate"], False)

    def test_expired_on_home_propagates(self):
        def fetch(token):
            raise CursorExpired("home gone")

        with self.assertRaises(CursorExpired):
            walk(fetch)

    def test_same_cursor_expiring_twice_propagates(self):
        calls = {"n": 0}

        def fetch(token):
            calls["n"] += 1
            if token in ("c2", "c2b"):
                raise CursorExpired("still expired")
            if token == 1:
                # 每次回首页都给同一个坏游标，恢复无法收敛
                return {"items": [{"id": calls["n"]}], "next_cursor": "c2"}
            return {"items": [], "next_cursor": None}

        with self.assertRaises(CursorExpired):
            walk(fetch)


class DuplicateCursorTest(unittest.TestCase):
    def test_same_cursor_twice_stops(self):
        def fetch(token):
            if token == 1:
                return {"items": [{"id": 1}], "next_cursor": "loop"}
            return {"items": [{"id": 2}], "next_cursor": "loop"}

        result = walk(fetch)
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["duplicate_cursor"], "loop")
        self.assertEqual(result["items"], [{"id": 1}, {"id": 2}])

    def test_no_duplicate_on_normal_walk(self):
        result = walk(lambda t: CURSOR_PAGES[t])
        self.assertFalse(result["duplicate"])
        self.assertEqual(result["items"], [{"id": 1}, {"id": 2}])


class CheckpointTest(unittest.TestCase):
    def test_resume_from_state_continues_cursor(self):
        first = walk(lambda t: CURSOR_PAGES[t])
        self.assertEqual(first["pages"], 2)

        state = resume(first["state"])
        # 续抓时后端多了一页新数据
        pages = dict(CURSOR_PAGES)
        pages["c3"] = {"items": [{"id": 3}], "next_cursor": "c4"}
        pages["c4"] = {"items": [], "next_cursor": None}

        second = walk(lambda t: pages[t], state)
        self.assertEqual(second["items"], [{"id": 3}])
        self.assertTrue(second["resumed"])
        self.assertEqual(second["skipped_pages"], 0)
        self.assertEqual(second["state"]["seen_keys"], [1, 2, 3])

    def test_checkpoint_file_roundtrip(self):
        first = walk(lambda t: CURSOR_PAGES[t])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            save_checkpoint(path, first["state"])
            loaded = load_checkpoint(path)
        self.assertEqual(loaded["version"], 2)
        self.assertEqual(loaded, first["state"])

    def test_skip_whole_seen_page_after_resume(self):
        state = {
            "version": 2,
            "cursor": None,
            "page": 1,
            "seen_keys": [1, 2],
            "pages_fetched": 2,
        }
        pages = {
            1: {"items": [{"id": 1}, {"id": 2}], "next_cursor": "c2"},
            "c2": {"items": [{"id": 3}], "next_cursor": None},
        }
        result = walk(lambda t: pages[t], state)
        self.assertEqual(result["items"], [{"id": 3}])
        self.assertEqual(result["skipped_pages"], 1)
        self.assertTrue(result["resumed"])

    def test_old_format_checkpoint_migrates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "old.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"page": 3}, f)
            state = load_checkpoint(path)
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["page"], 3)
        self.assertEqual(state["cursor"], None)
        self.assertEqual(state["seen_keys"], [])
        self.assertEqual(state["pages_fetched"], 2)
        # 迁移后的状态能直接用于 walk
        self.assertEqual(resume({"page": 2})["page"], 2)

    def test_invalid_state_rejected(self):
        with self.assertRaises(ValueError):
            resume({"version": 2, "page": 0, "seen_keys": []})
        with self.assertRaises(ValueError):
            resume({"version": 99})
        with self.assertRaises(ValueError):
            resume("not-a-dict")

    def test_result_carries_self_contained_state(self):
        result = walk(lambda t: CURSOR_PAGES[t])
        state = result["state"]
        self.assertEqual(state["version"], 2)
        self.assertIn("cursor", state)
        self.assertIn("seen_keys", state)
        self.assertIn("page", state)
        self.assertIn("pages_fetched", state)
        self.assertEqual(result["page"], state["page"])


if __name__ == "__main__":
    unittest.main()
