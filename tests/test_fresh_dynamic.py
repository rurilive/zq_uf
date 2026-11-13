import os
import subprocess
import sys
import unittest

from queuefresh import Queue

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def make_queue(clock, **overrides):
    params = dict(half_life=10.0, boost_threshold=5.0, boost_step=2.0,
                  stale_weight=4.0)
    params.update(overrides)
    return Queue(clock=clock, **params)


class DecayTest(unittest.TestCase):
    def test_fresh_beats_stale_same_effective_priority(self):
        clock = FakeClock()
        q = make_queue(clock)
        q.push({"id": "stale", "priority": 1,
                "published_at": clock.t - 10})  # 一个半衰期前
        q.push({"id": "fresh", "priority": 2, "published_at": clock.t})
        # stale: 1 + 4*(1-0.5) = 3，fresh: 2，新鲜的先出
        self.assertEqual(q.pop()["id"], "fresh")
        self.assertEqual(q.pop()["id"], "stale")

    def test_staleness_grows_with_age(self):
        clock = FakeClock()
        q = make_queue(clock)
        q.push({"id": "a", "priority": 1, "published_at": clock.t})
        clock.advance(10)  # 恰好一个半衰期
        row = q.snapshot()[0]
        self.assertAlmostEqual(row["score"], 1 + 4 * 0.5 - 2 * 2)
        self.assertEqual(row["boosts"], 2)  # 10 // 5


class BoostTest(unittest.TestCase):
    def test_boosts_count_and_score_output(self):
        clock = FakeClock()
        q = make_queue(clock)
        q.push({"id": 1, "priority": 9, "published_at": clock.t})
        clock.advance(12)  # 提权 12 // 5 = 2 次
        task = q.pop()
        self.assertEqual(task["boosts"], 2)
        expected = 9 + 4 * (1 - 2 ** (-12 / 10.0)) - 2 * 2
        self.assertAlmostEqual(task["score"], expected)

    def test_anti_starvation(self):
        clock = FakeClock()
        q = make_queue(clock)
        q.push({"id": "low", "priority": 9, "published_at": clock.t})
        clock.advance(40)  # 提权 8 次，分数 9 + 3.75 - 16 = -3.25
        q.push({"id": "hot", "priority": 1, "published_at": clock.t})
        first = q.pop()
        self.assertEqual(first["id"], "low")
        self.assertEqual(first["boosts"], 8)
        self.assertEqual(q.pop()["id"], "hot")

    def test_no_boost_below_threshold(self):
        clock = FakeClock()
        q = make_queue(clock)
        q.push({"id": 1, "priority": 5, "published_at": clock.t})
        clock.advance(4.9)  # 未达阈值 5
        self.assertEqual(q.pop()["boosts"], 0)


class TieAndExpiryTest(unittest.TestCase):
    def test_exact_tie_falls_back_to_fifo(self):
        clock = FakeClock()
        q = make_queue(clock)
        # a: 1 + 4*(1-0.5) = 3.0，b: 3 + 0 = 3.0，严格同分
        q.push({"id": "a", "priority": 1, "published_at": clock.t - 10})
        q.push({"id": "b", "priority": 3, "published_at": clock.t})
        self.assertEqual([q.pop()["id"], q.pop()["id"]], ["a", "b"])

    def test_expired_task_is_deprioritized_not_dropped(self):
        clock = FakeClock()
        q = make_queue(clock)
        q.push({"id": "expired", "priority": 1,
                "published_at": clock.t, "deadline": clock.t + 5})
        clock.advance(10)  # 已过 deadline
        q.push({"id": "fresh", "priority": 5, "published_at": clock.t})
        self.assertEqual(q.pop()["id"], "fresh")
        self.assertEqual(q.pop()["id"], "expired")
        self.assertIsNone(q.pop())


class ErrorPathTest(unittest.TestCase):
    def test_push_rejects_non_dict(self):
        q = make_queue(FakeClock())
        with self.assertRaises(TypeError):
            q.push("not-a-dict")
        self.assertEqual(q.size(), 0)

    def test_push_rejects_bad_field_types(self):
        q = make_queue(FakeClock())
        for bad in ({"priority": "high"},
                    {"published_at": "昨天"},
                    {"deadline": "明天"},
                    {"priority": True}):
            with self.assertRaises(TypeError, msg=repr(bad)):
                q.push(bad)
        self.assertEqual(q.size(), 0)

    def test_push_rejects_deadline_before_published(self):
        q = make_queue(FakeClock())
        with self.assertRaises(ValueError):
            q.push({"published_at": 100.0, "deadline": 50.0})
        self.assertEqual(q.size(), 0)

    def test_constructor_rejects_bad_params(self):
        with self.assertRaises(ValueError):
            Queue(half_life=0)
        with self.assertRaises(ValueError):
            Queue(boost_threshold=-1)
        with self.assertRaises(ValueError):
            Queue(boost_step=-0.5)
        with self.assertRaises(TypeError):
            Queue(half_life="1h")
        with self.assertRaises(TypeError):
            Queue(clock=123)

    def test_empty_pop_returns_none(self):
        q = make_queue(FakeClock())
        self.assertIsNone(q.pop())


class SnapshotTest(unittest.TestCase):
    def test_snapshot_matches_pop_order(self):
        clock = FakeClock()
        q = make_queue(clock)
        q.push({"id": "x", "priority": 3, "published_at": clock.t})
        q.push({"id": "y", "priority": 1, "published_at": clock.t})
        clock.advance(5)
        rows = q.snapshot()
        self.assertEqual([r["task"]["id"] for r in rows], ["y", "x"])
        self.assertEqual(rows[0]["boosts"], 1)
        self.assertEqual([q.pop()["id"], q.pop()["id"]], ["y", "x"])


class DemoTest(unittest.TestCase):
    def test_demo_output(self):
        result = subprocess.run(
            [sys.executable, "demo.py"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, """# 热点+归档混合（等待45s后出队）
hot-1 0.6216 1
hot-2 1.6216 1
archive-1 10.9257 1
# 防饿死（归档等待220s后新热点入队）
archive-2 -1.315 7
hot-3 1.0 0
""")


if __name__ == "__main__":
    unittest.main()
