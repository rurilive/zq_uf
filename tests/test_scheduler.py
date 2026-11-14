import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

from crontick import (CronTickError, FileStore, InMemoryStore, LeaseError,
                      ScheduleError, Scheduler)

UTC = timezone.utc
TASKS = {"shop": {"interval": 60, "tz": "UTC", "jitter_window": 0}}


def make(tasks=None, store=None, instance_id="inst-1"):
    return Scheduler(tasks or TASKS, store=store or InMemoryStore(),
                     instance_id=instance_id)


class ConcurrencyTest(unittest.TestCase):
    def test_only_one_instance_fires_per_slot(self):
        store = InMemoryStore()
        scheds = [make(store=store, instance_id=f"inst-{i}") for i in range(2)]
        now = datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
        results, runs = [], []

        def worker(s):
            results.append(s.tick(now, lambda task: runs.append(task)))

        threads = [threading.Thread(target=worker, args=(s,)) for s in scheds]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(runs), 1)
        self.assertEqual(sum(r["ran"] for r in results), 1)
        skipped = [r for r in results if not r["ran"]][0]
        self.assertIn(skipped["reason"], ("lease_held", "already_fired"))

    def test_file_store_excludes_second_instance(self):
        store = FileStore(tempfile.mkdtemp(prefix="crontick-test-"))
        a = make(store=store, instance_id="inst-A")
        b = make(store=store, instance_id="inst-B")
        now = datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
        ra = a.tick(now, lambda task: None)
        rb = b.tick(now, lambda task: None)
        self.assertEqual(ra["ran"] + rb["ran"], 1)
        self.assertEqual((a.tick(now, lambda task: None))["reason"], "already_fired")


class JitterWindowTest(unittest.TestCase):
    def test_offset_within_window_and_deterministic(self):
        tasks = {"shop": {"interval": 60, "tz": "UTC", "jitter_window": 20}}
        s = make(tasks, instance_id="inst-A")
        slot = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        fire1 = s.next_run("shop", slot - timedelta(seconds=1))
        fire2 = s.next_run("shop", slot - timedelta(seconds=1))
        self.assertEqual(fire1, fire2)  # 同实例同 slot 偏移确定
        offset = (fire1 - slot).total_seconds()
        self.assertGreaterEqual(offset, 0)
        self.assertLess(offset, 20)  # 偏移落在窗口内

    def test_tick_waits_for_jitter_then_fires(self):
        tasks = {"shop": {"interval": 60, "tz": "UTC", "jitter_window": 20}}
        s = make(tasks, instance_id="inst-A")
        slot = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        fire = s.next_run("shop", slot - timedelta(seconds=1))
        offset = (fire - slot).total_seconds()
        if offset > 0:
            early = s.tick(fire - timedelta(microseconds=1), lambda task: None)
            self.assertFalse(early["ran"])
            self.assertEqual(early["reason"], "jitter_wait")
        self.assertTrue(s.tick(fire, lambda task: None)["ran"])

    def test_zero_window_fires_on_slot(self):
        s = make()
        slot = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        self.assertEqual(s.next_run("shop", slot - timedelta(seconds=1)), slot)


class CatchupTest(unittest.TestCase):
    def test_missed_slots_fired_once(self):
        s = make()
        t0 = datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
        self.assertTrue(s.tick(t0, lambda task: None)["ran"])
        # 实例“挂掉”3 个间隔后恢复：只补最近一次，且只补一次
        t1 = t0 + timedelta(minutes=3)
        res = s.tick(t1, lambda task: None)
        self.assertTrue(res["ran"])
        self.assertTrue(res["catchup"])
        self.assertEqual(res["reason"], "catchup")
        self.assertEqual(res["missed_slots"], 2)
        self.assertEqual(res["slot"], datetime(2026, 1, 1, 0, 3, 0, tzinfo=UTC))
        again = s.tick(t1, lambda task: None)
        self.assertFalse(again["ran"])
        self.assertEqual(again["reason"], "already_fired")

    def test_normal_gap_is_not_catchup(self):
        s = make()
        t0 = datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
        s.tick(t0, lambda task: None)
        res = s.tick(t0 + timedelta(minutes=1), lambda task: None)
        self.assertTrue(res["ran"])
        self.assertFalse(res["catchup"])
        self.assertEqual(res["missed_slots"], 0)


class TimezoneTest(unittest.TestCase):
    def test_slots_align_to_task_timezone(self):
        tasks = {"shop": {"interval": 86400, "tz": "Asia/Shanghai"}}
        s = make(tasks)
        # UTC 00:00 是上海 08:00，下一个上海零点是 UTC 16:00
        now = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        self.assertEqual(s.next_run("shop", now),
                         datetime(2026, 1, 1, 16, 0, 0, tzinfo=UTC))
        res = s.tick(datetime(2026, 1, 1, 16, 0, 1, tzinfo=UTC),
                     lambda task: None)
        self.assertTrue(res["ran"])
        self.assertEqual(res["slot"], datetime(2026, 1, 1, 16, 0, 0, tzinfo=UTC))

    def test_naive_now_treated_as_utc(self):
        s = make()
        # naive 进 naive 出，按 UTC 对齐到下一分钟
        self.assertEqual(s.next_run("shop", datetime(2026, 1, 1, 0, 0, 0)),
                         datetime(2026, 1, 1, 0, 1, 0))


class ErrorTest(unittest.TestCase):
    def test_unknown_task(self):
        s = make()
        with self.assertRaises(ScheduleError):
            s.tick(datetime(2026, 1, 1, tzinfo=UTC), lambda task: None, task="ghost")
        with self.assertRaises(ScheduleError):
            s.next_run("ghost")

    def test_bad_config(self):
        with self.assertRaises(ScheduleError):
            Scheduler({"x": {"interval": 0}})
        with self.assertRaises(ScheduleError):
            Scheduler({"x": {"interval": 60, "tz": "Mars/Olympus"}})
        with self.assertRaises(ScheduleError):
            Scheduler({"x": {"interval": 60, "jitter_window": -1}})

    def test_run_failure_wrapped(self):
        s = make()

        def boom(task):
            raise RuntimeError("target down")

        with self.assertRaises(CronTickError):
            s.tick(datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC), boom)

    def test_corrupt_state_file_raises_lease_error(self):
        d = tempfile.mkdtemp(prefix="crontick-test-")
        with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        s = make(store=FileStore(d))
        with self.assertRaises(LeaseError):
            s.tick(datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC), lambda task: None)


if __name__ == "__main__":
    unittest.main()
