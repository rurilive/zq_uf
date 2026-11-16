import json
import os
import shutil
import tempfile
import unittest

from dirprobe import load, probe, report, reset
from dirprobe.probe import DEFAULT_FINGERPRINTS

WORDS = ["admin", "backup.zip", "login", "static"]
RESPONSES = {
    "admin": {"status": 200},
    "backup.zip": {"status": 200},
    "login": {"status": 403},
    "static": {"status": 429, "body": "too many requests"},
}


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.state_dir = tempfile.mkdtemp(prefix="dirprobe-test-")
        self.addCleanup(shutil.rmtree, self.state_dir, ignore_errors=True)

    def _load(self, site="shop", words=WORDS, responses=RESPONSES, **kwargs):
        return load(site, words, responses, state_dir=self.state_dir, **kwargs)

    def _state_file(self, site="shop"):
        return os.path.join(self.state_dir, f"{site}.json")

    # ---- 拦截页不再算命中 ----
    def test_blocked_page_not_counted_as_hit(self):
        self._load()
        out = probe("shop")
        hit_paths = [hit["path"] for hit in out["hits"]]
        self.assertNotIn("static", hit_paths)
        self.assertEqual(len(out["hits"]), 3)
        self.assertEqual(out["blocked"], 1)

    def test_fingerprint_body_is_blocked(self):
        words = ["a", "b"]
        responses = {
            "a": {"status": 200, "body": "正常页面"},
            "b": {"status": 200, "body": "访问频繁，请稍后再试"},
        }
        self._load(words=words, responses=responses)
        out = probe("shop")
        self.assertEqual([hit["path"] for hit in out["hits"]], ["a"])
        self.assertEqual(out["blocked_paths"], ["b"])
        # 本轮每条路径只请求一次：b 被拦后留待后续轮次，拦截次数为 1。
        self.assertEqual(out["blocked"], 1)
        self.assertEqual(out["settled_blocked"], [])
        self.assertEqual(out["resume"], 1)

    def test_persistent_block_settles_across_rounds(self):
        words = ["a", "b"]
        responses = {
            "a": {"status": 200},
            "b": {"status": 429, "body": "too many requests"},
        }
        self._load(words=words, responses=responses)
        for _ in range(3):
            probe("shop")
        out = probe("shop")  # 第四次调用：预算早已用尽，不再重试 b
        self.assertEqual(out["blocked"], 3)
        self.assertEqual(out["settled_blocked"], ["b"])
        self.assertEqual(out["resume"], 2)
        self.assertFalse(out["stopped"])
        # 用尽预算后继续调用也不会重复请求 b。
        requests_before = out["requests"]
        again = probe("shop")
        self.assertEqual(again["requests"], requests_before)

    def test_403_is_a_hit_not_block(self):
        self._load()
        out = probe("shop")
        login = next(hit for hit in out["hits"] if hit["path"] == "login")
        self.assertEqual(login["status"], 403)
        self.assertEqual(login["category"], "admin")

    # ---- 自适应并发 ----
    def test_block_drops_concurrency(self):
        # 起始并发 8，第一批里包含被拦截的 static，并发应立即下降。
        self._load()
        out = probe("shop", concurrency=8)
        self.assertLess(out["concurrency"], 8)

    def test_slow_response_drops_concurrency(self):
        words = [f"p{i}" for i in range(8)]
        responses = {word: {"status": 200, "ms": 3000} for word in words}
        self._load(words=words, responses=responses)
        out = probe("shop", concurrency=8)
        self.assertLess(out["concurrency"], 8)
        # 变慢不是拦截：路径仍算命中，也没有拦截计数。
        self.assertEqual(len(out["hits"]), 8)
        self.assertEqual(out["blocked"], 0)

    def test_concurrency_recovers_on_clean_batches(self):
        words = [f"p{i}" for i in range(12)]
        responses = {word: {"status": 200} for word in words}
        self._load(words=words, responses=responses)
        out = probe("shop", concurrency=4)
        # 全部干净，并发最终回到上限。
        self.assertEqual(out["concurrency"], 4)
        self.assertGreaterEqual(out["requests"], 12)

    def test_concurrency_never_below_one(self):
        # 足够多持续被拦的新路径，让并发一路下调到下限。
        words = [f"p{i}" for i in range(12)]
        responses = {word: {"status": 429} for word in words}
        self._load(words=words, responses=responses)
        out = probe("shop", concurrency=8)
        self.assertEqual(out["concurrency"], 7)  # 首个拦截批次下调一档
        # 不断有新路径被拦，并发继续下调，但绝不会低于 1。
        for _ in range(10):
            out = probe("shop", concurrency=8)
        self.assertEqual(out["concurrency"], 1)
        # 单条路径的个例拦截不会误判成站点整体拒绝（新路径不足 3 条时）。
        load("one", ["only"], {"only": {"status": 429}}, state_dir=self.state_dir)
        single = probe("one", concurrency=4)
        self.assertFalse(single["stopped"])

    # ---- 断点续跑 ----
    def test_resume_after_partial_run(self):
        words = [f"p{i}" for i in range(10)]
        responses = {word: {"status": 200} for word in words}
        self._load(words=words, responses=responses)
        first = probe("shop", concurrency=4, limit=4)
        self.assertEqual(first["resume"], 4)
        self.assertEqual(first["requests"], 4)

        second = probe("shop", concurrency=4, limit=4)
        self.assertEqual(second["resume"], 8)
        # 每批最多 4 条，本轮只发 4 个请求，没有重跑前 4 条。
        self.assertEqual(second["requests"], 8)
        requested = {p for p in words if _request_count(self, p)}
        self.assertEqual(len(requested), 8)

        third = probe("shop", concurrency=4)
        self.assertEqual(third["resume"], 10)
        self.assertEqual(third["requests"], 10)

    def test_resume_persists_across_process_reload(self):
        words = [f"p{i}" for i in range(6)]
        responses = {word: {"status": 200} for word in words}
        self._load(words=words, responses=responses)
        probe("shop", concurrency=2, limit=2)
        self.assertTrue(os.path.exists(self._state_file()))

        # 重新 load 等同进程重启：进度从落盘文件恢复。
        self._load(words=words, responses=responses)
        with open(self._state_file(), encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertEqual(saved["cursor"], 2)
        out = probe("shop", concurrency=2)
        self.assertEqual(out["resume"], 6)
        self.assertEqual(out["requests"], 6)

    def test_blocked_paths_retried_not_skipped(self):
        # static 第一轮 429；改为放行后续跑，应能命中且不重复请求已确认路径。
        self._load()
        first = probe("shop", concurrency=8, limit=4)
        # 前三条已有结论，static 被拦成空洞，游标停在 3。
        self.assertEqual(first["resume"], 3)
        self.assertEqual(first["blocked_paths"], ["static"])
        self.assertEqual(first["requests"], 4)

        responses = dict(RESPONSES)
        responses["static"] = {"status": 200}
        self._load(responses=responses)
        second = probe("shop")
        hit_paths = {hit["path"] for hit in second["hits"]}
        self.assertIn("static", hit_paths)
        # 只补请求了 static 一条，已确认的三条没有重复请求。
        self.assertEqual(second["requests"], 5)
        self.assertEqual(second["resume"], 4)

    # ---- 站点整体拒绝访问 ----
    def test_site_wide_refusal_stops_round(self):
        words = [f"p{i}" for i in range(10)]
        responses = {word: {"status": 429} for word in words}
        self._load(words=words, responses=responses)
        out = probe("shop", concurrency=4)
        self.assertTrue(out["stopped"])
        self.assertEqual(out["hits"], [])
        # 没有把拒绝页算成空结果后清掉进度：拦截与续跑位置都留着。
        self.assertGreaterEqual(out["blocked"], 3)
        self.assertEqual(out["progress"]["done"], 0)
        self.assertTrue(out["blocked_batches"])

    def test_reprobe_clears_stopped_flag(self):
        words = ["p1", "p2", "p3", "p4", "p5"]
        responses = {word: {"status": 429} for word in words}
        self._load(words=words, responses=responses)
        stopped = probe("shop")
        self.assertTrue(stopped["stopped"])

        responses = {word: {"status": 200} for word in words}
        self._load(words=words, responses=responses)
        out = probe("shop")
        self.assertFalse(out["stopped"])
        self.assertEqual(len(out["hits"]), 5)

    # ---- 命中归类与参数合并 ----
    def test_hit_categories(self):
        words = [
            "assets/",               # 目录（结尾斜杠）
            "index.html",            # 文件
            "db/backup.sql",         # 备份
            "admin/login.php",       # 管理入口
        ]
        responses = {word: {"status": 200} for word in words}
        self._load(words=words, responses=responses)
        probe("shop")
        rep = report("shop")
        self.assertEqual(
            {category: [h["path"] for h in rep["groups"][category]]
             for category in rep["groups"]},
            {
                "directory": ["assets/"],
                "file": ["index.html"],
                "backup": ["db/backup.sql"],
                "admin": ["admin/login.php"],
            },
        )
        self.assertEqual(rep["group_counts"],
                         {"directory": 1, "file": 1, "backup": 1, "admin": 1})

    def test_extensionless_endpoint_is_file(self):
        words = ["search", "assets/"]
        responses = {word: {"status": 200} for word in words}
        self._load(words=words, responses=responses)
        probe("shop")
        rep = report("shop")
        self.assertEqual([h["path"] for h in rep["groups"]["file"]], ["search"])
        self.assertEqual([h["path"] for h in rep["groups"]["directory"]], ["assets/"])

    def test_same_path_different_params_merged(self):
        words = [
            "search?q=1",
            "search?q=2&page=3",
            "search",
        ]
        responses = {word: {"status": 200} for word in words}
        self._load(words=words, responses=responses)
        probe("shop")
        rep = report("shop")
        self.assertEqual(rep["hits"], 1)
        entry = rep["groups"]["file"][0]
        self.assertEqual(entry["path"], "search")
        self.assertEqual(entry["params"], ["page", "q"])
        self.assertEqual(entry["count"], 3)

    # ---- 报告与被拦截批次 ----
    def test_report_lists_blocked_batches_separately(self):
        self._load()
        probe("shop", concurrency=8)
        rep = report("shop")
        # 本轮 static 只被拦一次，批次明细单独列出。
        self.assertEqual(rep["blocked"], 1)
        self.assertEqual(rep["blocked_paths"], ["static"])
        self.assertEqual(rep["settled_blocked"], [])
        self.assertEqual(len(rep["blocked_batches"]), 1)
        batch = rep["blocked_batches"][0]
        self.assertEqual(batch["paths"], ["static"])
        self.assertEqual(batch["concurrency_before"], 8)
        self.assertEqual(batch["concurrency_after"], 7)
        self.assertIn("429", batch["reasons"])
        self.assertEqual(rep["progress"]["total"], 4)

    def test_report_before_probe_raises(self):
        self._load()
        with self.assertRaises(ValueError):
            report("other")

    def test_reset_clears_progress(self):
        words = [f"p{i}" for i in range(4)]
        responses = {word: {"status": 200} for word in words}
        self._load(words=words, responses=responses)
        probe("shop", concurrency=4, limit=2)
        reset("shop")
        out = probe("shop", concurrency=4)
        self.assertEqual(out["resume"], 4)
        # 状态清空后从头再来，四条路径各请求一次。
        self.assertEqual(out["requests"], 4)

    # ---- 入参校验 ----
    def test_reject_empty_wordlist(self):
        with self.assertRaises(ValueError):
            load("shop", [], {}, state_dir=self.state_dir)

    def test_reject_unknown_site(self):
        with self.assertRaises(ValueError):
            probe("nope")

    def test_default_fingerprints_are_substrings(self):
        self.assertTrue(all(isinstance(f, str) and f for f in DEFAULT_FINGERPRINTS))


def _request_count(test, path):
    state_file = test._state_file()
    with open(state_file, encoding="utf-8") as fh:
        saved = json.load(fh)
    return saved["requested"].get(path, 0)


if __name__ == "__main__":
    unittest.main()
