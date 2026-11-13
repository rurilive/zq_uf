import threading
import time
import unittest

from dagrun import Task, TaskStatus, run


class ConcurrencyProbe:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def make_task(self, duration=0.05):
        def task():
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(duration)
            with self.lock:
                self.active -= 1
            return duration

        return task


def boom():
    raise RuntimeError("boom")


class DagrunTest(unittest.TestCase):
    def test_runs_all(self):
        dag = {"a": lambda: 1, "b": lambda: 2}
        self.assertEqual(run(dag), {"a": 1, "b": 2})

    def test_order_follows_dict(self):
        seen = []
        dag = {"a": lambda: seen.append("a"), "b": lambda: seen.append("b")}
        run(dag)
        self.assertEqual(seen, ["a", "b"])

    def test_empty_dag(self):
        self.assertEqual(run({}), {})

    def test_independent_tasks_run_in_parallel(self):
        probe = ConcurrencyProbe()
        dag = {name: probe.make_task() for name in ("a", "b", "c")}
        result = run(dag, workers=3)

        self.assertEqual(result, {"a": 0.05, "b": 0.05, "c": 0.05})
        self.assertGreaterEqual(probe.max_active, 2)
        for name in dag:
            self.assertEqual(result.status(name), TaskStatus.SUCCESS)

    def test_dependency_order_respected_under_parallel_workers(self):
        finished = []
        lock = threading.Lock()

        def mark(name):
            def task():
                time.sleep(0.02)
                with lock:
                    finished.append(name)
                return name

            return task

        dag = {
            "a": mark("a"),
            "b": Task(mark("b"), deps=["a"]),
            "c": Task(mark("c"), deps=["a"]),
            "d": Task(mark("d"), deps=["b", "c"]),
        }
        result = run(dag, workers=4)

        self.assertEqual(result, {"a": "a", "b": "b", "c": "c", "d": "d"})
        self.assertEqual(finished.index("a"), 0)
        self.assertLess(finished.index("b"), finished.index("d"))
        self.assertLess(finished.index("c"), finished.index("d"))
        self.assertEqual(result.status("d"), TaskStatus.SUCCESS)

    def test_upstream_failure_marks_downstream_skipped(self):
        dag = {
            "a": lambda: 1,
            "bad": boom,
            "b": Task(lambda: 2, deps=["a"]),
            "c": Task(lambda: 3, deps=["bad"]),
            "d": Task(lambda: 4, deps=["c"]),
        }
        result = run(dag, workers=4)

        self.assertEqual(result, {"a": 1, "b": 2})
        self.assertEqual(result.status("bad"), TaskStatus.FAILED)
        self.assertIsInstance(result.record("bad").error, RuntimeError)
        self.assertEqual(result.status("c"), TaskStatus.SKIPPED)
        self.assertEqual(result.status("d"), TaskStatus.SKIPPED)
        self.assertIn("bad", result.record("c").skip_reason)
        self.assertIn("bad", result.record("d").skip_reason)
        self.assertIsNone(result.record("c").started_at)

    def test_rerun_skips_successful_and_retries_failed_or_skipped(self):
        calls = []

        def once(name, value):
            def task():
                calls.append(name)
                if name == "bad":
                    raise RuntimeError("first run fails")
                return value

            return task

        dag = {
            "a": once("a", 1),
            "bad": once("bad", None),
            "c": Task(once("c", 3), deps=["bad"]),
        }
        first = run(dag, workers=2)
        self.assertEqual(first.status("a"), TaskStatus.SUCCESS)
        self.assertEqual(first.status("bad"), TaskStatus.FAILED)
        self.assertEqual(first.status("c"), TaskStatus.SKIPPED)

        dag["bad"] = lambda: 2
        second = run(dag, workers=2, resume=first)

        self.assertEqual(second, {"a": 1, "bad": 2, "c": 3})
        self.assertEqual(second.status("a"), TaskStatus.SUCCESS)
        self.assertEqual(second.status("bad"), TaskStatus.SUCCESS)
        self.assertEqual(second.status("c"), TaskStatus.SUCCESS)
        self.assertEqual(calls, ["a", "bad", "c"])
        self.assertIs(
            second.record("a"), first.record("a"),
        )

    def test_worker_count_caps_concurrency(self):
        probe = ConcurrencyProbe()
        dag = {name: probe.make_task() for name in ("a", "b", "c", "d")}
        run(dag, workers=2)

        self.assertEqual(probe.max_active, 2)

    def test_task_timeout_is_capped_and_propagates_skip(self):
        dag = {
            "slow": Task(lambda: time.sleep(0.3), timeout=100.0),
            "down": Task(lambda: "never", deps=["slow"]),
        }
        result = run(dag, workers=2, max_timeout=0.05)

        self.assertEqual(result.record("slow").timeout, 0.05)
        self.assertEqual(result.status("slow"), TaskStatus.FAILED)
        self.assertIsInstance(result.record("slow").error, TimeoutError)
        self.assertEqual(result.status("down"), TaskStatus.SKIPPED)
        self.assertIn("slow", result.record("down").skip_reason)

    def test_records_include_start_and_finish_times(self):
        result = run({"a": lambda: 1}, workers=1)
        record = result.record("a")

        self.assertIsNotNone(record.started_at)
        self.assertIsNotNone(record.finished_at)
        self.assertGreaterEqual(record.finished_at, record.started_at)
        self.assertGreaterEqual(record.duration, 0.0)

    def test_missing_dependency_and_cycle_are_rejected(self):
        with self.assertRaises(KeyError):
            run({"a": Task(lambda: 1, deps=["ghost"])})

        with self.assertRaises(ValueError):
            run(
                {
                    "a": Task(lambda: 1, deps=["b"]),
                    "b": Task(lambda: 2, deps=["a"]),
                }
            )
