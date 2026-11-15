import unittest

import crawlprio2
from crawlprio2 import pop, push


class QueueTest(unittest.TestCase):
    def setUp(self):
        while pop() is not None:
            pass

    def test_fifo(self):
        push({"id": "a"})
        push({"id": "b"})
        self.assertEqual(pop()["id"], "a")

    def test_empty_returns_none(self):
        while pop() is not None:
            pass
        self.assertIsNone(pop())

    def test_priority_ignored(self):
        push({"id": "low", "priority": 1})
        push({"id": "high", "priority": 9})
        self.assertEqual(pop()["id"], "low")


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class ConfiguredTest(unittest.TestCase):
    """新机制用例：每个用例前后都 reset，保证既有断言的默认 FIFO 环境不受影响。"""

    def setUp(self):
        crawlprio2.reset()

    def tearDown(self):
        crawlprio2.reset()

    def test_site_quota_breaks_consecutive_run(self):
        crawlprio2.configure(site_quota=2)
        for i in range(3):
            push({"id": "big-%d" % i, "site": "big"})
        push({"id": "small-0", "site": "small"})
        self.assertEqual(pop()["id"], "big-0")
        self.assertEqual(pop()["id"], "big-1")
        skipped = pop()
        self.assertEqual(skipped["id"], "small-0")
        self.assertEqual(skipped["dequeue"]["reason"], "quota_skip")
        self.assertEqual(pop()["id"], "big-2")

    def test_tenant_quota_counted_separately(self):
        crawlprio2.configure(tenant_quota=2)
        push({"id": "a", "site": "s1", "tenant": "t"})
        push({"id": "b", "site": "s2", "tenant": "t"})
        push({"id": "c", "site": "s3", "tenant": "t"})
        push({"id": "d", "site": "s4", "tenant": "u"})
        self.assertEqual(pop()["id"], "a")
        self.assertEqual(pop()["id"], "b")
        self.assertEqual(pop()["id"], "d")  # 租户 t 连续两次后被跳过
        self.assertEqual(pop()["id"], "c")
        s = crawlprio2.stats()
        self.assertEqual(s["by_site"], {"s1": 1, "s2": 1, "s4": 1, "s3": 1})
        self.assertEqual(s["by_tenant"], {"t": 3, "u": 1})

    def test_aging_boost_lifts_old_task_with_cap(self):
        clock = FakeClock()
        crawlprio2.configure(aging_rate=1.0, aging_cap=5,
                             preempt_threshold=8, preempt_quota=10,
                             clock=clock)
        push({"id": "head", "priority": 1})   # t=1000，有效 1+5=6
        clock.t += 1
        push({"id": "old", "priority": 4})    # t=1001，有效 4+5=9（提权封顶 5）
        clock.t += 9
        item = pop()
        self.assertEqual(item["id"], "old")
        self.assertEqual(item["dequeue"]["reason"], "aging_preempt")
        self.assertEqual(item["dequeue"]["boost"], 5)  # 等待 10s 也只提 5
        self.assertEqual(pop()["id"], "head")

    def test_preempt_consumes_site_quota(self):
        crawlprio2.configure(preempt_threshold=8, preempt_quota=1)
        push({"id": "low", "site": "s", "priority": 1})
        push({"id": "high-1", "site": "s", "priority": 9})
        push({"id": "high-2", "site": "s", "priority": 9})
        first = pop()
        self.assertEqual(first["id"], "high-1")
        self.assertEqual(first["dequeue"]["reason"], "preempt")
        self.assertEqual(pop()["id"], "low")   # 插队配额用尽，回到 FIFO
        self.assertEqual(pop()["id"], "high-2")
        self.assertEqual(crawlprio2.stats()["preempted"], 1)

    def test_tie_keeps_fifo(self):
        crawlprio2.configure(preempt_threshold=5, preempt_quota=10)
        push({"id": "a", "priority": 6})
        push({"id": "b", "priority": 6})
        push({"id": "c", "priority": 6})
        self.assertEqual([pop()["id"], pop()["id"], pop()["id"]],
                         ["a", "b", "c"])

    def test_guarantee_window_serves_low_priority(self):
        crawlprio2.configure(guarantee_window=2, low_priority=1)
        push({"id": "h1", "priority": 5})
        push({"id": "h2", "priority": 5})
        push({"id": "h3", "priority": 5})
        push({"id": "low", "priority": 1})
        self.assertEqual(pop()["id"], "h1")
        self.assertEqual(pop()["id"], "h2")
        g = pop()
        self.assertEqual(g["id"], "low")
        self.assertEqual(g["dequeue"]["reason"], "guarantee")
        self.assertEqual(pop()["id"], "h3")

    def test_timeout_degrades_then_serves(self):
        clock = FakeClock()
        crawlprio2.configure(max_wait=10, timeout_action="degrade", clock=clock)
        push({"id": "stale"})
        clock.t += 20
        push({"id": "fresh"})
        self.assertEqual(pop()["id"], "fresh")
        stale = pop()
        self.assertEqual(stale["id"], "stale")
        self.assertEqual(stale["dequeue"]["reason"], "degraded")
        self.assertEqual(crawlprio2.stats()["degraded"], 1)

    def test_timeout_drop(self):
        clock = FakeClock()
        crawlprio2.configure(max_wait=5, timeout_action="drop", clock=clock)
        push({"id": "gone"})
        clock.t += 10
        self.assertIsNone(pop())
        self.assertEqual(crawlprio2.stats()["dropped"], 1)
        self.assertEqual(crawlprio2.dequeue_log()[0]["reason"], "timeout_drop")

    def test_stats_and_log_record_reasons(self):
        crawlprio2.configure(site_quota=1)
        push({"id": "x", "site": "a"})
        push({"id": "y", "site": "b"})
        pop()
        pop()
        s = crawlprio2.stats()
        self.assertEqual(s["popped"], 2)
        self.assertEqual(s["by_site"], {"a": 1, "b": 1})
        log = crawlprio2.dequeue_log()
        self.assertEqual(len(log), 2)
        self.assertEqual(log[0]["reason"], "fifo")
        self.assertIn("wait", log[0])
        self.assertIn("boost", log[0])


if __name__ == "__main__":
    unittest.main()
