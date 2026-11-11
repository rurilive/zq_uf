import unittest

from crawlflow import NetworkError, Scheduler, SitePolicy

WIDE = SitePolicy(max_concurrency=100)


class PriorityTest(unittest.TestCase):
    def test_higher_priority_first(self):
        s = Scheduler(default_policy=WIDE)
        s.add("https://example.com/low", priority=0)
        s.add("https://example.com/high", priority=10)
        s.add("https://example.com/mid", priority=5)
        self.assertEqual(s.next().url, "https://example.com/high")
        self.assertEqual(s.next().url, "https://example.com/mid")
        self.assertEqual(s.next().url, "https://example.com/low")

    def test_same_priority_fifo(self):
        s = Scheduler(default_policy=WIDE)
        for i in range(3):
            s.add("https://example.com/%d" % i, priority=1)
        self.assertEqual(
            [s.next().url for _ in range(3)],
            ["https://example.com/0",
             "https://example.com/1",
             "https://example.com/2"],
        )


class BackoffTest(unittest.TestCase):
    def setUp(self):
        self.s = Scheduler(backoff_base=10.0, backoff_cap=50.0, max_attempts=5)
        self.s.add("https://example.com/a")

    def test_network_error_defers_next_run(self):
        task = self.s.next(now=1000.0)
        self.s.failure(task.url, NetworkError("timeout"), now=1000.0)
        # 退避中不可取
        self.assertIsNone(self.s.next(now=1000.0 + 9.9))
        # 到点可取：base * 2**0 = 10s
        task = self.s.next(now=1000.0 + 10.0)
        self.assertIsNotNone(task)
        self.assertEqual(task.attempts, 1)

    def test_backoff_grows_exponentially(self):
        t = 1000.0
        delays = []
        for _ in range(3):
            task = self.s.next(now=t)
            self.assertIsNotNone(task)
            self.s.failure(task.url, NetworkError("boom"), now=t)
            next_run_at = self.s.conn.execute(
                "SELECT next_run_at FROM tasks"
            ).fetchone()[0]
            delays.append(next_run_at - t)
            t = next_run_at
        self.assertEqual(delays, [10.0, 20.0, 40.0])

    def test_backoff_capped(self):
        s = Scheduler(backoff_base=100.0, backoff_cap=150.0, max_attempts=10)
        s.add("https://example.com/a")
        t = 0.0
        for expected in (100.0, 150.0, 150.0):
            task = s.next(now=t)
            self.assertIsNotNone(task)
            s.failure(task.url, NetworkError("boom"), now=t)
            # 未到退避截止时间不可取
            self.assertIsNone(s.next(now=t + expected - 0.01))
            t += expected
        self.assertIsNotNone(s.next(now=t))

    def test_network_error_exhausts_to_dead(self):
        s = Scheduler(backoff_base=1.0, max_attempts=3)
        s.add("https://example.com/a")
        t = 0.0
        for _ in range(3):
            task = s.next(now=t)
            self.assertIsNotNone(task)
            s.failure(task.url, NetworkError("boom"), now=t)
            t += 10.0
        self.assertIsNone(s.next(now=t + 100.0))
        self.assertEqual(len(s.dead_letters()), 1)
        self.assertEqual(s.dead_letters()[0][2], 3)  # attempts


if __name__ == "__main__":
    unittest.main()
