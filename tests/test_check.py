import importlib
import json
import os
import tempfile
import threading
import time
import unittest

from linkcheck import check, report
from linkcheck import reset, set_transport, site_report
from linkcheck.check import DEAD, ERROR, LIMIT, LOOP, REDIRECT
from linkcheck.transport import (
    CAT_CONNECTION,
    CAT_INVALID,
    CAT_TIMEOUT,
    FetchError,
    FetchResponse,
)

module = importlib.import_module("linkcheck.check")


class CheckTest(unittest.TestCase):
    def setUp(self):
        module._results.clear()

    def test_checks_all(self):
        self.assertEqual(check(["a", "b"])["checked"], 2)

    def test_workers_ignored(self):
        self.assertEqual(check(["a"], 8)["workers"], 1)

    def test_report_rows(self):
        check(["a"])
        self.assertEqual(report()[0]["url"], "a")


class FakeTransport:
    """脚本化假传输：routes 映射 URL -> (status, headers) / FetchError / 可调用。"""

    def __init__(self, routes=None, latency=0.0):
        self.routes = routes or {}
        self.latency = latency
        self.calls = []
        self.timestamps = []
        self.barrier = None
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def request(self, url, method="HEAD"):
        with self._lock:
            self.calls.append((url, method))
            self.timestamps.append(time.monotonic())
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.barrier is not None:
                self.barrier.wait(timeout=5)
            if self.latency:
                time.sleep(self.latency)
            rule = self._match(url)
            if isinstance(rule, FetchError):
                raise rule
            if callable(rule):
                rule = rule(url, method)
            status, headers = rule
            return FetchResponse(status, dict(headers), url)
        finally:
            with self._lock:
                self.active -= 1

    def _match(self, url):
        if url in self.routes:
            return self.routes[url]
        for prefix, rule in self.routes.items():
            if url.startswith(prefix):
                return rule
        raise AssertionError(f"unexpected url: {url}")


class ConcurrentDedupTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.transport = FakeTransport({
            "http://a/1": (200, {}),
            "http://a/2": (200, {}),
        })
        set_transport(self.transport)

    def tearDown(self):
        reset()

    def test_duplicate_url_checked_once(self):
        summary = check(
            ["http://a/1", "http://a/1", "http://a/1", "http://a/2"],
            workers=4,
        )
        requested = [u for u, _ in self.transport.calls]
        self.assertEqual(sorted(requested), ["http://a/1", "http://a/2"])
        self.assertEqual(summary["checked"], 4)
        self.assertEqual(summary["unique"], 2)
        rows = {row["url"]: row for row in report()}
        self.assertEqual(rows["http://a/1"]["references"], 3)
        self.assertEqual(rows["http://a/2"]["references"], 1)

    def test_requests_run_concurrently(self):
        # 四个不同站点、workers=4；不并发则屏障超时失败。
        self.transport.routes = {
            f"http://s{i}/x": (200, {}) for i in range(4)
        }
        self.transport.barrier = threading.Barrier(4)
        summary = check(
            [f"http://s{i}/x" for i in range(4)], workers=4
        )
        self.assertEqual(summary["workers"], 4)
        self.assertEqual(self.transport.max_active, 4)

    def test_workers_capped_by_task_count(self):
        summary = check(["http://a/1", "http://a/2"], workers=8)
        self.assertEqual(summary["workers"], 2)

    def test_result_reused_across_checks(self):
        check(["http://a/1", "http://a/2"], workers=2)
        self.assertEqual(len(self.transport.calls), 2)
        summary = check(["http://a/1", "http://a/1", "http://a/2"], workers=2)
        # 第二轮全部命中缓存，没有新请求；cached 按唯一 URL 计数。
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(summary["cached"], 2)
        self.assertEqual(summary["checked"], 3)
        rows = {row["url"]: row for row in report()}
        self.assertEqual(rows["http://a/1"]["references"], 3)
        self.assertEqual(rows["http://a/2"]["references"], 2)


class RedirectChainTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.transport = FakeTransport()
        set_transport(self.transport)

    def tearDown(self):
        reset()

    def test_full_redirect_chain_recorded(self):
        self.transport.routes = {
            "http://a/start": (301, {"Location": "/mid"}),
            "http://a/mid": (302, {"Location": "http://a/end"}),
            "http://a/end": (200, {}),
        }
        check(["http://a/start"], workers=1)
        row = report()[0]
        self.assertEqual(row["status"], REDIRECT)
        self.assertEqual(row["hops"], 2)
        self.assertEqual(row["final_url"], "http://a/end")
        self.assertEqual(
            [hop["status"] for hop in row["chain"]], [301, 302, 200]
        )
        self.assertEqual(
            [hop["url"] for hop in row["chain"]],
            ["http://a/start", "http://a/mid", "http://a/end"],
        )

    def test_redirect_loop_marked(self):
        self.transport.routes = {
            "http://a/loop1": (301, {"Location": "/loop2"}),
            "http://a/loop2": (301, {"Location": "/loop1"}),
        }
        check(["http://a/loop1"], workers=1)
        row = report()[0]
        self.assertEqual(row["status"], LOOP)
        self.assertEqual(row["loop_index"], 0)
        self.assertEqual(
            [hop["url"] for hop in row["chain"]],
            ["http://a/loop1", "http://a/loop2"],
        )

    def test_redirect_limit_marked(self):
        self.transport.routes = {
            f"http://a/r{i}": (302, {"Location": f"/r{i + 1}"})
            for i in range(5)
        }
        check(["http://a/r0"], workers=1, max_redirects=2)
        row = report()[0]
        self.assertEqual(row["status"], LIMIT)
        self.assertEqual(row["category"], "too_many_redirects")
        self.assertEqual(len(row["chain"]), 2)


class FailureClassificationTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.transport = FakeTransport()
        set_transport(self.transport)

    def tearDown(self):
        reset()

    def test_4xx_classified_dead(self):
        self.transport.routes = {"http://a/missing": (404, {})}
        check(["http://a/missing"], workers=1)
        row = report()[0]
        self.assertEqual(row["status"], DEAD)
        self.assertEqual(row["http_status"], 404)
        self.assertEqual(row["category"], "http_404")

    def test_timeout_retried_with_backoff_then_error(self):
        self.transport.routes = {
            "http://a/slow": FetchError(CAT_TIMEOUT, "timed out")
        }
        started = time.monotonic()
        check(["http://a/slow"], workers=1, retries=2, backoff=0.05)
        elapsed = time.monotonic() - started
        row = report()[0]
        self.assertEqual(row["status"], ERROR)
        self.assertEqual(row["category"], CAT_TIMEOUT)
        self.assertEqual(row["attempts"], 3)  # 初始 + 2 次退避重试
        # 退避序列 0.05 + 0.10
        self.assertGreaterEqual(elapsed, 0.14)

    def test_invalid_url_not_retried(self):
        self.transport.routes = {
            "bad-url": FetchError(CAT_INVALID, "unknown url type")
        }
        check(["bad-url"], workers=1, retries=3, backoff=0.05)
        row = report()[0]
        self.assertEqual(row["status"], ERROR)
        self.assertEqual(row["category"], CAT_INVALID)
        self.assertEqual(row["attempts"], 1)

    def test_5xx_retried_then_dead(self):
        self.transport.routes = {"http://a/down": (503, {})}
        check(["http://a/down"], workers=1, retries=1, backoff=0.01)
        row = report()[0]
        self.assertEqual(row["status"], DEAD)
        self.assertEqual(row["category"], "http_503")
        self.assertEqual(row["attempts"], 2)

    def test_connection_error_category(self):
        self.transport.routes = {
            "http://a/x": FetchError(CAT_CONNECTION, "refused")
        }
        summary = check(["http://a/x"], workers=1, retries=0)
        self.assertEqual(summary["by_category"][CAT_CONNECTION], 1)
        self.assertEqual(summary["dead"], 1)


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.transport = FakeTransport({
            "http://same/1": (200, {}),
            "http://same/2": (200, {}),
            "http://same/3": (200, {}),
        })
        set_transport(self.transport)

    def tearDown(self):
        reset()

    def test_same_site_throttled_across_workers(self):
        # workers 再大，同站点请求也必须被最小间隔拉开。
        check(
            ["http://same/1", "http://same/2", "http://same/3"],
            workers=4, site_interval=0.2,
        )
        stamps = sorted(self.transport.timestamps)
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(len(gaps), 2)
        self.assertTrue(all(gap >= 0.18 for gap in gaps), gaps)


class SiteReportTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.transport = FakeTransport({
            "http://a/ok": (200, {}),
            "http://a/gone": (404, {}),
            "http://a/move": (301, {"Location": "http://a/ok"}),
            "http://b/ok": (200, {}),
        })
        set_transport(self.transport)

    def tearDown(self):
        reset()

    def test_summary_grouped_by_site(self):
        check(
            ["http://a/ok", "http://a/gone", "http://a/move", "http://b/ok"],
            workers=2,
        )
        sites = {item["site"]: item for item in site_report()}
        self.assertEqual(sites["a"]["urls"], 3)
        self.assertEqual(sites["a"]["dead"], 1)
        self.assertEqual(sites["a"]["redirects"], 1)
        self.assertEqual(sites["b"]["urls"], 1)
        self.assertEqual(sites["b"]["dead"], 0)

    def test_status_and_category_counts(self):
        summary = check(
            ["http://a/ok", "http://a/gone", "http://a/move", "http://b/ok"],
            workers=2,
        )
        self.assertEqual(summary["by_status"]["ok"], 2)
        self.assertEqual(summary["by_status"]["dead"], 1)
        self.assertEqual(summary["by_status"]["redirect"], 1)
        self.assertEqual(summary["by_category"]["http_404"], 1)


class IncrementalExportTest(unittest.TestCase):
    def setUp(self):
        reset()
        self.transport = FakeTransport({
            "http://a/1": (200, {}),
            "http://a/2": (404, {}),
            "http://a/3": (200, {}),
        })
        set_transport(self.transport)
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        reset()
        os.unlink(self.path)

    def test_only_new_rows_exported(self):
        first = check(
            ["http://a/1", "http://a/2"], workers=2, export_path=self.path
        )
        self.assertEqual(first["exported"], 2)
        second = check(
            ["http://a/1", "http://a/3"], workers=2, export_path=self.path
        )
        # http://a/1 已导出且命中缓存，只增量写 http://a/3。
        self.assertEqual(second["exported"], 1)
        with open(self.path, encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual([row["url"] for row in lines],
                         ["http://a/1", "http://a/2", "http://a/3"])


if __name__ == "__main__":
    unittest.main()
