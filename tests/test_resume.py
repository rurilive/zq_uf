import os
import tempfile
import unittest

from crawlflow import Scheduler


class ResumeTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.unlink(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_running_tasks_requeued_on_restart(self):
        s = Scheduler(self.db_path)
        s.add("https://example.com/a")
        s.add("https://example.com/b")
        task = s.next()
        self.assertIsNotNone(task)  # a 进入 running，模拟进程在此崩溃
        s.close()

        s2 = Scheduler(self.db_path)
        self.assertEqual(s2.pending(), 2)  # running 被重置回 pending
        urls = {s2.next().url, s2.next().url}
        self.assertEqual(urls, {"https://example.com/a", "https://example.com/b"})
        s2.close()

    def test_done_tasks_not_refetched_after_restart(self):
        s = Scheduler(self.db_path)
        s.add("https://example.com/a?utm_source=x&id=1")
        s.success(s.next().url, "body")
        s.close()

        s2 = Scheduler(self.db_path)
        # 同一文章的变体 URL 也不会再次入队
        self.assertFalse(s2.add("https://example.com/a?id=1"))
        self.assertIsNone(s2.next())
        s2.close()

    def test_cursor_persists(self):
        s = Scheduler(self.db_path)
        s.set_cursor("example.com", "2026-11-01T00:00:00")
        s.close()

        s2 = Scheduler(self.db_path)
        self.assertEqual(s2.get_cursor("example.com"), "2026-11-01T00:00:00")
        self.assertEqual(s2.get_cursor("missing", "default"), "default")
        s2.set_cursor("example.com", "2026-11-02T00:00:00")
        self.assertEqual(s2.get_cursor("example.com"), "2026-11-02T00:00:00")
        s2.close()

    def test_attempts_and_backoff_survive_restart(self):
        s = Scheduler(self.db_path, backoff_base=10.0)
        s.add("https://example.com/a")
        from crawlflow import NetworkError
        task = s.next(now=0.0)
        s.failure(task.url, NetworkError("boom"), now=0.0)
        s.close()

        s2 = Scheduler(self.db_path, backoff_base=10.0)
        self.assertIsNone(s2.next(now=5.0))   # 退避状态仍在
        task = s2.next(now=10.0)
        self.assertIsNotNone(task)
        self.assertEqual(task.attempts, 1)
        s2.close()


if __name__ == "__main__":
    unittest.main()
