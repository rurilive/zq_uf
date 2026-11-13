import unittest

from quotasched import Queue


class SchedTest(unittest.TestCase):
    def test_first_come_first_run(self):
        q = Queue()
        q.submit({"id": 1, "cpu": 32, "mem": 64})
        q.submit({"id": 2, "cpu": 1, "mem": 1})
        self.assertEqual(q.next()["id"], 1)

    def test_resource_fields_ignored(self):
        q = Queue()
        q.submit({"id": 1, "cpu": 64})
        self.assertEqual(q.next(), {"id": 1, "cpu": 64})

    def test_no_preemption(self):
        q = Queue()
        q.submit({"id": 1, "cpu": 1})
        self.assertNotIn("preempted", q.next())


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class BinPackingTest(unittest.TestCase):
    def test_small_task_fits_around_big(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "big", "cpu": 6, "mem": 8})
        q.submit({"id": "small", "cpu": 2, "mem": 4})
        self.assertEqual(q.next()["id"], "big")
        self.assertEqual(q.next()["id"], "small")
        self.assertIsNone(q.next())  # 容量已满

    def test_skip_blocked_head(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "big", "cpu": 6, "mem": 8})
        q.submit({"id": "too_big_now", "cpu": 4, "mem": 4})
        q.submit({"id": "small", "cpu": 2, "mem": 4})
        q.next()  # big
        self.assertEqual(q.next()["id"], "small")  # 跳过装不下的 too_big_now
        self.assertIsNone(q.next())

    def test_mem_blocks_even_if_cpu_free(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "mem_hog", "cpu": 1, "mem": 12})
        q.submit({"id": "needs_mem", "cpu": 1, "mem": 8})
        q.next()
        self.assertIsNone(q.next())

    def test_done_frees_resources(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "a", "cpu": 6, "mem": 8})
        q.submit({"id": "b", "cpu": 6, "mem": 8})
        q.next()
        self.assertIsNone(q.next())
        q.done("a")
        self.assertEqual(q.next()["id"], "b")

    def test_exact_fit(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "exact", "cpu": 8, "mem": 16})
        self.assertEqual(q.next()["id"], "exact")
        self.assertIsNone(q.next())

    def test_same_priority_fifo(self):
        clock = FakeClock()
        q = Queue(cpu=4, mem=8, clock=clock)
        q.submit({"id": "first", "cpu": 4, "mem": 8, "priority": 1})
        clock.advance(5)
        q.submit({"id": "second", "cpu": 4, "mem": 8, "priority": 1})
        self.assertEqual(q.next()["id"], "first")
        q.done("first")
        clock.advance(2)
        self.assertEqual(q.next()["id"], "second")
        self.assertEqual(q.stats()["waits"]["second"], [2.0])

    def test_higher_priority_dispatched_first(self):
        q = Queue(cpu=4, mem=8)
        q.submit({"id": "low", "cpu": 4, "mem": 8, "priority": 0})
        q.submit({"id": "high", "cpu": 4, "mem": 8, "priority": 9})
        self.assertEqual(q.next()["id"], "high")


class PreemptionTest(unittest.TestCase):
    def test_high_priority_preempts_low(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "low", "cpu": 8, "mem": 16, "priority": 0})
        q.next()
        q.submit({"id": "urgent", "cpu": 8, "mem": 16, "priority": 9})
        self.assertEqual(q.next()["id"], "urgent")
        self.assertEqual(q.stats()["preemptions"], 1)
        self.assertEqual(q.size(), 1)  # low 回到等待队列

    def test_preemption_saves_progress(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "low", "cpu": 8, "mem": 16, "priority": 0})
        q.next()
        q.report("low", 0.6)
        q.submit({"id": "urgent", "cpu": 8, "mem": 16, "priority": 9})
        q.next()
        q.done("urgent")
        resumed = q.next()
        self.assertEqual(resumed["id"], "low")
        self.assertEqual(resumed["progress"], 0.6)

    def test_same_priority_no_preemption(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "a", "cpu": 8, "mem": 16, "priority": 5})
        q.next()
        q.submit({"id": "b", "cpu": 8, "mem": 16, "priority": 5})
        self.assertIsNone(q.next())
        self.assertEqual(q.stats()["preemptions"], 0)

    def test_no_preemption_when_it_would_not_fit_anyway(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "peer", "cpu": 4, "mem": 8, "priority": 9})
        q.submit({"id": "low", "cpu": 4, "mem": 8, "priority": 0})
        q.next()
        q.next()
        q.submit({"id": "urgent", "cpu": 8, "mem": 8, "priority": 9})
        self.assertIsNone(q.next())  # 只能抢 low，腾出 4 cpu 仍不够 8
        self.assertEqual(q.stats()["preemptions"], 0)

    def test_preemption_picks_lowest_priority_victim(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "p1", "cpu": 4, "mem": 8, "priority": 1})
        q.submit({"id": "p2", "cpu": 4, "mem": 8, "priority": 2})
        q.next()
        q.next()
        q.submit({"id": "urgent", "cpu": 4, "mem": 8, "priority": 9})
        self.assertEqual(q.next()["id"], "urgent")
        waiting_ids = {s["id"] for s in q.snapshot()["waiting"]}
        self.assertEqual(waiting_ids, {"p1"})  # 只抢占优先级更低的 p1


class UtilizationTest(unittest.TestCase):
    def test_utilization(self):
        q = Queue(cpu=8, mem=16)
        q.submit({"id": "a", "cpu": 4, "mem": 8})
        q.next()
        self.assertEqual(q.utilization(), {"cpu": 0.5, "mem": 0.5})
        q.done("a")
        self.assertEqual(q.utilization(), {"cpu": 0.0, "mem": 0.0})

    def test_wait_stats(self):
        clock = FakeClock()
        q = Queue(cpu=4, mem=8, clock=clock)
        q.submit({"id": "a", "cpu": 4, "mem": 8})
        clock.advance(3)
        q.submit({"id": "b", "cpu": 4, "mem": 8})
        clock.advance(2)
        q.next()  # a，等待 5s
        q.done("a")
        clock.advance(1)
        q.next()  # b，等待 3s
        s = q.stats()
        self.assertEqual(s["waits"], {"a": [5.0], "b": [3.0]})
        self.assertEqual(s["avg_wait"], 4.0)
        self.assertEqual(s["max_wait"], 5.0)


class ValidationTest(unittest.TestCase):
    def test_rejects_non_dict(self):
        q = Queue()
        with self.assertRaises(TypeError):
            q.submit(["not", "a", "dict"])

    def test_rejects_missing_id(self):
        q = Queue()
        with self.assertRaises(ValueError):
            q.submit({"cpu": 1})

    def test_rejects_duplicate_id(self):
        q = Queue()
        q.submit({"id": "a", "cpu": 1})
        with self.assertRaises(ValueError):
            q.submit({"id": "a", "cpu": 1})

    def test_rejects_bad_resource_values(self):
        q = Queue()
        for bad in ({"id": 1, "cpu": 0}, {"id": 2, "cpu": -1},
                    {"id": 3, "cpu": "x"}, {"id": 4, "cpu": 1, "mem": -1},
                    {"id": 5, "cpu": True}):
            with self.assertRaises(ValueError):
                q.submit(bad)

    def test_rejects_task_exceeding_capacity(self):
        q = Queue(cpu=4, mem=8)
        with self.assertRaises(ValueError):
            q.submit({"id": "huge", "cpu": 5})
        with self.assertRaises(ValueError):
            q.submit({"id": "fat", "cpu": 1, "mem": 9})

    def test_rejects_bad_priority_and_progress(self):
        q = Queue()
        with self.assertRaises(ValueError):
            q.submit({"id": 1, "priority": 1.5})
        with self.assertRaises(ValueError):
            q.submit({"id": 2, "progress": 1.2})

    def test_report_and_done_errors(self):
        q = Queue()
        with self.assertRaises(ValueError):
            q.report("ghost", 0.5)
        with self.assertRaises(ValueError):
            q.done("ghost")
        q.submit({"id": "a", "cpu": 1})
        q.next()
        with self.assertRaises(ValueError):
            q.report("a", 2.0)


class SnapshotTest(unittest.TestCase):
    def test_snapshot_roundtrip(self):
        import json
        clock = FakeClock()
        q = Queue(cpu=8, mem=16, clock=clock)
        q.submit({"id": "run", "cpu": 4, "mem": 8})
        q.submit({"id": "wait", "cpu": 8, "mem": 16})
        q.next()
        clock.advance(7)
        data = json.loads(json.dumps(q.snapshot()))  # 必须可 JSON 序列化
        q2 = Queue.restore(data, clock=clock)
        self.assertEqual(q2.stats()["running"], 1)
        self.assertEqual(q2.stats()["waiting"], 1)
        self.assertEqual(q2.utilization(), q.utilization())
        q2.done("run")
        clock.advance(1)
        self.assertEqual(q2.next()["id"], "wait")
        self.assertEqual(q2.stats()["waits"]["wait"], [8.0])

    def test_restore_rejects_bad_snapshot(self):
        with self.assertRaises(ValueError):
            Queue.restore({})
        with self.assertRaises(ValueError):
            Queue.restore({"capacity": {"cpu": 4, "mem": 8},
                           "waiting": [{"id": "broken"}]})
