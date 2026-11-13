import os
import subprocess
import sys
import unittest

from fairqueue import TaskQueue


class FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class FairRotationTest(unittest.TestCase):
    def test_same_weight_round_robin(self):
        q = TaskQueue()
        for task_id, site in (("a1", "a"), ("b1", "b"), ("a2", "a"), ("b2", "b")):
            q.push({"id": task_id, "site": site})
        self.assertEqual([q.next()["id"] for _ in range(4)], ["a1", "b1", "a2", "b2"])

    def test_same_weight_earliest_enqueue_first(self):
        clock = FakeClock()
        q = TaskQueue(clock=clock)
        clock.now = 1
        q.push({"id": "s1", "site": "slow"})
        clock.now = 2
        q.push({"id": "f1", "site": "fast"})
        clock.now = 3
        q.push({"id": "f2", "site": "fast"})
        self.assertEqual(q.next()["id"], "s1")

    def test_weight_ratio(self):
        q = TaskQueue(weights={"big": 2, "small": 1})
        counts = {"big": 0, "small": 0}
        for _ in range(3):
            for site in ("big", "small", "big"):
                counts[site] += 1
                q.push({"id": "%s%d" % (site[0], counts[site]), "site": site})
        order = [q.next()["id"] for _ in range(9)]
        self.assertEqual(order, ["b1", "s1", "b2", "b3", "s2", "b4", "b5", "s3", "b6"])
        stats = q.stats()
        self.assertEqual(stats["big"]["dequeued"], 6)
        self.assertEqual(stats["small"]["dequeued"], 3)

    def test_set_weight_takes_effect(self):
        q = TaskQueue()
        q.set_weight("a", 2)
        q.push({"id": "a1", "site": "a"})
        q.push({"id": "b1", "site": "b"})
        q.push({"id": "a2", "site": "a"})
        self.assertEqual([q.next()["id"] for _ in range(3)], ["a1", "b1", "a2"])


class RetryTest(unittest.TestCase):
    def test_retry_returns_to_own_site_queue(self):
        q = TaskQueue()
        for i in range(1, 6):
            q.push({"id": "b%d" % i, "site": "big"})
        q.push({"id": "s1", "site": "small"})
        self.assertEqual(q.next()["id"], "b1")
        self.assertEqual(q.next()["id"], "s1")
        q.push({"id": "s1", "site": "small", "retry": True})
        self.assertEqual(q.next()["id"], "b2")
        # 重试回到 small 自己的队列，下一轮即出队，而不是排到 b3..b5 之后
        self.assertEqual(q.next()["id"], "s1")
        self.assertEqual(q.stats()["small"]["retries"], 1)


class QuotaTest(unittest.TestCase):
    def test_quota_skips_busy_site(self):
        q = TaskQueue(quota=1)
        slow1 = {"id": 1, "site": "slow"}
        q.push(slow1)
        q.push({"id": 2, "site": "slow"})
        q.push({"id": 3, "site": "fast"})
        self.assertEqual(q.next()["id"], 1)
        self.assertEqual(q.next()["id"], 3)  # slow 在途满，被跳过
        self.assertIsNone(q.next())  # fast 已空，slow 未释放
        q.done(slow1)
        self.assertEqual(q.next()["id"], 2)

    def test_done_unknown_task_raises(self):
        q = TaskQueue(quota=1)
        with self.assertRaises(KeyError):
            q.done({"id": 99, "site": "slow"})

    def test_done_twice_raises(self):
        q = TaskQueue(quota=1)
        task = {"id": 1, "site": "a"}
        q.push(task)
        q.next()
        q.done(task)
        with self.assertRaises(KeyError):
            q.done(task)


class StatsTest(unittest.TestCase):
    def test_wait_stats(self):
        clock = FakeClock()
        q = TaskQueue(clock=clock)
        clock.now = 5
        q.push({"id": 1, "site": "a"})
        clock.now = 8
        q.push({"id": 2, "site": "a"})
        clock.now = 10
        q.next()
        clock.now = 20
        q.next()
        stats = q.stats()["a"]
        self.assertEqual(stats["dequeued"], 2)
        self.assertEqual(stats["total_wait"], 17)
        self.assertEqual(stats["avg_wait"], 8.5)
        self.assertEqual(stats["max_wait"], 12)
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(stats["in_flight"], 2)

    def test_stats_empty_site(self):
        q = TaskQueue()
        q.push({"id": 1, "site": "a"})
        stats = q.stats()["a"]
        self.assertEqual(stats["dequeued"], 0)
        self.assertEqual(stats["avg_wait"], 0.0)
        self.assertEqual(stats["pending"], 1)

    def test_worker_param_accepted(self):
        q = TaskQueue()
        q.push({"id": 1, "site": "a"})
        self.assertEqual(q.next("worker-1")["id"], 1)


class InvalidInputTest(unittest.TestCase):
    def test_invalid_quota(self):
        with self.assertRaises(ValueError):
            TaskQueue(quota=0)
        with self.assertRaises(TypeError):
            TaskQueue(quota="2")
        with self.assertRaises(TypeError):
            TaskQueue(quota=True)

    def test_invalid_weight(self):
        with self.assertRaises(ValueError):
            TaskQueue(weights={"a": 0})
        with self.assertRaises(ValueError):
            TaskQueue(weights={"a": -1})
        with self.assertRaises(TypeError):
            TaskQueue(weights={"a": "2"})
        q = TaskQueue()
        with self.assertRaises(ValueError):
            q.set_weight("a", 0)
        with self.assertRaises(TypeError):
            q.set_weight("a", None)

    def test_invalid_task(self):
        q = TaskQueue()
        with self.assertRaises(TypeError):
            q.push("not-a-dict")
        with self.assertRaises(ValueError):
            q.push({"id": 1, "site": ""})
        with self.assertRaises(TypeError):
            q.push({"id": 1, "site": 123})


class DemoOutputTest(unittest.TestCase):
    def test_demo_output_matches(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            [sys.executable, "demo.py"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("b1 s1 b2 b3 s2 b4 b5 s3 b6", result.stdout)
        self.assertIn("big: 出队 6, 平均等待 9.0", result.stdout)
        self.assertIn("small: 出队 3, 平均等待 9.0", result.stdout)
        self.assertIn("slow1 fast1 None fast2 None slow2", result.stdout)


if __name__ == "__main__":
    unittest.main()
