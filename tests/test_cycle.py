import json
import os
import subprocess
import sys
import tempfile
import unittest

from pagecycle import walk
from pagecycle.walk import normalize_key


def ring_pages():
    return {
        "p1": {"url": "p1", "next": "p2"},
        "p2": {"url": "p2", "next": "p3"},
        "p3": {"url": "p3", "next": "p1"},
    }


class CrossPageCycleTest(unittest.TestCase):
    def test_cross_page_cycle_stops(self):
        pages = ring_pages()
        result = walk(lambda u: pages[u], start="p1", limit=10, stop_on_cycle=True)
        self.assertEqual(result, ["p1", "p2", "p3"])
        self.assertEqual(result.stop_reason, "cycle")
        self.assertEqual(result.repeated, "p1")

    def test_default_keeps_legacy_behavior(self):
        pages = ring_pages()
        result = walk(lambda u: pages[u], start="p1", limit=5)
        self.assertEqual(result, ["p1", "p2", "p3", "p1", "p2"])
        self.assertEqual(result.stop_reason, "limit")


class NormalizeKeyTest(unittest.TestCase):
    def test_sort_display_session_params_ignored(self):
        base = "http://shop/list?page=2"
        variants = [
            "http://shop/list?page=2&sort=price",
            "http://shop/list?page=2&order=desc&dir=asc",
            "http://shop/list?page=2&view=grid&theme=dark",
            "http://shop/list?page=2&sid=abc&session_id=xyz",
            "http://shop/list?page=2&utm_source=feed",
        ]
        keys = {normalize_key(u) for u in [base] + variants}
        self.assertEqual(len(keys), 1)

    def test_key_params_distinguish_pages(self):
        self.assertNotEqual(
            normalize_key("http://shop/list?page=1"),
            normalize_key("http://shop/list?page=2"),
        )
        self.assertNotEqual(
            normalize_key("http://shop/list?cursor=abc"),
            normalize_key("http://shop/list?cursor=def"),
        )

    def test_param_order_normalized(self):
        self.assertEqual(
            normalize_key("http://shop/list?b=2&a=1"),
            normalize_key("http://shop/list?a=1&b=2"),
        )

    def test_same_page_with_sort_params_stops_cycle(self):
        pages = {
            "http://shop/list?page=1": {
                "url": "http://shop/list?page=1",
                "next": "http://shop/list?page=1&sort=price",
            },
            "http://shop/list?page=1&sort=price": {
                "url": "http://shop/list?page=1&sort=price",
                "next": None,
            },
        }
        result = walk(
            lambda u: pages[u],
            start="http://shop/list?page=1",
            stop_on_cycle=True,
        )
        self.assertEqual(result, ["http://shop/list?page=1"])
        self.assertEqual(result.stop_reason, "repeat")


class MixedModeTest(unittest.TestCase):
    def test_page_and_cursor_mixed_cycle(self):
        pages = {
            "http://shop/list?page=1": {
                "url": "http://shop/list?page=1",
                "next": "http://shop/list?page=2",
            },
            "http://shop/list?page=2": {
                "url": "http://shop/list?page=2",
                "next": "http://shop/list?cursor=abc",
            },
            "http://shop/list?cursor=abc": {
                "url": "http://shop/list?cursor=abc",
                "next": "http://shop/list?page=1&sort=price",
            },
        }
        result = walk(
            lambda u: pages[u],
            start="http://shop/list?page=1",
            limit=10,
            stop_on_cycle=True,
        )
        self.assertEqual(
            result,
            [
                "http://shop/list?page=1",
                "http://shop/list?page=2",
                "http://shop/list?cursor=abc",
            ],
        )
        self.assertEqual(result.stop_reason, "cycle")


class ConsecutiveRepeatTest(unittest.TestCase):
    def test_self_loop_stops_immediately(self):
        calls = []

        def fetch(u):
            calls.append(u)
            return {"url": u, "next": "http://shop/list?page=2&sort=price"}

        result = walk(fetch, start="http://shop/list?page=2",
                      limit=10, stop_on_cycle=True)
        self.assertEqual(result, ["http://shop/list?page=2"])
        self.assertEqual(result.stop_reason, "repeat")
        self.assertEqual(len(calls), 1)


class StopReasonTest(unittest.TestCase):
    def test_done_reason(self):
        pages = {"p1": {"url": "p1", "next": None}}
        result = walk(lambda u: pages[u], start="p1", stop_on_cycle=True)
        self.assertEqual(result.stop_reason, "done")

    def test_limit_reason(self):
        pages = ring_pages()
        result = walk(lambda u: pages[u], start="p1", limit=4)
        self.assertEqual(result.stop_reason, "limit")

    def test_cycle_reason(self):
        pages = ring_pages()
        result = walk(lambda u: pages[u], start="p1", stop_on_cycle=True)
        self.assertEqual(result.stop_reason, "cycle")


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_state_persisted_to_disk(self):
        pages = ring_pages()
        walk(lambda u: pages[u], start="p1", limit=2,
             stop_on_cycle=True, state_path=self.state_path)
        with open(self.state_path, encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(state["pages"], ["p1", "p2"])
        self.assertEqual(state["visited"], ["p1", "p2"])
        self.assertEqual(state["next"], "p3")

    def test_resume_reuses_visited_set(self):
        pages = ring_pages()
        first = walk(lambda u: pages[u], start="p1", limit=2,
                     stop_on_cycle=True, state_path=self.state_path)
        self.assertEqual(first.stop_reason, "limit")
        second = walk(lambda u: pages[u], start="p1", limit=10,
                      stop_on_cycle=True, state_path=self.state_path,
                      resume=True)
        self.assertEqual(second, ["p1", "p2", "p3"])
        self.assertEqual(second.stop_reason, "cycle")
        self.assertEqual(second.repeated, "p1")

    def test_resume_across_processes(self):
        pages = ring_pages()
        walk(lambda u: pages[u], start="p1", limit=2,
             stop_on_cycle=True, state_path=self.state_path)
        code = (
            "from pagecycle import walk;"
            "pages={'p1':{'url':'p1','next':'p2'},"
            "'p2':{'url':'p2','next':'p3'},"
            "'p3':{'url':'p3','next':'p1'}};"
            "r=walk(lambda u: pages[u], start='p1', limit=10,"
            f"stop_on_cycle=True, state_path={self.state_path!r}, resume=True);"
            "print(list(r));"
            "print(r.stop_reason)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.splitlines(), ["['p1', 'p2', 'p3']", "cycle"])


if __name__ == "__main__":
    unittest.main()
