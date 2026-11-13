import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from crawlquota import (
    CAPS,
    QuotaError,
    QuotaTimeoutError,
    acquire,
    release,
    release_all,
    reset,
    set_cap,
    set_wait_timeout,
    stats,
    task_usage,
    usage,
    wait_stats,
)


class QuotaTest(unittest.TestCase):
    def setUp(self):
        for kind in CAPS:
            release("t", kind, CAPS[kind])

    def test_acquire_increments(self):
        self.assertEqual(acquire("a", "thread", 2), 2)

    def test_over_cap_raises(self):
        acquire("a", "thread", CAPS["thread"])
        with self.assertRaises(RuntimeError):
            acquire("b", "thread", 1)

    def test_tasks_share_usage(self):
        acquire("a", "fd", 5)
        self.assertEqual(usage()["fd"], 5)
        release("a", "fd", 5)


class RuntimeQuotaTest(unittest.TestCase):
    def setUp(self):
        reset()

    def tearDown(self):
        reset()

    def test_three_kinds_accounted(self):
        acquire("a", "thread", 3)
        acquire("a", "fd", 4)
        acquire("b", "memory_mb", 100)
        self.assertEqual(usage(), {"thread": 3, "fd": 4, "memory_mb": 100})
        self.assertEqual(task_usage("a")["thread"], 3)
        self.assertEqual(task_usage("a")["fd"], 4)
        self.assertEqual(task_usage("b")["memory_mb"], 100)
        report = stats()
        self.assertEqual(report["used"]["fd"], 4)
        self.assertIn("a", report["tasks"])
        self.assertIn("b", report["tasks"])

    def test_acquire_exact_cap_boundary(self):
        self.assertEqual(acquire("a", "thread", CAPS["thread"]), CAPS["thread"])
        self.assertEqual(usage()["thread"], CAPS["thread"])

    def test_waits_until_release(self):
        acquire("a", "thread", CAPS["thread"])
        acquired = []

        def worker():
            acquired.append(acquire("b", "thread", 1, timeout=5.0))

        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(0.1)
        self.assertEqual(acquired, [])
        release("a", "thread", 1)
        thread.join(5.0)
        self.assertEqual(acquired, [CAPS["thread"]])
        stats_b = wait_stats("b")
        self.assertEqual(stats_b["wait_count"], 1)
        self.assertGreater(stats_b["wait_seconds"], 0)

    def test_timeout_raises_runtime_error(self):
        acquire("a", "memory_mb", CAPS["memory_mb"])
        started = time.monotonic()
        with self.assertRaises(QuotaTimeoutError) as ctx:
            acquire("b", "memory_mb", 1, timeout=0.1)
        self.assertGreaterEqual(time.monotonic() - started, 0.1)
        self.assertIsInstance(ctx.exception, RuntimeError)
        self.assertIn("memory_mb", str(ctx.exception))
        self.assertEqual(wait_stats("b")["wait_count"], 1)

    def test_set_cap_wakes_waiter(self):
        acquire("a", "fd", CAPS["fd"])
        acquired = []

        def worker():
            acquired.append(acquire("b", "fd", 1, timeout=5.0))

        thread = threading.Thread(target=worker)
        thread.start()
        time.sleep(0.1)
        old_cap = CAPS["fd"]
        set_cap("fd", CAPS["fd"] + 10)
        thread.join(5.0)
        self.assertEqual(acquired, [old_cap + 1])
        self.assertEqual(usage()["fd"], old_cap + 1)

    def test_task_isolation(self):
        acquire("a", "thread", 2)
        acquire("b", "thread", 3)
        release("a", "thread", 2)
        self.assertEqual(task_usage("a")["thread"], 0)
        self.assertEqual(task_usage("b")["thread"], 3)
        self.assertEqual(usage()["thread"], 3)

    def test_release_all_on_task_end(self):
        acquire("a", "thread", 2)
        acquire("a", "memory_mb", 10)
        freed = release_all("a")
        self.assertEqual(freed["thread"], 2)
        self.assertEqual(freed["memory_mb"], 10)
        self.assertEqual(freed["fd"], 0)
        self.assertEqual(usage(), {"thread": 0, "fd": 0, "memory_mb": 0})
        self.assertEqual(task_usage("a"), {"thread": 0, "fd": 0, "memory_mb": 0})

    def test_unknown_kind(self):
        with self.assertRaises(KeyError):
            acquire("a", "gpu")
        with self.assertRaises(KeyError):
            release("a", "gpu")
        with self.assertRaises(KeyError):
            set_cap("gpu", 1)

    def test_invalid_amount(self):
        for bad in (0, -1, "1", None):
            with self.assertRaises(ValueError):
                acquire("a", "thread", bad)
            with self.assertRaises(ValueError):
                release("a", "thread", bad)

    def test_request_above_cap_fails_fast(self):
        started = time.monotonic()
        with self.assertRaises(QuotaError):
            acquire("a", "thread", CAPS["thread"] + 1, timeout=30.0)
        self.assertLess(time.monotonic() - started, 1.0)

    def test_invalid_wait_timeout(self):
        with self.assertRaises(ValueError):
            set_wait_timeout(-1)
        with self.assertRaises(ValueError):
            acquire("a", "thread", 1, timeout=-0.5)

    def test_over_release_clamps_to_zero(self):
        acquire("a", "fd", 2)
        self.assertEqual(release("a", "fd", 99), 0)
        self.assertEqual(usage()["fd"], 0)
        self.assertEqual(task_usage("a")["fd"], 0)


class DemoTest(unittest.TestCase):
    def test_demo_output(self):
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "demo.py"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn("配额: thread=2 fd=4 memory_mb=128", out)
        self.assertIn(
            "任务 job-a: 占用 thread=0 fd=0 memory_mb=0, 等待 0.00s (0 次)", out
        )
        self.assertEqual(out.count("(1 次)"), 2)
        self.assertIn("最终全局占用: thread=0 fd=0 memory_mb=0", out)
