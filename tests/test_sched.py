import unittest

from jobpriority import next_job, submit
from jobpriority import sched


class SchedTest(unittest.TestCase):
    def setUp(self):
        sched._queue.clear()

    def test_fifo(self):
        submit({"id": 1}); submit({"id": 2})
        self.assertEqual(next_job()["id"], 1)

    def test_empty_none(self):
        self.assertIsNone(next_job())

    def test_submit_count(self):
        self.assertEqual(submit({"id": 1}), 1)


class QuotaTest(unittest.TestCase):
    def setUp(self):
        sched.reset()

    def tearDown(self):
        sched.reset()

    def test_quota_skips_big_tenant(self):
        sched.configure(quotas={"big": 1}, window_seconds=60.0)
        submit({"id": "b1", "tenant": "big"})
        submit({"id": "b2", "tenant": "big"})
        submit({"id": "s1", "tenant": "small"})
        self.assertEqual(next_job()["id"], "b1")
        self.assertEqual(next_job()["id"], "s1")
        self.assertIsNone(next_job())  # big 超配额，队列空转

    def test_window_recovers_quota(self):
        now = [1000.0]
        sched.set_clock(lambda: now[0])
        sched.configure(quotas={"big": 1}, window_seconds=10.0)
        submit({"id": "b1", "tenant": "big"})
        self.assertEqual(next_job()["id"], "b1")
        submit({"id": "b2", "tenant": "big"})
        self.assertIsNone(next_job())
        now[0] += 11.0  # 跨过时间窗
        self.assertEqual(next_job()["id"], "b2")

    def test_reclaim_idempotent(self):
        now = [1000.0]
        sched.set_clock(lambda: now[0])
        sched.configure(quotas={"big": 2}, window_seconds=10.0)
        submit({"id": "b1", "tenant": "big"})
        next_job()
        now[0] += 11.0
        self.assertEqual(sched.reclaim("big"), 1)
        self.assertEqual(sched.reclaim("big"), 0)  # 幂等
        self.assertEqual(sched.stats()["quota"]["big"]["used"], 0)


class GuaranteeTest(unittest.TestCase):
    def setUp(self):
        sched.reset()

    def tearDown(self):
        sched.reset()

    def test_low_priority_floor(self):
        sched.configure(capacity=2, guarantees={0: 1})
        for i in range(3):
            submit({"id": "h%d" % i, "priority": 5})
        submit({"id": "l0", "priority": 0})
        first_two = {next_job()["id"], next_job()["id"]}
        self.assertIn("l0", first_two)  # 低优保底不被高优挤没

    def test_floor_not_preempted(self):
        sched.configure(capacity=2, guarantees={0: 1})
        submit({"id": "l0", "priority": 0})
        submit({"id": "h0", "priority": 5})
        next_job(); next_job()
        submit({"id": "h1", "priority": 9})
        self.assertEqual(next_job()["id"], "h1")  # 抢占 h0 而非保底的 l0
        rec = sched.stats()["preemptions"]["records"][0]
        self.assertEqual(rec["job_id"], "h0")


class PreemptResumeTest(unittest.TestCase):
    def setUp(self):
        sched.reset()

    def tearDown(self):
        sched.reset()

    def test_preempt_saves_progress_and_resumes(self):
        sched.configure(capacity=1)
        submit({"id": "low", "priority": 0})
        self.assertEqual(next_job()["id"], "low")
        sched.report_progress("low", 0.6)
        submit({"id": "high", "priority": 9})
        self.assertEqual(next_job()["id"], "high")
        rec = sched.stats()["preemptions"]["records"][0]
        self.assertEqual(rec["progress"], 0.6)  # 进度已保存
        sched.release("high")
        resumed = next_job()
        self.assertEqual(resumed["id"], "low")
        self.assertEqual(resumed["progress"], 0.6)  # 续跑不重跑

    def test_same_priority_fifo(self):
        sched.configure(capacity=1)
        submit({"id": "a", "priority": 3})
        submit({"id": "b", "priority": 3})
        self.assertEqual(next_job()["id"], "a")
        sched.release("a")
        self.assertEqual(next_job()["id"], "b")


class StatsTest(unittest.TestCase):
    def setUp(self):
        sched.reset()

    def tearDown(self):
        sched.reset()

    def test_stats_and_release_idempotent(self):
        sched.configure(capacity=1, quotas={"big": 5})
        submit({"id": "j1", "tenant": "big", "priority": 1})
        next_job()
        s = sched.stats()
        self.assertEqual(s["quota"]["big"]["used"], 1)
        self.assertEqual(s["wait"]["count"], 1)
        self.assertIn(1, s["wait"]["by_priority"])
        self.assertTrue(sched.release("j1"))
        self.assertFalse(sched.release("j1"))  # 幂等，不重复记录
        self.assertEqual(sched.stats()["running"], 0)
        types = [e["type"] for e in sched.events()]
        self.assertEqual(types.count("release"), 1)  # 留档不重复
