import json
import unittest

from taskfair import (
    Scheduler,
    SchedulerError,
    next_task,
    reset,
    restore,
    snapshot,
    submit,
)


class SchedTest(unittest.TestCase):
    def setUp(self):
        reset()

    def test_fifo_across_queues(self):
        submit("big", {"id": 1})
        submit("small", {"id": 2})
        self.assertEqual(next_task()["id"], 1)

    def test_no_weights(self):
        self.assertNotIn("weights", submit("big", {"id": 1}) if isinstance(submit("big", {"id": 1}), dict) else {})

    def test_no_preemption(self):
        submit("big", {"id": 1})
        self.assertNotIn("preempted", next_task())


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def tick(self, seconds):
        self.now += seconds


class WeightedSchedulingTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sched = Scheduler(clock=self.clock)

    def test_weighted_round_robin(self):
        self.sched.configure("big", weight=3)
        self.sched.configure("small", weight=1)
        for index in range(6):
            self.sched.submit("big", {"id": "b%d" % index})
        for index in range(3):
            self.sched.submit("small", {"id": "s%d" % index})

        order = [self.sched.next_task()["id"] for _ in range(9)]
        self.assertEqual(
            order,
            ["b0", "b1", "b2", "s0", "b3", "b4", "b5", "s1", "s2"],
        )

    def test_same_weight_orders_by_wait_time(self):
        self.sched.configure("a", weight=1)
        self.sched.configure("b", weight=1)
        self.sched.submit("a", {"id": "a0"})
        self.clock.tick(2)
        self.sched.submit("b", {"id": "b0"})
        self.clock.tick(3)
        self.sched.submit("b", {"id": "b1"})

        order = [self.sched.next_task()["id"] for _ in range(3)]
        self.assertEqual(order, ["a0", "b0", "b1"])


class GuaranteeTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sched = Scheduler(clock=self.clock)

    def test_low_priority_guarantee_each_round(self):
        self.sched.configure("big", weight=3, guarantee=0)
        self.sched.configure("low", weight=1, guarantee=1)
        for index in range(6):
            self.sched.submit("big", {"id": "b%d" % index})
        for index in range(3):
            self.sched.submit("low", {"id": "l%d" % index})

        order = [self.sched.next_task()["id"] for _ in range(9)]
        self.assertEqual(
            order,
            ["l0", "b0", "b1", "b2", "l1", "b3", "b4", "b5", "l2"],
        )


class PreemptResumeTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sched = Scheduler(clock=self.clock)

    def test_preempt_saves_progress_and_resumes_at_head(self):
        self.sched.configure("low", weight=1, priority=0)
        self.sched.configure("hi", weight=1, priority=10)
        self.sched.submit("low", {"id": "job", "step": 0})
        self.sched.submit("low", {"id": "other"})

        running = self.sched.next_task()
        self.assertEqual(running["id"], "job")
        self.clock.tick(5)

        saved = self.sched.preempt("job", progress={"done": 7})
        self.assertEqual(saved["progress"], {"done": 7})
        self.assertEqual(self.sched.stats()["low"]["preemptions"], 1)

        resumed = self.sched.next_task()
        self.assertEqual(resumed["id"], "job")
        self.assertEqual(resumed["progress"], {"done": 7})

    def test_default_preempt_targets_lowest_priority(self):
        self.sched.configure("low", priority=0)
        self.sched.configure("mid", priority=5)
        self.sched.submit("low", {"id": "l"})
        self.sched.submit("mid", {"id": "m"})
        self.sched.next_task()
        self.sched.next_task()

        saved = self.sched.preempt(progress="checkpoint")
        self.assertEqual(saved["id"], "l")


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sched = Scheduler(clock=self.clock)

    def test_wait_stats_and_dequeue_counts(self):
        self.sched.configure("q", weight=1)
        self.sched.submit("q", {"id": 1})
        self.clock.tick(4)
        self.sched.submit("q", {"id": 2})
        self.sched.next_task()
        self.clock.tick(6)
        self.sched.next_task()

        stats = self.sched.stats()["q"]
        self.assertEqual(stats["dequeued"], 2)
        self.assertEqual(stats["total_wait"], 10.0)
        self.assertEqual(stats["max_wait"], 6.0)
        self.assertEqual(stats["avg_wait"], 5.0)
        self.assertEqual(stats["preemptions"], 0)


class SerializationTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.sched = Scheduler(clock=self.clock)

    def test_restore_queues_after_restart(self):
        self.sched.configure("big", weight=2, guarantee=1, priority=0)
        self.sched.submit("big", {"id": "b0"})
        self.sched.submit("big", {"id": "b1"})
        self.clock.tick(3)
        self.sched.next_task()
        self.sched.preempt("b0", progress="ckpt")

        payload = json.dumps(self.sched.to_dict())
        restarted = Scheduler.from_dict(
            json.loads(payload), clock=self.clock
        )

        task = restarted.next_task()
        self.assertEqual(task["id"], "b0")
        self.assertEqual(task["progress"], "ckpt")
        stats = restarted.stats()["big"]
        self.assertEqual(stats["preemptions"], 1)
        self.assertEqual(stats["dequeued"], 2)

    def test_module_level_snapshot_restore(self):
        reset()
        configure = __import__("taskfair").configure
        configure("q", weight=2)
        submit("q", {"id": "x"})
        data = snapshot()
        reset()
        restore(data)
        self.assertEqual(next_task()["id"], "x")


class ErrorPathTest(unittest.TestCase):
    def setUp(self):
        self.sched = Scheduler()

    def test_invalid_configuration(self):
        with self.assertRaises(SchedulerError):
            self.sched.configure("q", weight=0)
        with self.assertRaises(SchedulerError):
            self.sched.configure("q", guarantee=-1)
        with self.assertRaises(SchedulerError):
            self.sched.configure("", weight=1)

    def test_invalid_submit(self):
        with self.assertRaises(SchedulerError):
            self.sched.submit("q", "not-a-dict")
        with self.assertRaises(SchedulerError):
            self.sched.submit("q", {"no_id": True})

    def test_duplicate_task_id_rejected(self):
        self.sched.configure("q")
        self.sched.submit("q", {"id": "dup"})
        with self.assertRaises(SchedulerError):
            self.sched.submit("q", {"id": "dup"})
        self.sched.next_task()
        with self.assertRaises(SchedulerError):
            self.sched.submit("q", {"id": "dup"})

    def test_preempt_unknown_and_empty(self):
        self.sched.configure("q")
        self.sched.submit("q", {"id": 1})
        with self.assertRaises(SchedulerError):
            self.sched.preempt(999)
        self.sched.next_task()
        with self.assertRaises(SchedulerError):
            self.sched.complete(999)
        self.sched.complete()
        with self.assertRaises(SchedulerError):
            self.sched.preempt()

    def test_corrupt_snapshot(self):
        with self.assertRaises(SchedulerError):
            Scheduler.from_dict({"version": 99})
        with self.assertRaises(SchedulerError):
            Scheduler.from_dict({"version": 1, "seq": 1, "queues": {}})
