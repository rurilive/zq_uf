import unittest

from dirprobe import load, probe, report, update

WORDS = ["admin", "backup.zip", "login", "static", "robots.txt", "images"]
RESPONSES = {
    "admin": {"status": 200},
    "backup.zip": {"status": 200},
    "login": {"status": 403},
    "static": {"status": 429, "body": "too many requests"},
    "robots.txt": {"status": 200},
    "images": {"status": 301},
}


class BlockTest(unittest.TestCase):
    def test_blocked_page_not_counted_as_hit(self):
        load("shop", WORDS, RESPONSES)
        out = probe("shop")
        paths = [hit["path"] for hit in out["hits"]]
        self.assertNotIn("static", paths)
        self.assertEqual(len(out["hits"]), 5)
        self.assertEqual(out["blocked"], 1)

    def test_blocked_paths_listed_in_report(self):
        load("shop", WORDS, RESPONSES)
        probe("shop")
        rep = report("shop")
        self.assertEqual(rep["blocked_paths"], ["static"])
        self.assertEqual(rep["blocked"], 1)

    def test_block_page_body_marked_as_blocked(self):
        load("waf", ["a", "b"], {"a": {"status": 200, "body": "access denied by waf"},
                                 "b": {"status": 200, "body": "hello"}})
        out = probe("waf")
        self.assertEqual([hit["path"] for hit in out["hits"]], ["b"])
        self.assertEqual(out["blocked"], 1)


class ConcurrencyTest(unittest.TestCase):
    def test_concurrency_drops_on_block(self):
        load("one", ["static"], {"static": {"status": 429}})
        out = probe("one")
        self.assertEqual(out["concurrency"], 4)

    def test_concurrency_drops_on_slow_response(self):
        load("slow", ["a"], {"a": {"status": 200, "latency_ms": 1500}})
        out = probe("slow")
        self.assertEqual(out["concurrency"], 4)
        self.assertEqual(out["hits"][0]["path"], "a")  # 慢不等于拦截，仍算命中

    def test_concurrency_recovers_after_clean_streak(self):
        words = ["blocked"] + ["w%d" % i for i in range(8)]
        responses = {"blocked": {"status": 429}}
        responses.update({"w%d" % i: {"status": 404} for i in range(8)})
        load("rec", words, responses)
        out = probe("rec")
        self.assertGreater(out["concurrency"], 4)
        self.assertLessEqual(out["concurrency"], 8)


class ResumeTest(unittest.TestCase):
    def _load_refusing_site(self):
        words = ["a", "b", "c", "x", "y", "z", "d"]
        responses = {
            "a": {"status": 200}, "b": {"status": 404}, "c": {"status": 404},
            "x": {"error": "connection refused"},
            "y": {"error": "connection refused"},
            "z": {"error": "connection refused"},
            "d": {"status": 200},
        }
        load("site", words, responses)
        return words

    def test_refusal_stops_round_and_keeps_progress(self):
        words = self._load_refusing_site()
        out = probe("site")
        self.assertTrue(out["stopped"])
        self.assertEqual(out["resume"], 3)  # a/b/c 已确认，x 起未确认
        rep = report("site")
        self.assertTrue(rep["refused"])
        self.assertEqual(rep["done"], 3)
        self.assertEqual(rep["total"], len(words))
        self.assertEqual(rep["hits"], 1)  # 拒绝页不算空结果，已有命中保留

    def test_resume_continues_from_last_position(self):
        words = self._load_refusing_site()
        probe("site")
        update("site", {"x": {"status": 404}, "y": {"status": 404},
                        "z": {"status": 404}})
        out = probe("site")
        self.assertFalse(out["stopped"])
        self.assertEqual(out["resume"], len(words))
        self.assertEqual([hit["path"] for hit in out["hits"]], ["a", "d"])

    def test_confirmed_paths_not_requested_again(self):
        self._load_refusing_site()
        probe("site")
        # 目标侧 a 的响应变了，但 a 已确认，续跑不应重复请求
        update("site", {"a": {"status": 429, "body": "too many requests"},
                        "x": {"status": 404}, "y": {"status": 404},
                        "z": {"status": 404}})
        out = probe("site")
        self.assertIn("a", [hit["path"] for hit in out["hits"]])
        self.assertEqual(out["blocked"], 0)


class GroupTest(unittest.TestCase):
    def test_hits_grouped_by_category(self):
        load("shop", WORDS, RESPONSES)
        probe("shop")
        groups = report("shop")["groups"]
        self.assertEqual(groups["admin"], ["admin", "login"])
        self.assertEqual(groups["backup"], ["backup.zip"])
        self.assertEqual(groups["file"], ["robots.txt"])
        self.assertEqual(groups["dir"], ["images"])

    def test_same_path_params_merged(self):
        load("params", ["login?u=a", "login?u=b", "admin"],
             {"login?u=a": {"status": 200}, "login?u=b": {"status": 200},
              "admin": {"status": 200}})
        out = probe("params")
        self.assertEqual(len(out["hits"]), 2)
        login = [h for h in out["hits"] if h["path"] == "login"][0]
        self.assertEqual(sorted(login["params"]), ["u=a", "u=b"])


class LoadTest(unittest.TestCase):
    def test_reject_empty_wordlist(self):
        with self.assertRaises(ValueError):
            load("shop", [], {})

    def test_report_before_probe_raises(self):
        load("fresh", ["a"], {})
        with self.assertRaises(ValueError):
            report("fresh")


if __name__ == "__main__":
    unittest.main()
